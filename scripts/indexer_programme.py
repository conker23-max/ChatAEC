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

INPUT_FILE = "programme_aec.md"
EMBEDDING_MODEL = "gemini-embedding-001"


def decouper_markdown_en_chunks(filepath: str) -> list[dict]:
    """Découpe le fichier Markdown par chapitre (#) et sous-thème (## / ###)."""
    with open(filepath, "r", encoding="utf-8") as f:
        texte = f.read()

    sections = texte.split("\n---\n")
    chunks = []

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extraction du chapitre (# ...) et de la source
        match_titre = re.search(r"^#\s+(.+)$", section, re.MULTILINE)
        chapitre = match_titre.group(1).strip() if match_titre else "Introduction / Avant-propos"
        if chapitre == "Section sans titre":
            chapitre = "Partie thématique"

        match_source = re.search(r"^Source\s*:\s*(\S+)$", section, re.MULTILINE)
        source_url = match_source.group(1).strip() if match_source else "https://melenchon2027.fr/programme/livre/"

        # Nettoyage de l'en-tête pour découper sur ## ou ###
        corps = re.sub(r"^#\s+.+$", "", section, count=1, flags=re.MULTILINE)
        corps = re.sub(r"^Source\s*:\s*\S+$", "", corps, count=1, flags=re.MULTILINE).strip()

        sous_blocs = re.split(r"(?=^#{2,3}\s+)", corps, flags=re.MULTILINE)

        for bloc in sous_blocs:
            bloc = bloc.strip()
            if len(bloc) < 80:
                continue

            match_st = re.search(r"^#{2,3}\s+(.+)$", bloc, re.MULTILINE)
            sous_theme = match_st.group(1).strip() if match_st else chapitre

            # Enrichir le texte avec son contexte hiérarchique pour un meilleur embedding
            texte_enrichi = f"[{chapitre} > {sous_theme}]\n{bloc}"

            # Si un sous-bloc dépasse 3500 caractères, on le subdivise par paragraphes
            if len(texte_enrichi) > 3500:
                paragraphes = bloc.split("\n\n")
                tampon = f"[{chapitre} > {sous_theme}]\n"
                for p in paragraphes:
                    if len(tampon) + len(p) > 3000 and len(tampon) > 200:
                        chunks.append({
                            "chapitre": chapitre,
                            "sous_theme": sous_theme,
                            "source_url": source_url,
                            "contenu": tampon.strip(),
                        })
                        tampon = f"[{chapitre} > {sous_theme} (suite)]\n{p}\n\n"
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


def indexer_tout():
    chunks = decouper_markdown_en_chunks(INPUT_FILE)
    print(f"📦 {len(chunks)} chunks générés depuis '{INPUT_FILE}'.")

    # Optionnel : vider la table avant réindexation complète
    supabase.table("programme_chunks").delete().neq("id", 0).execute()

    for idx, chunk in enumerate(chunks, start=1):
        try:
            vecteur = generer_embedding(chunk["contenu"])
            supabase.table("programme_chunks").insert({
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

    print("\n🎉 Indexation RAG terminée avec succès !")


if __name__ == "__main__":
    indexer_tout()