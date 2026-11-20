import asyncio
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, MISTRAL_API_KEY
from services.storage_service import supabase

client_ai = genai.Client(api_key=GEMINI_API_KEY)
EMBEDDING_MODEL = "gemini-embedding-001"


def _embed_query_sync(requete: str) -> list[float]:
    res = client_ai.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=requete,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=768,
        ),
    )
    return res.embeddings[0].values


async def rechercher_passages_programme(
    requete: str,
    top_k: int = 5,
    seuil: float = 0.50,
) -> str:
    """Recherche les passages les plus pertinents de L'Avenir en commun."""
    if not requete or not requete.strip():
        return ""

    # 1. Vectorisation de la question (dans un thread pour ne pas bloquer FastAPI)
    vecteur_requete = await asyncio.to_thread(_embed_query_sync, requete)

    # 2. Appel RPC pgvector sur Supabase
    def _rpc_sync():
        return supabase.rpc(
            "match_programme_chunks",
            {
                "query_embedding": vecteur_requete,
                "match_threshold": seuil,
                "match_count": top_k,
            },
        ).execute()

    response = await asyncio.to_thread(_rpc_sync)
    resultats = response.data or []

    if not resultats:
        return "Aucun extrait spécifique trouvé dans le programme pour cette requête."

    # 3. Formatage des extraits avec traçabilité
    blocs = []
    for item in resultats:
        titre_court = item.get("titre_court") or item.get("titre_source") or "Programme"
        partie = item.get("partie")
        chapitre = item.get("chapitre") or ""

        if partie:
            en_tete = f"--- Extrait [SOURCE : {titre_court} | {partie} | {chapitre}] ---"
        else:
            en_tete = f"--- Extrait [SOURCE : {titre_court} | {chapitre}] ---"

        blocs.append(f"{en_tete}\n{item['contenu']}")

    return "\n\n".join(blocs)