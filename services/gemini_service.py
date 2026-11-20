from datetime import datetime, timedelta, timezone
import re
from services.tools_factory import LISTE_TOOLS_GEMINI, REGISTRE_OUTILS  # <--- Importé ici
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
Finis systématiquement par "#AEC2027"
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
     • Pour le Programme : _📖 Programme (Partie X, chap. Y)_
     • Pour un plan : _📖 Nom du plan_
   - Ne l'affiche jamais lors des salutations ou des échanges généraux.

4. MISE EN PAGE TELEGRAM :
   - Mets le texte des listes à puces directement sur la même ligne que le tiret (`- Titre de la mesure : explication`), sans retour à la ligne inutile juste après le tiret.
   - Conclus toujours par une question de relance neutre, sobre et ouverte.
"""


# =====================================================================
# 2. HISTORIQUE CONVERSATIONNEL (Fenêtre glissante)
# =====================================================================
async def reformuler_requete_rag(
    question_actuelle: str,
    historique: list[types.Content],
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
) -> tuple[str, bool, int, int, str]:
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

    # 2. Chargement de l'historique EN PREMIER
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

    # 6. Appel à Gemini AVEC les outils intégrés dans la config
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.3,
        tools=LISTE_TOOLS_GEMINI,  # <--- On passe la liste d'outils ici
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents_to_send,
        config=config,
    )

    # 7. Vérification si Gemini souhaite appeler un outil (Function Calling)
    if response.function_calls:
        # On prend le premier appel de fonction demandé par Gemini
        fc = response.function_calls[0]
        nom_outil = fc.name
        args = fc.args

        if nom_outil in REGISTRE_OUTILS:
            # Exécution de la fonction Python située dans tools_factory.py
            resultat_outil = REGISTRE_OUTILS[nom_outil](**args)
            
            # Le résultat de la fonction (ex: chemin de l'image générée) devient la réponse du bot
            # (Tu pourras adapter si tu veux que le bot commente textuellement le résultat)
            clean_text = f"Infographie générée avec succès !\n\n{resultat_outil}"
            
            # Métriques de tokens approximatives pour cet appel
            usage = getattr(response, "usage_metadata", None)
            p_tokens = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
            c_tokens = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0

            return clean_text, False, p_tokens, c_tokens, texte_utilisateur

    # 8. Comptabilisation des tokens (flux textuel normal)
    usage = getattr(response, "usage_metadata", None)
    total_p_tokens = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
    total_c_tokens = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0

    # 9. Extraction et nettoyage de la réponse textuelle
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