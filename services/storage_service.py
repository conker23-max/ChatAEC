import os
import uuid
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise ValueError(
        "SUPABASE_URL ou SUPABASE_SERVICE_KEY manquants dans le .env"
    )

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
BUCKET_NAME = os.getenv("SUPABASE_STORAGE_BUCKET", "chataec_medias")


def upload_bytes_to_storage(
    file_bytes: bytes, file_ext: str, mime_type: str
) -> str:
    """Téléverse un fichier binaire (audio/image) et renvoie son URL publique."""
    unique_filename = f"{uuid.uuid4()}.{file_ext}"

    # Upload dans le bucket
    supabase.storage.from_(BUCKET_NAME).upload(
        path=unique_filename,
        file=file_bytes,
        file_options={"content-type": mime_type},
    )

    # Récupération de l'URL publique
    return supabase.storage.from_(BUCKET_NAME).get_public_url(unique_filename)