from datetime import datetime, timedelta, timezone
import re
import warnings
from google import genai
from google.genai import types
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import GEMINI_API_KEY
from models import MessageLog, Utilisateur
from services.rag_service import rechercher_passages_programme

warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3.5-flash-lite"


# =====================================================================
# 1. PROMPT SYSTÈME CHATAEC
# =====================================================================
def generer_system_instruction(
    utilisateur: Utilisateur,
    extraits_programme: str = "",
) -> str:
    prenom = getattr(utilisateur, "prenom_eleve", None) or "citoyen"
    date_du_jour = datetime.now().strftime("%d/%m/%Y")

    bloc_rag = ""
    if extraits_programme:
        bloc_rag = f"""
=======================================================
EXTRAITS DU PROGRAMME « L'AVENIR EN COMMUN » (SOURCE DE RÉFÉRENCE)
=======================================================
Base ta réponse en priorité sur ces extraits officiels :
{extraits_programme}
=======================================================
"""

    return f"""Tu es ChatAEC, l'assistant conversationnel dédié au programme « L'Avenir en commun » de la France insoumise.

CONTEXTE :
- Date du jour : {date_du_jour}
- Interlocuteur : {prenom}
{bloc_rag}
RÈGLES DE CONDUITE ET DE TON :
1. FIDÉLITÉ STRICTE AU CORPUS :
   - Base ta réponse EXCLUSIVEMENT sur les extraits fournis ci-dessus. N'utilise jamais tes connaissances générales externes, même si tu connais le sujet.
   - Si les extraits fournis ne contiennent pas la réponse précise à la question, réponds explicitement : « Je n'ai pas trouvé cette précision dans les extraits du programme et des plans à ma disposition. »
   - Ne déduis aucun chiffre ni calendrier qui ne soit pas écrit noir sur blanc dans les extraits.

2. CLARTÉ & PÉDAGOGIE :
   - Adopte un ton accessible, engagé, respectueux et pédagogue.
   - Va droit au but : structure tes réponses avec des listes à puces courtes lorsque tu présentes plusieurs mesures.
   - Termine souvent par une ouverture courte pour proposer d'approfondir un point ou un thème connexe.

3. FORMAT MESSAGERIE (TELEGRAM / WHATSAPP) :
   - Pas de titres Markdown (`#`, `##`, `###`).
   - Utilise uniquement le double astérisque (`**mot**`) pour mettre en gras les notions clés et les mesures principales. N'utilise jamais d'astérisque simple (`*mot*`).
   - Utilise des tirets (`- `) ou des puces (`• `) pour les listes.
   - Reste concis (idéalement moins de 1 200 caractères par réponse pour rester agréable à lire sur mobile).
"""


# =====================================================================
# 2. HISTORIQUE CONVERSATIONNEL (Fenêtre glissante)
# =====================================================================
async def reformuler_requete_rag(
    question_actuelle: str,
    historique: list[types.Content],
) -> str:
    """Si la question est une relance courte ('et le coût ?', 'pourquoi ?'),
    la rend autonome grâce à l'historique pour que le RAG trouve les bons passages."""
    if not historique:
        return question_actuelle

    # On prend les 4 derniers messages pour le contexte immédiat
    contexte_recent = historique[-4:]
    prompt_reformulation = (
        "Réécris la dernière question de l'utilisateur pour qu'elle soit autonome et claire "
        "pour un moteur de recherche sur le programme politique « L'Avenir en commun », "
        "en intégrant le sujet de la conversation précédente si nécessaire. "
        "Réponds UNIQUEMENT par la question reformulée, en une phrase courte."
    )

    resp = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[
            *contexte_recent,
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=f"{prompt_reformulation}\n\nQuestion : {question_actuelle}")],
            ),
        ],
    )
    return (resp.text or question_actuelle).strip()

async def charger_historique_recent(
    id_utilisateur: int,
    session_db: AsyncSession,
    limite: int = 12,
    timeout_heures: int = 12,
) -> list[types.Content]:
    """Charge les N derniers messages de l'utilisateur depuis Supabase."""
    query = (
        select(MessageLog)
        .where(MessageLog.id_utilisateur == id_utilisateur)
        .order_by(desc(MessageLog.cree_le))
        .limit(limite)
    )
    result = await session_db.execute(query)
    messages_db = result.scalars().all()

    if not messages_db:
        return []

    dernier_msg = messages_db[0]
    maintenant = datetime.now(timezone.utc)
    date_msg = (
        dernier_msg.cree_le
        if dernier_msg.cree_le.tzinfo
        else dernier_msg.cree_le.replace(tzinfo=timezone.utc)
    )

    if (maintenant - date_msg) > timedelta(hours=timeout_heures):
        return []

    messages_db.reverse()

    historique = []
    for msg in messages_db:
        role = "user" if msg.role == "user" else "model"
        texte_propre = (
            (msg.contenu or "")
            .replace("[AUDIO]", "")
            .replace("[TEXTE]", "")
            .strip()
        )
        if texte_propre:
            historique.append(
                types.Content(
                    role=role, parts=[types.Part.from_text(text=texte_propre)]
                )
            )

    return historique


# =====================================================================
# 3. FONCTION PRINCIPALE CHATAEC
# =====================================================================
async def generate_aec_response(
    utilisateur: Utilisateur,
    user_input: str | bytes,
    session_db: AsyncSession,
    media_type: str = "texte",
    is_group: bool = False,
) -> tuple[str, bool, int, int, str]:  # <-- on ajoute le texte transcrit en 5e valeur de retour
    """Génère une réponse écrite appuyée sur le RAG du programme."""

    # 1. Si c'est un vocal, on le transcrit d'abord en texte
    if media_type == "audio" and isinstance(user_input, bytes):
        transcript_resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                types.Part.from_bytes(data=user_input, mime_type="audio/ogg"),
                "Transcris exactement ce message vocal en français, sans aucun commentaire.",
            ],
        )
        user_input = (transcript_resp.text or "").strip()
        media_type = "texte"

        if not user_input:
            return (
                "Je n'ai pas réussi à entendre ce message vocal. Peux-tu le réenregistrer ou m'écrire ta question ?",
                False,
                0,
                0,
                "",
            )

    texte_utilisateur = str(user_input)

    # 2. Chargement de l'historique EN PREMIER (pour éclairer aussi le RAG)
    if is_group:
        contents_to_send = []
    else:
        contents_to_send = await charger_historique_recent(
            utilisateur.id_utilisateur, session_db, limite=12
        )

    # 3. Reformulation contextuelle + Recherche RAG dans le programme
    extraits_programme = ""
    if media_type == "texte" and texte_utilisateur:
        requete_rag = await reformuler_requete_rag(texte_utilisateur, contents_to_send)
        extraits_programme = await rechercher_passages_programme(requete_rag)

    # 4. Construction du prompt système
    system_instruction = generer_system_instruction(
        utilisateur=utilisateur,
        extraits_programme=extraits_programme,
    )

    # 5. Préparation du message utilisateur
    part = types.Part.from_text(text=texte_utilisateur)
    contents_to_send.append(types.Content(role="user", parts=[part]))

    # 6. Appel à Gemini
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.3,
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents_to_send,
        config=config,
    )

    # 7. Comptabilisation des tokens
    usage = getattr(response, "usage_metadata", None)
    total_p_tokens = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
    total_c_tokens = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0

    # 8. Extraction et nettoyage de la réponse
    clean_text = ""
    if response.candidates and response.candidates[0].content.parts:
        text_parts = [
            p.text
            for p in response.candidates[0].content.parts
            if getattr(p, "text", None)
        ]
        clean_text = "".join(text_parts).strip()

    clean_text = re.sub(r"\[THOUGHT\].*?\[/THOUGHT\]", "", clean_text, flags=re.DOTALL)
    clean_text = clean_text.replace("[AUDIO]", "").replace("[TEXTE]", "").strip()

    if not clean_text:
        clean_text = "Je n'ai pas pu formuler de réponse. Peux-tu reformuler ta question sur le programme ?"

    return clean_text, False, total_p_tokens, total_c_tokens, texte_utilisateur