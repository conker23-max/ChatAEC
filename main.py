from contextlib import asynccontextmanager
import inspect
from pathlib import Path
import traceback
import uuid

from cachetools import TTLCache
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse
import markdown
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import WEBHOOK_VERIFY_TOKEN
from database import AsyncSessionLocal, get_db
from models import MessageLog, Utilisateur
from services.gemini_service import generate_aec_response
from services.telegram_service import send_telegram_text
from services.user_service import (
    LIMITE_MESSAGES_JOUR,
    get_or_create_user,
    verifier_quota_journalier,
)
from services.whatsapp_service import (
    mark_message_as_read,
    send_text_message,
)

BOT_USERNAME = "@ChatAEC2027_bot"
PRIVACY_MD_PATH = Path("./pages/confidentialite.md")

PROCESSED_MESSAGES = TTLCache(maxsize=2000, ttl=300)
PROCESSING_USERS = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Démarrage de ChatAEC (mode texte)...", flush=True)
    yield
    print("🛑 Arrêt de ChatAEC...", flush=True)


app = FastAPI(
    title="ChatAEC - Assistant L'Avenir en commun",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/confidentialite", response_class=HTMLResponse)
def privacy_policy():
    if not PRIVACY_MD_PATH.exists():
        raise HTTPException(
            status_code=404, detail="Page de confidentialité introuvable."
        )

    md_content = PRIVACY_MD_PATH.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_content,
        extensions=["extra", "sane_lists", "nl2br"],
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Politique de confidentialité et RGPD — ChatAEC</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      max-width: 780px;
      margin: 40px auto;
      padding: 0 20px;
      line-height: 1.65;
      color: #1f2937;
    }}
    h1, h2, h3 {{
      color: #111827;
      margin-top: 1.6em;
    }}
    code {{
      background: #f3f4f6;
      padding: 2px 5px;
      border-radius: 4px;
    }}
  </style>
</head>
<body>
  {html_body}
</body>
</html>"""


# ==========================================================
# TRAITEMENT DES MESSAGES WHATSAPP (TEXTE UNIQUEMENT)
# ==========================================================
async def process_whatsapp_message(msg: dict):
    sender_phone = msg.get("from")
    msg_id = msg.get("id")

    if msg_id:
        mark_message_as_read(msg_id)

    try:
        if msg.get("type") != "text":
            if sender_phone:
                fn = send_text_message(
                    sender_phone,
                    "Pour le moment, ChatAEC ne traite que les messages texte. Écris-moi ta question !",
                )
                if inspect.iscoroutinefunction(send_text_message):
                    await fn
            return

        user_content = msg.get("text", {}).get("body", "").strip()
        if not user_content:
            return

        async with AsyncSessionLocal() as session:
            utilisateur = await get_or_create_user(sender_phone, session)
            print(
                f"👤 Message WhatsApp de {utilisateur.prenom_eleve or 'Nouveau'} ({sender_phone})"
            )

            (
                bot_reply,
                _,
                total_p_tokens,
                total_c_tokens,
                _,
            ) = await generate_aec_response(
                utilisateur=utilisateur,
                user_input=user_content,
                session_db=session,
                media_type="texte",
            )

            session.add(
                MessageLog(
                    id_utilisateur=utilisateur.id_utilisateur,
                    role="user",
                    contenu=user_content,
                    tokens_entree=total_p_tokens,
                    tokens_sortie=total_c_tokens,
                    type_media="texte",
                )
            )
            session.add(
                MessageLog(
                    id_utilisateur=utilisateur.id_utilisateur,
                    role="assistant",
                    contenu=bot_reply,
                    type_media="texte",
                )
            )
            await session.commit()

        if bot_reply and sender_phone:
            fn = send_text_message(sender_phone, bot_reply)
            if inspect.iscoroutinefunction(send_text_message):
                await fn

    except Exception as e:
        print(f"❌ Erreur process_whatsapp_message : {e}")
        traceback.print_exc()


async def run_process_and_unlock(msg: dict, sender_phone: str | None):
    """Exécute le traitement et libère le verrou anti-spam quoi qu'il arrive."""
    try:
        await process_whatsapp_message(msg)
    finally:
        if sender_phone:
            PROCESSING_USERS.discard(sender_phone)


# ==========================================================
# ROUTES WEBHOOK META / WHATSAPP
# ==========================================================
@app.get("/webhook")
@app.head("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe":
        if hub_token == WEBHOOK_VERIFY_TOKEN:
            return Response(content=hub_challenge or "", media_type="text/plain")
        raise HTTPException(status_code=403, detail="Invalid verification token")

    return {"status": "webhook endpoint active"}


@app.post("/webhook")
async def handle_whatsapp_webhook(
    request: Request, background_tasks: BackgroundTasks
):
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=status.HTTP_200_OK)

    try:
        entry = payload.get("entry", [])
        if not entry:
            return Response(status_code=status.HTTP_200_OK)

        changes = entry[0].get("changes", [])
        if not changes:
            return Response(status_code=status.HTTP_200_OK)

        value = changes[0].get("value", {})
        if "statuses" in value:
            return Response(status_code=status.HTTP_200_OK)

        messages = value.get("messages")
        if not messages:
            return Response(status_code=status.HTTP_200_OK)

        msg = messages[0]
        msg_id = msg.get("id")
        sender_phone = msg.get("from")

        if msg_id and msg_id in PROCESSED_MESSAGES:
            return Response(status_code=status.HTTP_200_OK)
        if msg_id:
            PROCESSED_MESSAGES[msg_id] = True

        if sender_phone and sender_phone in PROCESSING_USERS:
            return Response(status_code=status.HTTP_200_OK)

        if sender_phone:
            PROCESSING_USERS.add(sender_phone)

        background_tasks.add_task(run_process_and_unlock, msg, sender_phone)
        return Response(status_code=status.HTTP_200_OK)

    except Exception as e:
        print(f"❌ Erreur webhook : {e}")
        return Response(status_code=status.HTTP_200_OK)


# ==========================================================
# ROUTE TELEGRAM WEBHOOK (TEXTE UNIQUEMENT)
# ==========================================================
@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request, session_db: AsyncSession = Depends(get_db)
):
    data = await request.json()

    message = data.get("message")
    if not message:
        return {"status": "ignored"}

    message_texte = (message.get("text") or "").strip()
    chat_id = message.get("chat", {}).get("id")

    chat_type = message.get("chat", {}).get("type", "private")
    is_group = chat_type in ["group", "supergroup"]

    user_from = message.get("from", {})
    user_id = user_from.get("id")
    prenom_telegram = user_from.get("first_name", "Citoyen")

    if not chat_id or not user_id:
        return {"status": "ignored"}

    if is_group:
        if BOT_USERNAME.lower() not in message_texte.lower():
            return {"status": "ignored"}

        message_texte = (
            message_texte.replace(BOT_USERNAME, "")
            .replace(BOT_USERNAME.lower(), "")
            .strip()
        )

        if not message_texte:
            await send_telegram_text(
                chat_id,
                f"Salut {prenom_telegram} ! Pose-moi ta question sur le programme avec mon tag, ou viens discuter en MP 👇",
                show_mp_button=True,
            )
            return {"status": "ok"}

    if "text" not in message:
        await send_telegram_text(
            chat_id,
            "Pour le moment, ChatAEC ne répond qu'aux messages texte. Écris-moi ta question !",
            show_mp_button=is_group,
        )
        return {"status": "ok"}

    if not message_texte:
        return {"status": "ignored"}

    stmt = select(Utilisateur).where(Utilisateur.whatsapp_id == f"tg_{user_id}")
    result = await session_db.execute(stmt)
    utilisateur = result.scalar_one_or_none()

    if not utilisateur:
        utilisateur = Utilisateur(
            whatsapp_id=f"tg_{user_id}",
            prenom_eleve=prenom_telegram or None,
            pays="France",
            dashboard_token=uuid.uuid4().hex,
            etape_onboarding="TERMINE",
        )
        session_db.add(utilisateur)
        await session_db.commit()
        await session_db.refresh(utilisateur)

    if message_texte.startswith("/start"):
        await send_telegram_text(
            chat_id,
            f"Bonjour {prenom_telegram} ! 👋 Je suis ChatAEC, ton assistant pour explorer le programme *L'Avenir en commun* ainsi que les plans thématiques de la France insoumise.\n"
            "Pose-moi une question en texte sur une mesure, un thème ou une proposition pour commencer !\n"
            "🔒 Politique de confidentialité : https://chataec.onrender.com/confidentialite\n\n"
            "_Outil militant non officiel_",

        )
        return {"status": "ok"}

    autorise, _ = await verifier_quota_journalier(
        utilisateur.id_utilisateur, session_db
    )
    if not autorise:
        message_limite = (
            f"Merci pour ces échanges aujourd'hui {utilisateur.prenom_eleve or ''} ! ✊\n\n"
            f"Tu as atteint la limite quotidienne ({LIMITE_MESSAGES_JOUR} messages).\n"
            "On reprend la discussion demain !"
        )
        await send_telegram_text(chat_id, message_limite, show_mp_button=is_group)
        return {"status": "quota_reached"}

    (
        bot_reply,
        _,
        total_p_tokens,
        total_c_tokens,
        _,
    ) = await generate_aec_response(
        utilisateur=utilisateur,
        user_input=message_texte,
        session_db=session_db,
        media_type="texte",
        is_group=is_group,
    )

    session_db.add(
        MessageLog(
            id_utilisateur=utilisateur.id_utilisateur,
            role="user",
            contenu=message_texte,
            tokens_entree=total_p_tokens,
            tokens_sortie=total_c_tokens,
            type_media="texte",
        )
    )
    session_db.add(
        MessageLog(
            id_utilisateur=utilisateur.id_utilisateur,
            role="assistant",
            contenu=bot_reply,
            type_media="texte",
        )
    )
    await session_db.commit()
    await send_telegram_text(chat_id, bot_reply, show_mp_button=is_group)

    return {"status": "ok"}


# ==========================================================
# SITE INTERNET
# ==========================================================
@app.get("/")
async def landing_page():
    return {"status": "ChatAEC en ligne"}