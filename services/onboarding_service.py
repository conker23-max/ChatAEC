import json
from google import genai
from google.genai import types
from models import Utilisateur
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import unicodedata
client = genai.Client()


class DecisionOnboarding(BaseModel):
    prenom: str | None = Field(
        None,
        description=(
            "Prénom extrait ou corrigé. Null si non mentionné ou incompris."
        ),
    )
    genre: str | None = Field(
        None,
        description=(
            "'Fille' ou 'Garcon' (gère les fautes, argot 'mec/meuf/gars'). Null"
            " si non précisé."
        ),
    )
    pays: str | None = Field(
        None,
        description=(
            "Pays normalisé (ex: 'France', 'Bénin', 'Belgique', etc.). Null si"
            " non précisé."
        ),
    )
    classe: str | None = Field(
        None,
        description=(
            "Classe normalisée (ex: '6e', '5e', '4e', '3e', '2nde', '1re',"
            " 'Terminale', ou équivalent local). Null si non précisée."
        ),
    )
    message_a_envoyer: str = Field(
        ...,
        description=(
            "Le message à envoyer pour demander l'information manquante."
        ),
    )
    est_termine: bool = Field(
        ...,
        description=(
            "True UNIQUEMENT si le prénom, le pays ET la classe sont"
            " tous connus."
        ),
    )


def executer_onboarding_ia(
    eleve: Utilisateur, message_texte: str
) -> DecisionOnboarding:
    prompt = f"""
Tu es SoMax, un tuteur socratique de mathématiques chaleureux et bienveillant sur WhatsApp.
Tu es en train d'accueillir un nouvel élève.

État actuel de ce que tu sais sur l'élève :
- Prénom : {eleve.prenom_eleve or "Inconnu"}
- Pays : {eleve.pays or "Inconnu"}
- Classe : {eleve.classe or "Inconnu"}

Message que l'élève vient d'envoyer : "{message_texte}"

Consignes :
1. Analyse le message pour extraire intelligemment toute information nouvelle (fautes, argot, abréviations).
2. Question à poser :
- Le prénom
- La classe (Ex : 6eme, 5eme, 4eme, 3eme, 2nde, 1re, Terminale).
S'il répond Terminale demande de préciser la filière (math complémentaire, math expert, tle A, B, C ou D)
3. Si tout est complet (Classe, prénom), définis est_termine=True.
4. Rédige un message court et adapté au format WhatsApp pour relancer l'élève sur l'information manquante.
"""
    # - Genre : {eleve.genre or "Inconnu"}

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DecisionOnboarding,
            temperature=0.1,
        ),
    )
    data = json.loads(response.text)
    return DecisionOnboarding(**data)


# Mapping des pays francophones (nom normalisé -> code ISO 3166-1 alpha-2)
PAYS_VERS_CODE = {
    # France & Europe
    "france": "FR",
    "belgique": "BE",
    "suisse": "CH",
    "luxembourg": "LU",
    "monaco": "MC",
    # Afrique de l'Ouest
    "benin": "BJ",
    "senegal": "SN",
    "cote d ivoire": "CI",
    "cote d'ivoire": "CI",
    "togo": "TG",
    "mali": "ML",
    "burkina faso": "BF",
    "guinee": "GN",
    "niger": "NE",
    # Afrique Centrale & Océan Indien
    "cameroun": "CM",
    "gabon": "GA",
    "congo": "CG",
    "rdc": "CD",
    "republique democratique du congo": "CD",
    "madagascar": "MG",
    # Maghreb
    "maroc": "MA",
    "algerie": "DZ",
    "tunisie": "TN",
    # Amériques
    "canada": "CA",
    "quebec": "CA",
    "haiti": "HT",
}

def extraire_code_pays(pays_nom: str | None) -> str | None:
    """Nettoie le texte (accents, casse) et retourne le code ISO à 2 lettres."""
    if not pays_nom:
        return None

    # Normalisation : minuscules, suppression des accents et espaces superflus
    texte_propre = pays_nom.strip().lower()
    texte_sans_accents = "".join(
        c
        for c in unicodedata.normalize("NFD", texte_propre)
        if unicodedata.category(c) != "Mn"
    )

    return PAYS_VERS_CODE.get(texte_sans_accents, "FR")  # "FR" par défaut si non reconnu

async def handle_onboarding_step(
    eleve: Utilisateur, message_texte: str, session: AsyncSession
) -> str:
    """Gère l'onboarding piloté par Gemini et injecte le message d'accueil complet."""
    decision = executer_onboarding_ia(eleve, message_texte)

    # 1. Sauvegarde en BDD des champs extraits
    if decision.prenom:
        eleve.prenom_eleve = decision.prenom.capitalize()
    if decision.genre:
        eleve.genre = decision.genre
    if decision.pays:
        eleve.pays = decision.pays
        eleve.pays_code = extraire_code_pays(decision.pays)
    if decision.classe:
        eleve.classe = decision.classe

    # 2. Mise à jour de l'état et sélection du message de retour
    if decision.est_termine:
        eleve.etape_onboarding = "TERMINE"
        prenom_affiche = eleve.prenom_eleve or "à toi"

        # 📌 Message final structuré pour WhatsApp
        message_final = f"""Bravo {prenom_affiche}, ton inscription est validée ! 🎉

Moi, c'est *SoMax*, ton tuteur de maths. Mon but n'est pas de te donner les réponses toutes faites, mais de t'aider à comprendre pas à pas pour que tu réussisses par toi-même.

⚠️ *Version Bêta* : SoMax est en plein apprentissage ! Pour améliorer ses explications, les échanges sont enregistrés de façon anonyme.
Si une explication n'est pas claire, dis-lui directement dans le chat (ex: "j'ai pas compris" ou "tu t'es trompé"), il s'adaptera !

*Ce qu'on peut faire ensemble :*
- *Vocaux, photos ou texte :* envoie-moi une photo de ton cahier, un énoncé tapé ou un vocal si tu bloques.
- *Schémas et figures :* je peux générer des graphiques et des figures géométriques si tu as besoin de visualiser.
- *Préparation de tes contrôles :* dis-moi dès que tu as une date de DS ou d'interro, je m'en souviendrai pour cibler tes révisions.
- *Entraînement sur mesure :* si tu veux t'exercer, je peux te proposer des problèmes adaptés à ton niveau.

On commence ? Tu as un devoir à faire, ou tu préfères que je te propose un sujet ?"""

        reponse_texte = message_final
    else:
        reponse_texte = decision.message_a_envoyer
        if not eleve.prenom_eleve:
            eleve.etape_onboarding = "ATTENTE_PRENOM"
        # elif not eleve.genre:
        #     eleve.etape_onboarding = "ATTENTE_GENRE"
        elif not eleve.pays:
            eleve.etape_onboarding = "ATTENTE_PAYS"
        else:
            eleve.etape_onboarding = "ATTENTE_CLASSE"

    await session.commit()
    return reponse_texte

