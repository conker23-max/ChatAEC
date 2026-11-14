import os
import re
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from supabase import create_client

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

client_ai = genai.Client(api_key=GEMINI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

EMBEDDING_MODEL = "gemini-embedding-001"

# ==========================================================
# PARAMÈTRES DU DOCUMENT À INDEXER (À changer selon le plan)
# ==========================================================
INPUT_FILE = "plan_textile.md"
TYPE_DOCUMENT = "plan_productif"  # 'programme_aec' | 'plan_investissement' | 'plan_thematique'
TITRE_SOURCE = "Plan d'investissement productif : Textile"
DEFAULT_URL = "https://melenchon2027.fr/plans-investissement/textile/"


def decouper_markdown_en_chunks(filepath: str, titre_source: str, default_url: str) -> list[dict]:
    """Découpe le fichier Markdown par chapitre (#) et sous-thème (## / ###)."""
    with open(filepath, "r", encoding="utf-8") as f:
        texte = f.read()

    sections = texte.split("\n---\n")
    chunks = []

    for section in sections:
        section = section.strip()
        if not section:
            continue

        match_titre = re.search(r"^#\s+(.+)$", section, re.MULTILINE)
        chapitre = match_titre.group(1).strip() if match_titre else titre_source
        if chapitre == "Section sans titre":
            chapitre = "Partie thématique"

        match_source = re.search(r"^Source\s*:\s*(\S+)$", section, re.MULTILINE)
        source_url = match_source.group(1).strip() if match_source else default_url

        corps = re.sub(r"^#\s+.+$", "", section, count=1, flags=re.MULTILINE)
        corps = re.sub(r"^Source\s*:\s*\S+$", "", corps, count=1, flags=re.MULTILINE).strip()

        sous_blocs = re.split(r"(?=^#{2,3}\s+)", corps, flags=re.MULTILINE)

        for bloc in sous_blocs:
            bloc = bloc.strip()
            if len(bloc) < 80:
                continue

            match_st = re.search(r"^#{2,3}\s+(.+)$", bloc, re.MULTILINE)
            sous_theme = match_st.group(1).strip() if match_st else chapitre

            # Enrichir le vecteur avec le nom du plan pour éviter toute confusion avec le programme
            texte_enrichi = f"[{titre_source} | {chapitre} > {sous_theme}]\n{bloc}"

            if len(texte_enrichi) > 3500:
                paragraphes = bloc.split("\n\n")
                tampon = f"[{titre_source} | {chapitre} > {sous_theme}]\n"
                for p in paragraphes:
                    if len(tampon) + len(p) > 3000 and len(tampon) > 200:
                        chunks.append({
                            "chapitre": chapitre,
                            "sous_theme": sous_theme,
                            "source_url": source_url,
                            "contenu": tampon.strip(),
                        })
                        tampon = f"[{titre_source} | {chapitre} > {sous_theme} (suite)]\n{p}\n\n"
                    else:
                        tampon += p + "\n\n"
                if len(tampon.strip()) > 100:
                    chunks.append({
                        "chapitre": chapitre,
                        "sous_theme": sous_theme,
                        "source_url": source_url,
                        "contenu": tampon.strip(),
                    })
            else:
                chunks.append({
                    "chapitre": chapitre,
                    "sous_theme": sous_theme,
                    "source_url": source_url,
                    "contenu": texte_enrichi,
                })

    return chunks


def generer_embedding(texte: str) -> list[float]:
    res = client_ai.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texte,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=768,
        ),
    )
    return res.embeddings[0].values


def indexer_document():
    chunks = decouper_markdown_en_chunks(INPUT_FILE, TITRE_SOURCE, DEFAULT_URL)
    print(f"📦 {len(chunks)} chunks générés depuis '{INPUT_FILE}' ({TITRE_SOURCE}).")

    if not chunks:
        print("⚠️ Aucun chunk valide détecté, abandon avant suppression.")
        return

    # Suppression CIBLEE uniquement sur ce document (préserve le programme général !)
    supabase.table("programme_chunks").delete().eq("titre_source", TITRE_SOURCE).execute()
    print(f"🧹 Anciens chunks de '{TITRE_SOURCE}' nettoyés.")

    for idx, chunk in enumerate(chunks, start=1):
        try:
            vecteur = generer_embedding(chunk["contenu"])
            supabase.table("programme_chunks").insert({
                "type_document": TYPE_DOCUMENT,
                "titre_source": TITRE_SOURCE,
                "chapitre": chunk["chapitre"],
                "sous_theme": chunk["sous_theme"],
                "source_url": chunk["source_url"],
                "contenu": chunk["contenu"],
                "embedding": vecteur,
            }).execute()
            print(f"✅ [{idx}/{len(chunks)}] Indexé : {chunk['chapitre']} > {chunk['sous_theme']}")
            time.sleep(0.1)
        except Exception as e:
            print(f"❌ Erreur sur chunk {idx} : {e}")

    print(f"\n🎉 Indexation de '{TITRE_SOURCE}' terminée avec succès !")


if __name__ == "__main__":
    indexer_document()