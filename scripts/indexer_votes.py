import csv
import html
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict

import requests
from dotenv import load_dotenv
from google import genai
from supabase import create_client

load_dotenv()

# --- CONFIGURATION ---
EMBEDDING_MODEL = "gemini-embedding-001"
TABLE_SUPABASE = "votes"
FICHIER_SCRUTINS = "./data/civix_scrutins_legislature_17.csv"
FICHIER_VOTES = "./data/civix_votes_legislature_17.csv"
FICHIER_AMENDEMENTS = "./data/amendements_agreges.csv"
FICHIER_CACHE_XML = "./data/cache_amendements.json"

# Mapping officiel des groupes parlementaires (17e législature)
GROUPES_L17 = {
    "PO845401": ("RN", "Rassemblement National (RN)"),
    "PO845407": ("EPR", "Ensemble pour la République (EPR)"),
    "PO845413": ("LFI", "La France insoumise - Nouveau Front Populaire (LFI)"),
    "PO845419": ("SOC", "Socialistes et apparentés (SOC)"),
    "PO845425": ("DR", "Droite Républicaine (DR / LR)"),
    "PO845439": ("ECO", "Écologiste et Social (ECO)"),
    "PO845454": ("DEM", "Les Démocrates (MoDem)"),
    "PO845470": ("HOR", "Horizons & Indépendants (HOR)"),
    "PO845485": ("LIOT", "Libertés, Indépendants, Outre-mer et Territoires (LIOT)"),
    "PO845514": ("GDR", "Gauche Démocrate et Républicaine (GDR / PCF)"),
    "PO847173": ("UDR", "Union des droites pour la République (UDR / Ciotti)"),
    "PO840056": ("NI", "Députés Non-inscrits (NI)"),
}

# Mots-clés thématiques recherchés dans l'exposé sommaire des amendements
MOTS_CLES_IMPORTANTS = (
    # Pouvoir d'achat, Salaires & Travail
    "smic", "salaire", "remuneration", "pouvoir d'achat", "inflation",
    "alimentaire", "ticket-restaurant", "tickets-restaurant",
    "heures supplementaires", "temps de travail", "35 heures",
    "chomage", "assurance chomage", "rsa", "allocations", "aah", "pauvrete",
    # Retraites & Protection sociale
    "retraite", "retraites", "pension", "pensions", "minimum vieillesse",
    "age legal", "64 ans", "62 ans", "60 ans", "carriere longue", "penibilite",
    "securite sociale", "franchise medicale", "deremboursement",
    # Fiscalité, Impôts & Budget
    "impot", "impots", "fiscal", "tva", "csg", "isf", "fortune",
    "superprofits", "dividendes", "rachat d'actions", "taxe", "taxation",
    "niches fiscales", "exoneration", "cotisations", "flat tax",
    # Énergie, Carburants & Transports
    "energie", "electricite", "gaz", "carburant", "carburants", "essence", "gazole",
    "bouclier tarifaire", "edf", "nucleaire", "renouvelable", "eolien",
    "peage", "autoroute", "sncf", "zfe",
    # Immigration, Nationalité & Sécurité
    "immigration", "etranger", "etrangers", "titre de sejour", "oqtf",
    "expulsion", "asile", "frontiere", "nationalite", "droit du sol",
    "ame", "aide medicale", "police", "gendarmerie", "prison", "peine",
    "delinquance", "stupefiants", "narcotrafic", "laicite", "separatisme",
    # Logement & Immobilier
    "logement", "loyer", "loyers", "encadrement", "apl", "hlm",
    "logement social", "sru", "passoire thermique", "dpe",
    # Santé, Éducation, Société & Services publics
    "hopital", "hopitaux", "soignant", "desert medical", "ehpad",
    "ecole", "education", "enseignant", "universite", "etudiant", "bourse",
    "audiovisuel public", "redevance", "aide a mourir", "fin de vie",
    # Agriculture, Écologie & Industrie
    "agriculture", "agriculteur", "egalim", "pesticide", "glyphosate",
    "mercosur", "libre-echange", "bassines", "megabassines", "delocalisation"
)

client_gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))


def normaliser(texte: str) -> str:
    """Supprime les accents et passe en minuscules pour des jointures et recherches fiables."""
    if not texte:
        return ""
    texte = unicodedata.normalize("NFD", texte.lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texte).strip()


def nettoyer_html_xml(texte_brut: str) -> str:
    """Nettoie les balises HTML encodées dans les exposés sommaires XML de l'Assemblée."""
    if not texte_brut:
        return ""
    propre = html.unescape(texte_brut)
    propre = re.sub(r"<[^>]+>", " ", propre)
    return re.sub(r"\s+", " ", propre).strip()


def charger_cache_xml() -> dict:
    if os.path.exists(FICHIER_CACHE_XML):
        with open(FICHIER_CACHE_XML, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def sauvegarder_cache_xml(cache: dict):
    with open(FICHIER_CACHE_XML, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def charger_index_amendements() -> dict:
    """
    Indexe amendements_agreges.csv par numéro d'amendement de séance publique (purement numérique).
    Retourne { "836": [ {dossier, auteur, sort, url_xml}, ... ] }
    """
    index_amdt = defaultdict(list)
    if not os.path.exists(FICHIER_AMENDEMENTS):
        print(f"⚠️ Fichier '{FICHIER_AMENDEMENTS}' introuvable : seuls les scrutins majeurs seront indexés.")
        return index_amdt

    with open(FICHIER_AMENDEMENTS, mode="r", encoding="utf-8-sig") as f:
        lecteur = csv.DictReader(f)
        for row in lecteur:
            num = str(row.get("Numéro de l'amendement", "")).strip()
            # On garde uniquement les amendements de séance publique (numéro sans préfixe AS/CL/CE...)
            if not num.isdigit():
                continue
            url_xml = str(row.get("URL Amendement format XML", "")).strip()
            if not url_xml:
                continue

            index_amdt[num].append({
                "titre_court": row.get("Titre court", "").strip(),
                "titre_complet": row.get("Titre complet", "").strip(),
                "auteur": row.get("Auteur", "").strip(),
                "sort": row.get("Sort de l'amendement", "").strip(),
                "url_xml": url_xml,
            })

    total_seance = sum(len(v) for v in index_amdt.values())
    print(f"📑 {total_seance} amendements de séance publique chargés depuis '{FICHIER_AMENDEMENTS}'.")
    return index_amdt

def trouver_url_xml_amendement(titre_scrutin: str, index_amdt: dict) -> dict | None:
    """
    Croise le titre d'un scrutin CIVIX avec l'index des amendements en vérifiant
    la cohérence du texte/dossier pour éviter les faux positifs sur des numéros identiques.
    """
    m_num = re.search(r"amendement\s+n[°o]\s*(\d+)", titre_scrutin, flags=re.IGNORECASE)
    if not m_num:
        return None

    num = m_num.group(1)
    candidats = index_amdt.get(num, [])
    if not candidats:
        return None

    titre_norm = normaliser(titre_scrutin)
    annees_scrutin = set(re.findall(r"\b202[4-7]\b", titre_norm))

    # Mots vides à ignorer pour tester la correspondance du texte/dossier
    stopwords = {
        "projet", "proposition", "loi", "financement", "finances", "securite",
        "sociale", "pour", "les", "des", "une", "dans", "sur", "avec", "aux",
        "relative", "visant", "tendant", "portant", "premiere", "deuxieme",
        "nouvelle", "lecture", "commission", "mixte", "paritaire", "article",
        "amendement", "sous-amendement", "examen", "prioritaire"
    }

    meilleur_candidat = None
    meilleur_score = 0

    for c in candidats:
        texte_c = normaliser(f"{c.get('titre_court', '')} {c.get('titre_complet', '')}")
        annees_c = set(re.findall(r"\b202[4-7]\b", texte_c))

        # Si les deux mentionnent des années différentes (ex: 2025 vs 2027), on écarte
        if annees_scrutin and annees_c and not (annees_scrutin & annees_c):
            continue

        mots_c = [
            m for m in re.findall(r"[a-z0-9]{4,}", texte_c)
            if m not in stopwords and not m.isdigit()
        ]
        # On ne garde que les mots présents dans le titre du scrutin
        mots_communs = set(m for m in mots_c if m in titre_norm)
        score = len(mots_communs)

        # Bonus si l'auteur correspond
        auteur_raw = c.get("auteur", "")
        if auteur_raw:
            morceaux_auteur = [normaliser(p) for p in re.split(r"[\s,]+", auteur_raw) if len(p) > 3]
            if any(p in titre_norm for p in morceaux_auteur):
                score += 2

        if score > meilleur_score:
            meilleur_score = score
            meilleur_candidat = c

    # Seuil minimal : il faut au moins 1 mot-clé significatif en commun (ou l'auteur)
    return meilleur_candidat if meilleur_score >= 1 else None

def recuperer_contenu_xml(url_xml: str, cache: dict, session: requests.Session) -> str:
    """Télécharge et extrait l'exposé sommaire (+ dispositif) depuis l'URL XML de l'Assemblée."""
    if url_xml in cache:
        return cache[url_xml]

    try:
        resp = session.get(url_xml, timeout=15)
        if resp.status_code != 200:
            cache[url_xml] = ""
            return ""

        root = ET.fromstring(resp.content)
        expose = ""
        dispositif = ""

        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "exposeSommaire" and elem.text:
                expose = nettoyer_html_xml(elem.text)
            elif tag == "dispositif" and elem.text:
                dispositif = nettoyer_html_xml(elem.text)

        # On privilégie l'exposé sommaire (le but politique de l'amendement), complété par le dispositif si court
        texte_final = expose if expose else dispositif
        if expose and len(expose) < 120 and dispositif:
            texte_final = f"{expose} ({dispositif})"

        # Troncature propre pour garder un vecteur dense et précis
        if len(texte_final) > 850:
            coupe = texte_final[:850]
            dernier_point = max(coupe.rfind("."), coupe.rfind(";"))
            if dernier_point > 400:
                texte_final = coupe[: dernier_point + 1]
            else:
                dernier_espace = coupe.rfind(" ")
                texte_final = (coupe[:dernier_espace] if dernier_espace > 0 else coupe) + "…"
        cache[url_xml] = texte_final
        time.sleep(0.15)  # Courtoisie envers le serveur de l'Assemblée nationale
        return texte_final
    except Exception:
        cache[url_xml] = ""
        return ""


def preparer_scrutins_enrichis() -> dict:
    """
    Sélectionne :
    1. Les scrutins auto-porteurs (ensemble d'un texte, motion de censure, résolution).
    2. Les amendements dont on a pu récupérer l'exposé sommaire ET qui touchent un thème clé.
    """
    index_amdt = charger_index_amendements()
    cache_xml = charger_cache_xml()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (CivicRAGBot/1.0)"})

    scrutins_retenus = {}
    stats = {"textes_majeurs": 0, "amendements_enrichis": 0, "amendements_ignores": 0}

    with open(FICHIER_SCRUTINS, mode="r", encoding="utf-8") as f:
        lecteur = csv.DictReader(f)
        for row in lecteur:
            uid = row.get("scrutin_uid") or row.get("crutin_uid")
            titre = row.get("titre", "").strip()
            if not uid or not titre:
                continue

            titre_low = titre.lower()
            date_s = row.get("date_scrutin", "")[:10]

            try:
                resultat_global = json.loads(row.get("resultat_scrutin", "{}")).get("code", "")
            except json.JSONDecodeError:
                resultat_global = ""

            # CAS A : Amendement -> on ne le garde QUE s'il a un exposé sommaire thématique
            if titre_low.startswith(("l'amendement", "le sous-amendement")):
                match_amdt = trouver_url_xml_amendement(titre, index_amdt)
                if not match_amdt:
                    stats["amendements_ignores"] += 1
                    continue

                contenu_amdt = recuperer_contenu_xml(match_amdt["url_xml"], cache_xml, session)
                if not contenu_amdt:
                    stats["amendements_ignores"] += 1
                    continue

                texte_check = normaliser(f"{titre} {contenu_amdt}")
                if not any(mot in texte_check for mot in MOTS_CLES_IMPORTANTS):
                    stats["amendements_ignores"] += 1
                    continue

                scrutins_retenus[uid] = {
                    "date": date_s,
                    "titre": titre,
                    "resultat_global": resultat_global,
                    "contexte_enrichi": f"Objet de l'amendement ({match_amdt['titre_court']}) : {contenu_amdt}",
                }
                stats["amendements_enrichis"] += 1
                continue

            # CAS B : On exclut les motions de rejet et les articles isolés sans contexte
            if titre_low.startswith(("la motion de rejet", "l'article ", "les articles ")):
                continue

            # CAS C : Votes auto-porteurs (ensemble d'un projet/proposition de loi, motion de censure, résolution...)
            scrutins_retenus[uid] = {
                "date": date_s,
                "titre": titre,
                "resultat_global": resultat_global,
                "contexte_enrichi": "",
            }
            stats["textes_majeurs"] += 1

    sauvegarder_cache_xml(cache_xml)
    print(
        f"🎯 Scrutins retenus : {len(scrutins_retenus)} au total "
        f"({stats['textes_majeurs']} textes majeurs/motions + {stats['amendements_enrichis']} amendements enrichis ; "
        f"{stats['amendements_ignores']} amendements sans contexte ou hors-thème écartés)."
    )
    return scrutins_retenus


def agreger_votes_par_groupe(scrutins_valides: set) -> dict:
    compteurs = defaultdict(lambda: {"pour": 0, "contre": 0, "abstention": 0, "nonVotant": 0})
    with open(FICHIER_VOTES, mode="r", encoding="utf-8") as f:
        lecteur = csv.DictReader(f)
        for row in lecteur:
            uid = row.get("scrutin_uid")
            groupe_uid = row.get("groupe_uid", "").strip()
            if uid not in scrutins_valides or groupe_uid not in GROUPES_L17:
                continue
            pos = row.get("position", "").strip()
            if pos:
                compteurs[(uid, groupe_uid)][pos] = compteurs[(uid, groupe_uid)].get(pos, 0) + 1
    return compteurs


def generer_embeddings_batch(textes: list[str]) -> list[list[float]]:
    res = client_gemini.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=textes,
    )
    return [e.values for e in res.embeddings]


def indexer_votes_propres():
    scrutins = preparer_scrutins_enrichis()
    if not scrutins:
        print("❌ Aucun scrutin à indexer.")
        return

    votes_par_groupe = agreger_votes_par_groupe(set(scrutins.keys()))
    elements_a_inserer = []

    for (uid, groupe_uid), decompte in votes_par_groupe.items():
        infos = scrutins[uid]
        parti_code, groupe_nom_complet = GROUPES_L17[groupe_uid]

        votes_exprimes = {
            "POUR": decompte["pour"],
            "CONTRE": decompte["contre"],
            "ABSTENTION": decompte["abstention"],
        }
        position_maj = max(votes_exprimes, key=votes_exprimes.get)
        if votes_exprimes[position_maj] == 0:
            position_maj = "NON-VOTANT"

        detail = f"Pour: {decompte['pour']}, Contre: {decompte['contre']}, Abstention: {decompte['abstention']}"
        bloc_contexte = f" {infos['contexte_enrichi']}." if infos["contexte_enrichi"] else ""

        resume_rag = (
            f"Scrutin du {infos['date']} (résultat : {infos['resultat_global']}) : {infos['titre']}."
            f"{bloc_contexte} "
            f"Position du groupe {groupe_nom_complet} : {position_maj} ({detail})."
        )

        elements_a_inserer.append({
            "scrutin_id": uid,
            "parti": parti_code,
            "groupe_nom": groupe_nom_complet,
            "date_scrutin": infos["date"],
            "titre": infos["titre"],
            "vote_position": position_maj,
            "detail_votes": detail,
            "resume_rag": resume_rag,
        })

    total = len(elements_a_inserer)
    print(f"📦 {total} fiches propres prêtes à être vectorisées et uploadées sur Supabase...")

    taille_lot = 100
    nb_lots = (total - 1) // taille_lot + 1

    for i in range(0, total, taille_lot):
        lot = elements_a_inserer[i : i + taille_lot]
        vecteurs = generer_embeddings_batch([item["resume_rag"] for item in lot])
        for item, vec in zip(lot, vecteurs):
            item["embedding"] = vec

        supabase.table(TABLE_SUPABASE).upsert(lot, on_conflict="scrutin_id,parti").execute()
        print(f"✅ Lot {i // taille_lot + 1}/{nb_lots} uploadé.")
        time.sleep(0.8)

    print("🎉 Indexation propre terminée ! Chaque ligne contient désormais le sens réel du vote.")


if __name__ == "__main__":
    indexer_votes_propres()