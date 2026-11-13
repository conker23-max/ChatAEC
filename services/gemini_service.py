from datetime import datetime, timedelta, timezone
import re
import warnings
from google import genai
from google.genai import types
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import GEMINI_API_KEY
from models import MessageLog, Utilisateur
# À décommenter dès que nous aurons construit le service RAG :
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
1. FIDÉLITÉ AU PROGRAMME :
   - Appuie-toi strictement sur les extraits du programme fournis ci-dessus.
   - Si une question porte sur un sujet absent des extraits ou hors du programme, indique-le honnêtement sans inventer de mesure.
   - Cite, lorsque c'est pertinent, les mesures phares, les objectifs ou les chiffres clés présents dans les textes.

2. CLARTÉ & PÉDAGOGIE :
   - Adopte un ton accessible, engagé, respectueux et pédagogue.
   - Va droit au but : structure tes réponses avec des listes à puces courtes lorsque tu présentes plusieurs mesures.
   - Termine souvent par une ouverture courte pour proposer d'approfondir un point ou un thème connexe.

3. FORMAT MESSAGERIE (TELEGRAM / WHATSAPP) :
   - Pas de titres Markdown complexes (`#`, `##`).
   - Utilise le gras (`*mot*`) pour mettre en valeur les notions clés et les mesures principales.
   - Reste concis (idéalement moins de 1 200 caractères par réponse pour rester agréable à lire sur mobile).

4. MODALITÉ DE RÉPONSE (OBLIGATOIRE EN DÉBUT DE MESSAGE) :
   Commence chaque réponse par l'une de ces deux balises :
   - [AUDIO] : Uniquement si l'utilisateur t'a envoyé un message vocal ou demande explicitement une réponse vocale.
   - [TEXTE] : Dans tous les autres cas (par défaut).
"""


# =====================================================================
# 2. HISTORIQUE CONVERSATIONNEL (Fenêtre glissante)
# =====================================================================
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

    # Réinitialisation du contexte après une longue pause
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
) -> tuple[str, bool, int, int]:
    """Génère une réponse conversationnelle appuyée sur le RAG du programme."""

    # 1. Recherche RAG dans le programme (si l'entrée est textuelle)
    extraits_programme = ""
    if media_type == "texte" and isinstance(user_input, str):
        # TODO : Brancher ici la recherche vectorielle / hybride sur Supabase
        extraits_programme = await rechercher_passages_programme(user_input)
        pass

    # 2. Construction du prompt système
    system_instruction = generer_system_instruction(
        utilisateur=utilisateur,
        extraits_programme=extraits_programme,
    )

    # 3. Chargement de l'historique (désactivé en groupe pour éviter les mélanges)
    if is_group:
        contents_to_send = []
    else:
        contents_to_send = await charger_historique_recent(
            utilisateur.id_utilisateur, session_db, limite=12
        )

    # 4. Préparation du message utilisateur (Texte, Audio ou Image)
    if media_type == "audio" and isinstance(user_input, bytes):
        part = types.Part.from_bytes(data=user_input, mime_type="audio/ogg")
    elif media_type == "image" and isinstance(user_input, bytes):
        part = types.Part.from_bytes(data=user_input, mime_type="image/jpeg")
    else:
        part = types.Part.from_text(text=str(user_input))

    contents_to_send.append(types.Content(role="user", parts=[part]))

    # 5. Appel à Gemini (sans tools complexes, le RAG injecte le contexte directement)
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.4,  # Température modérée pour rester fidèle aux extraits du programme
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents_to_send,
        config=config,
    )

    # 6. Comptabilisation des tokens
    total_p_tokens = 0
    total_c_tokens = 0
    usage = getattr(response, "usage_metadata", None)
    if usage:
        total_p_tokens = getattr(usage, "prompt_token_count", 0) or 0
        total_c_tokens = getattr(usage, "candidates_token_count", 0) or 0

    # 7. Extraction et nettoyage de la réponse
    clean_text = ""
    if response.candidates and response.candidates[0].content.parts:
        text_parts = [
            p.text
            for p in response.candidates[0].content.parts
            if getattr(p, "text", None)
        ]
        clean_text = "".join(text_parts).strip()

    clean_text = re.sub(r"\[THOUGHT\].*?\[/THOUGHT\]", "", clean_text, flags=re.DOTALL)

    reply_as_audio = clean_text.startswith("[AUDIO]")
    clean_text = clean_text.replace("[AUDIO]", "").replace("[TEXTE]", "").strip()

    if not clean_text:
        clean_text = "Je n'ai pas pu formuler de réponse. Peux-tu reformuler ta question sur le programme ?"

    return clean_text, reply_as_audio, total_p_tokens, total_c_tokens