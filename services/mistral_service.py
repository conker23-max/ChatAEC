from datetime import datetime, timedelta, timezone
import json
import re
import warnings

from mistralai.client import Mistral
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MISTRAL_API_KEY
from models import MessageLog, Utilisateur
from services.rag_service_mistral import rechercher_passages_programme
# from services.tools_factory import LISTE_TOOLS_MISTRAL, REGISTRE_OUTILS

warnings.filterwarnings("ignore", category=UserWarning)

if not MISTRAL_API_KEY:
    raise RuntimeError("La variable d'environnement MISTRAL_API_KEY est manquante.")

MODEL_NAME = "mistral-small-2603"


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
Finis toujours tes réponses par "#JLM2027"
Base ta réponse en priorité sur ces extraits officiels :
{extraits_programme}
=======================================================
"""

    return f"""Tu es ChatAEC, l'assistant conversationnel dédié au programme « L'Avenir en commun » de la France insoumise.
Tu as été créé par un militant. Tu es une version prototype et non officielle, mais tu te bases EXCLUSIVEMENT sur le programme enrichi des plans thématiques.
CONTEXTE :
- Date du jour : 
{date_du_jour}
- Interlocuteur : 
{prenom}
{bloc_rag}
RÈGLES DE CONDUITE ET DE TON :

0. ACCUEIL ET CONVERSATION COURANTE :
- Si l'interlocuteur te salue (« Bonjour », « Salut », etc.) ou demande qui tu es : réponds chaleureusement en une ou deux phrases, sans mentionner l'absence d'extraits, et propose-lui d'aborder un thème (pouvoir d'achat, retraite, écologie, ruralité, VIe République...).

1. FIDÉLITÉ STRICTE, SOBRIÉTÉ RÉDACTIONNELLE ET EXACTITUDE :
  - Base tes réponses aux questions programmatiques EXCLUSIVEMENT sur les extraits fournis. N'invente jamais de chiffre, de date ou de mesure.
  - Restitue les propositions avec exactitude : reprends les termes précis, les mécanismes juridiques ou les chiffres clés tels qu'ils sont rédigés dans les extraits (utilise des citations courtes entre guillemets si nécessaire pour les mesures phares).
  - Ne paraphrase pas exagérément et évite les envolées lyriques ou le militantisme théâtral : adopte une posture objective, factuelle et directe.
  - Si les extraits permettent de répondre, donne directement les mesures sous forme de puces claires. Ne commence JAMAIS ta réponse par « Je n'ai pas trouvé cette précision... » si tu disposes d'éléments pertinents dans le corpus.
  - Réserve la phrase « Je n'ai pas trouvé cette précision dans les extraits du programme et des plans à ma disposition. » UNIQUEMENT aux cas de vide total d'information.

2. RÉPONSE AUX OBJECTIONS OU ATTAQUES POLITIQUES :
- Face à une critique ou une caricature, rédige une réponse posée et argumentée en t'appuyant strictement sur les principes et mesures du programme présents dans les extraits.

3. TRAÇABILITÉ ULTRA-COURTE (PARTIE ET CHAPITRE) :
- Lorsque tu cites des mesures du corpus, ajoute juste avant ta question de relance une seule ligne discrète reprenant les références :
  - Pour le Programme : _📖 Programme (Partie X, chap. Y)_
    - Pour un plan : _📖 Nom du plan_
  - Ne l'affiche jamais lors des salutations ou des échanges généraux.

4. MISE EN PAGE TELEGRAM :
- Mets le texte des listes à puces directement sur la même ligne que le tiret (`- Titre de la mesure : explication`), sans retour à la ligne inutile juste après le tiret.
  - Conclus toujours par une question de relance neutre, sobre et ouverte.
"""


# =====================================================================
# 2. HISTORIQUE CONVERSATIONNEL (Fenêtre glissante)
# =====================================================================
async def reformuler_requete_rag(
    mistral_client: Mistral,
    question_actuelle: str,
    historique: list[dict],
) -> str:
    if not historique:
        return question_actuelle

    contexte_recent = historique[-4:]
    prompt_reformulation = (
        "Réécris la dernière question de l'utilisateur pour qu'elle soit autonome et claire "
        "pour un moteur de recherche sur le programme politique « L'Avenir en commun », "
        "en intégrant le sujet de la conversation précédente si nécessaire. "
        "Réponds UNIQUEMENT par la question reformulée, en une phrase courte."
    )

    messages = [
        *contexte_recent,
        {
            "role": "user",
            "content": f"{prompt_reformulation}\n\nQuestion : {question_actuelle}",
        },
    ]

    resp = await mistral_client.chat.complete_async(
        model=MODEL_NAME,
        messages=messages,
        temperature=0.1,
    )
    texte = (resp.choices[0].message.content or "").strip()
    return texte or question_actuelle


async def charger_historique_recent(
    id_utilisateur: int,
    session_db: AsyncSession,
    limite: int = 12,
    timeout_heures: int = 12,
) -> list[dict]:
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

    historique: list[dict] = []
    for msg in messages_db:
        role = "user" if msg.role == "user" else "assistant"
        texte_propre = (
            (msg.contenu or "")
            .replace("[AUDIO]", "")
            .replace("[TEXTE]", "")
            .strip()
        )
        if texte_propre:
            historique.append({"role": role, "content": texte_propre})

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
) -> tuple[str, bool, int, int, str]:
    """Génère une réponse écrite appuyée sur le RAG du programme."""

    if media_type == "audio" and isinstance(user_input, bytes):
        return (
            "Je ne traite pas encore les messages vocaux sur cette version. Peux-tu m'écrire ta question en texte ?",
            False,
            0,
            0,
            "",
        )

    texte_utilisateur = str(user_input).strip()

    async with Mistral(api_key=MISTRAL_API_KEY) as mistral_client:
        # 1. Chargement de l'historique EN PREMIER
        if is_group:
            historique: list[dict] = []
        else:
            historique = await charger_historique_recent(
                utilisateur.id_utilisateur, session_db, limite=12
            )

        # 2. Reformulation contextuelle + Recherche RAG dans le programme
        extraits_programme = ""
        if media_type == "texte" and texte_utilisateur:
            requete_rag = await reformuler_requete_rag(
                mistral_client, texte_utilisateur, historique
            )
            extraits_programme = await rechercher_passages_programme(requete_rag)

        # 3. Construction du prompt système et des messages
        system_instruction = generer_system_instruction(
            utilisateur=utilisateur,
            extraits_programme=extraits_programme,
        )

        messages: list[dict] = [
            {"role": "system", "content": system_instruction},
            *historique,
            {"role": "user", "content": texte_utilisateur},
        ]

        # 4. Appel à Mistral avec outils
        response = await mistral_client.chat.complete_async(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.3,
            # tools=LISTE_TOOLS_MISTRAL,
        )

        usage = getattr(response, "usage", None)
        p_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        c_tokens = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0

        assistant_msg = response.choices[0].message

        # 5. Vérification si Mistral souhaite appeler un outil (Function Calling)
        if getattr(assistant_msg, "tool_calls", None):
            tc = assistant_msg.tool_calls[0]
            nom_outil = tc.function.name
            raw_args = tc.function.arguments or "{}"
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

            if nom_outil in REGISTRE_OUTILS:
                resultat_outil = REGISTRE_OUTILS[nom_outil](**args)
                clean_text = f"Infographie générée avec succès !\n\n{resultat_outil}"
                return clean_text, False, p_tokens, c_tokens, texte_utilisateur

        # 6. Extraction et nettoyage de la réponse textuelle
        raw_content = assistant_msg.content
        if isinstance(raw_content, list):
            clean_text = "".join(
                getattr(chunk, "text", "")
                for chunk in raw_content
                if getattr(chunk, "text", None)
            ).strip()
        else:
            clean_text = (raw_content or "").strip()

        clean_text = re.sub(
            r"\[THOUGHT\].*?\[/THOUGHT\]", "", clean_text, flags=re.DOTALL
        )
        clean_text = clean_text.replace("[AUDIO]", "").replace("[TEXTE]", "").strip()

        if not clean_text:
            clean_text = "Je n'ai pas pu formuler de réponse. Peux-tu reformuler ta question sur le programme ?"

        return clean_text, False, p_tokens, c_tokens, texte_utilisateur