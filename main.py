import os
import asyncio
from contextlib import asynccontextmanager
import inspect
import traceback
import uuid
from cachetools import TTLCache
import discord
from discord.ext import commands
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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import WEBHOOK_VERIFY_TOKEN
from database import AsyncSessionLocal, get_db
from models import MessageLog, Utilisateur
# À remplacer par ton futur service IA pour L'Avenir en commun
from services.gemini_service import generate_aec_response
from services.storage_service import upload_bytes_to_storage
from services.telegram_service import (
    download_telegram_file,
    send_telegram_text,
)
from services.user_service import (
    LIMITE_MESSAGES_JOUR,
    get_or_create_user,
    verifier_quota_journalier,
)
from services.whatsapp_service import (
    download_media_bytes,
    mark_message_as_read,
    # send_audio_message,
    send_text_message,
)

# ==========================================================
# CONFIGURATION BOT DISCORD (Prêt pour étape suivante)
# ==========================================================
intents = discord.Intents.default()
intents.message_content = True
discord_bot = commands.Bot(command_prefix="!", intents=intents)

@discord_bot.event
async def on_ready():
    print(f"🤖 Bot Discord connecté en tant que : {discord_bot.user}", flush=True)


@discord_bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = (
        discord_bot.user in message.mentions if discord_bot.user else False
    )

    if not is_dm and not is_mentioned:
        return

    if not is_dm and is_mentioned:
        try:
            dm_channel = message.author.dm_channel or await message.author.create_dm()
            question = message.clean_content
            if discord_bot.user:
                question = question.replace(f"@{discord_bot.user.name}", "").strip()

            if question:
                await dm_channel.send(
                    f"Salut {message.author.display_name} ! 👋 Tu m'as demandé dans le salon :\n"
                    f"> *« {question} »*\n\n"
                    "On en discute tranquillement ici, dis-moi tout !"
                )
            else:
                await dm_channel.send(
                    f"Salut {message.author.display_name} ! 👋 Je suis ChatAEC. "
                    "Quelle mesure ou quel thème du programme *L'Avenir en commun* t'intéresse ?"
                )

            await message.reply(
                f"Salut {message.author.mention} ! Je viens de t'envoyer un message privé pour qu'on échange ensemble. 🚀"
            )
        except discord.Forbidden:
            await message.reply(
                f"Aïe {message.author.mention}, je ne peux pas t'envoyer de message privé ! "
                "Pense à autoriser les MP pour ce serveur dans tes paramètres de confidentialité. 😉"
            )
        return

    discord_id = f"dc_{message.author.id}"
    prenom_discord = message.author.display_name or "Citoyen"

    async with AsyncSessionLocal() as session_db:
        stmt = select(Utilisateur).where(Utilisateur.whatsapp_id == discord_id)
        result = await session_db.execute(stmt)
        utilisateur = result.scalar_one_or_none()

        if not utilisateur:
            utilisateur = Utilisateur(
                whatsapp_id=discord_id,
                prenom_eleve=prenom_discord,
                pays="France",
                dashboard_token=uuid.uuid4().hex,
                etape_onboarding="TERMINE",
            )
            session_db.add(utilisateur)
            await session_db.commit()
            await session_db.refresh(utilisateur)

        async with message.channel.typing():
            autorise, _ = await verifier_quota_journalier(utilisateur.id_utilisateur, session_db)
            if not autorise:
                limite_msg = (
                    f"Merci pour ces échanges aujourd'hui {utilisateur.prenom_eleve or ''} ! ✊\n\n"
                    f"Tu as atteint la limite quotidienne ({LIMITE_MESSAGES_JOUR} messages).\n"
                    "On reprend la discussion demain !"
                )
                await message.reply(limite_msg)
                return

            user_content = message.content or ""
            if discord_bot.user:
                user_content = user_content.replace(f"@{discord_bot.user.name}", "").strip()

            media_type = "texte"
            media_bytes = None

            if message.attachments:
                attachment = message.attachments[0]
                content_type = attachment.content_type or ""

                if "image" in content_type:
                    media_type = "image"
                    media_bytes = await attachment.read()
                    user_content = user_content or "[Image envoyée par l'utilisateur]"
                elif "audio" in content_type or "ogg" in content_type:
                    media_type = "audio"
                    media_bytes = await attachment.read()
                    user_content = "[Message vocal envoyé par l'utilisateur]"

            # Appel IA ChatAEC (À connecter)
            bot_reply, reply_as_audio, total_p_tokens, total_c_tokens = await generate_aec_response(
                utilisateur=utilisateur,
                user_input=media_bytes if media_bytes else user_content,
                session_db=session_db,
                media_type=media_type,
            )
            bot_reply = "Réponse test de ChatAEC."
            reply_as_audio = False
            total_p_tokens = 0
            total_c_tokens = 0

            session_db.add(
                MessageLog(
                    id_utilisateur=utilisateur.id_utilisateur,
                    role="user",
                    contenu=user_content,
                    tokens_entree=total_p_tokens,
                    tokens_sortie=total_c_tokens,
                    type_media=media_type,
                )
            )
            session_db.add(
                MessageLog(
                    id_utilisateur=utilisateur.id_utilisateur,
                    role="assistant",
                    contenu=bot_reply,
                    type_media="audio" if reply_as_audio else "texte",
                )
            )
            await session_db.commit()

            if reply_as_audio:
                tmp_audio_path = None
                try:
                    tmp_audio_path = await generer_audio_discord(bot_reply)
                    audio_file = discord.File(tmp_audio_path, filename="vocal_chataec.ogg")
                    await message.reply(content=bot_reply, file=audio_file)
                except Exception as err_audio:
                    print(f"⚠️ Erreur génération vocal Discord : {err_audio}", flush=True)
                    await message.reply(bot_reply)
                finally:
                    if tmp_audio_path and os.path.exists(tmp_audio_path):
                        os.remove(tmp_audio_path)
            else:
                if len(bot_reply) > 1950:
                    for chunk in [bot_reply[i : i + 1950] for i in range(0, len(bot_reply), 1950)]:
                        await message.reply(chunk)
                else:
                    await message.reply(bot_reply)


# ==========================================================
# GESTION DU CYCLE DE VIE (LIFESPAN)
# ==========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.getenv("DISCORD_BOT_TOKEN")
    discord_task = None

    if token:
        discord_task = asyncio.create_task(discord_bot.start(token))
        print("🚀 Démarrage du client Discord...", flush=True)
    else:
        print("ℹ️ DISCORD_BOT_TOKEN non configuré (canal en veille).", flush=True)

    yield

    if token and not discord_bot.is_closed():
        await discord_bot.close()
        if discord_task:
            await discord_task
        print("🛑 Arrêt propre du client Discord.", flush=True)


# ==========================================================
# INITIALISATION FASTAPI
# ==========================================================
app = FastAPI(
    title="ChatAEC - Assistant L'Avenir en commun",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
PROCESSED_MESSAGES = TTLCache(maxsize=2000, ttl=300)
ACTIVE_USERS = set()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/confidentialite")
def privacy_policy():
    return {"message": "Politique de confidentialité de ChatAEC..."}


# ==========================================================
# TRAITEMENT DES MESSAGES WHATSAPP (Prêt pour étape suivante)
# ==========================================================
async def process_whatsapp_message(msg: dict):
    sender_phone = msg.get("from")
    msg_id = msg.get("id")

    if msg_id:
        mark_message_as_read(msg_id)

    try:
        msg_type = msg.get("type")

        async with AsyncSessionLocal() as session:
            utilisateur = await get_or_create_user(sender_phone, session)
            print(f"👤 Message WhatsApp de {utilisateur.prenom_eleve or 'Nouveau'} ({sender_phone})")

            user_content = ""
            media_type = "texte"
            media_url = None
            media_bytes = None

            if msg_type == "text":
                user_content = msg["text"]["body"]
                media_type = "texte"

            elif msg_type in ["audio", "voice"]:
                media_type = "audio"
                media_id = msg[msg_type]["id"]
                media_bytes = download_media_bytes(media_id)
                try:
                    media_url = upload_bytes_to_storage(media_bytes, "ogg", "audio/ogg")
                except Exception as err:
                    print(f"⚠️ Erreur Storage audio : {err}")
                user_content = "[Message vocal envoyé par l'utilisateur]"

            elif msg_type == "image":
                media_type = "image"
                media_id = msg["image"]["id"]
                media_bytes = download_media_bytes(media_id)
                try:
                    media_url = upload_bytes_to_storage(media_bytes, "jpg", "image/jpeg")
                except Exception as err:
                    print(f"⚠️ Erreur Storage image : {err}")
                user_content = "[Image envoyée par l'utilisateur]"

            else:
                return

            # Appel IA ChatAEC (À connecter)
            bot_reply, reply_as_audio, total_p_tokens, total_c_tokens = await generate_aec_response(
                utilisateur=utilisateur,
                user_input=media_bytes if media_bytes else user_content,
                session_db=session,
                media_type=media_type,
            )
            bot_reply = "Réponse test de ChatAEC."
            reply_as_audio = False
            total_p_tokens = 0
            total_c_tokens = 0

            session.add(
                MessageLog(
                    id_utilisateur=utilisateur.id_utilisateur,
                    role="user",
                    contenu=user_content,
                    tokens_entree=total_p_tokens,
                    tokens_sortie=total_c_tokens,
                    type_media=media_type,
                    media_url=media_url,
                )
            )
            session.add(
                MessageLog(
                    id_utilisateur=utilisateur.id_utilisateur,
                    role="assistant",
                    contenu=bot_reply,
                    type_media="audio" if reply_as_audio else "texte",
                    media_url=None,
                )
            )
            await session.commit()

        if reply_as_audio:
            fn = send_audio_message(sender_phone, bot_reply)
            if inspect.iscoroutinefunction(send_audio_message):
                await fn
        else:
            fn = send_text_message(sender_phone, bot_reply)
            if inspect.iscoroutinefunction(send_text_message):
                await fn

    except Exception as e:
        print(f"❌ Erreur process_whatsapp_message : {e}")
        traceback.print_exc()
    finally:
        ACTIVE_USERS.discard(sender_phone)


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
            return Response(content=hub_challenge, media_type="text/plain")
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

        if msg_id in PROCESSED_MESSAGES or sender_phone in ACTIVE_USERS:
            return Response(status_code=status.HTTP_200_OK)

        PROCESSED_MESSAGES[msg_id] = True
        ACTIVE_USERS.add(sender_phone)

        background_tasks.add_task(process_whatsapp_message, msg)
        return Response(status_code=status.HTTP_200_OK)

    except Exception as e:
        print(f"❌ Erreur webhook : {e}")
        return Response(status_code=status.HTTP_200_OK)


# ==========================================================
# ROUTE TELEGRAM WEBHOOK (Canal actif principal)
# ==========================================================
@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request, session_db: AsyncSession = Depends(get_db)
):
    data = await request.json()

    message = data.get("message")
    if not message:
        return {"status": "ignored"}

    message_texte = message.get("text") or message.get("caption") or ""
    chat_id = message["chat"]["id"]

    chat_type = message.get("chat", {}).get("type", "private")
    is_group = chat_type in ["group", "supergroup"]

    user_from = message.get("from", {})
    user_id = user_from.get("id")
    prenom_telegram = user_from.get("first_name", "Citoyen")

    if not chat_id or not user_id:
        return {"status": "ignored"}

    BOT_USERNAME = "@ChatAEC_bot"  # Remplace par le @ exact de ton bot Telegram

    if is_group:
        if BOT_USERNAME.lower() not in message_texte.lower():
            return {"status": "ignored"}

        message_texte = (
            message_texte.replace(BOT_USERNAME, "")
            .replace(BOT_USERNAME.lower(), "")
            .strip()
        )

        if not message_texte and not (message.get("photo") or message.get("voice")):
            await send_telegram_text(
                chat_id,
                f"Salut {prenom_telegram} ! Pose-moi ta question sur le programme avec mon tag, ou viens discuter en MP 👇",
                show_mp_button=True,
            )
            return {"status": "ok"}

    # Récupération ou création du profil utilisateur
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

    # Commande /start
    if message_texte.startswith("/start"):
        await send_telegram_text(
            chat_id,
            f"Bonjour {prenom_telegram} ! 👋 Je suis ChatAEC, ton assistant pour explorer le programme *L'Avenir en commun*.\n"
            "Pose-moi une question sur une mesure, un thème ou une proposition pour commencer !",
        )
        return {"status": "ok"}

    # Vérification du quota journalier
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

    user_content = ""
    media_type = "texte"
    media_bytes = None

    if "photo" in message:
        media_type = "image"
        file_id = message["photo"][-1]["file_id"]
        media_bytes = await download_telegram_file(file_id)
        user_content = message.get("caption") or "[Image envoyée par l'utilisateur]"

        if not media_bytes:
            await send_telegram_text(
                chat_id, "Je n'ai pas réussi à charger ta photo. Réessaie !"
            )
            return {"status": "ok"}

    elif "voice" in message or "audio" in message:
        media_type = "audio"
        audio_obj = message.get("voice") or message.get("audio")
        media_bytes = await download_telegram_file(audio_obj["file_id"])
        user_content = "[Message vocal envoyé par l'utilisateur]"

    elif "text" in message:
        user_content = message_texte
        media_type = "texte"

    else:
        return {"status": "ignored"}

    # Appel IA ChatAEC (À connecter)
    bot_reply, reply_as_audio, total_p_tokens, total_c_tokens = await generate_aec_response(
        utilisateur=utilisateur,
        user_input=media_bytes if media_bytes else user_content,
        session_db=session_db,
        media_type=media_type,
        is_group=is_group,
    )
    bot_reply = "Réponse test de ChatAEC."
    reply_as_audio = False
    total_p_tokens = 0
    total_c_tokens = 0

    # Historique DB
    session_db.add(
        MessageLog(
            id_utilisateur=utilisateur.id_utilisateur,
            role="user",
            contenu=user_content,
            tokens_entree=total_p_tokens,
            tokens_sortie=total_c_tokens,
            type_media=media_type,
        )
    )
    session_db.add(
        MessageLog(
            id_utilisateur=utilisateur.id_utilisateur,
            role="assistant",
            contenu=bot_reply,
            type_media="audio" if reply_as_audio else "texte",
        )
    )
    await session_db.commit()

    if reply_as_audio:
        await send_telegram_audio(chat_id, bot_reply, show_mp_button=is_group)
    else:
        await send_telegram_text(chat_id, bot_reply, show_mp_button=is_group)

    return {"status": "ok"}


# ==========================================================
# SITE INTERNET
# ==========================================================
@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")