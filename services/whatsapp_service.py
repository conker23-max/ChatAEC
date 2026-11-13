import asyncio
from io import BytesIO
import os
import tempfile
from config import META_GRAPH_URL, PHONE_NUMBER_ID, WHATSAPP_TOKEN
import requests
from services.tts_service import tts_service

HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json",
}


def send_text_message(recipient_phone: str, text: str) -> dict:
    """Envoie un message texte simple à l'utilisateur."""
    url = f"{META_GRAPH_URL}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_phone,
        "type": "text",
        "text": {"body": text},
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    return response.json()


def download_media_bytes(media_id: str) -> bytes:
    """Récupère l'URL temporaire et télécharge le flux binaire du média."""
    info_url = f"{META_GRAPH_URL}/{media_id}"
    res_info = requests.get(
        info_url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    )
    res_info.raise_for_status()
    download_url = res_info.json().get("url")

    res_file = requests.get(
        download_url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    )
    res_file.raise_for_status()
    return res_file.content


def upload_whatsapp_media(file_path: str, mime_type: str = "audio/ogg") -> str:
    """Téléverse un fichier local sur les serveurs Meta et retourne son media_id."""
    url = f"{META_GRAPH_URL}/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, mime_type)}
        data = {"messaging_product": "whatsapp"}
        res = requests.post(url, headers=headers, data=data, files=files)
        res.raise_for_status()
        return res.json().get("id")

def mark_message_as_read(message_id: str) -> dict:
    """Marque le message reçu comme lu (déclenche les coches bleues côté élève)."""
    url = f"{META_GRAPH_URL}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        response = requests.post(url, headers=HEADERS, json=payload, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"⚠️ Impossible de marquer le message comme lu : {e}")
        return {}

def send_image_message(
    recipient_phone: str, image_bytes: bytes, caption: str = ""
) -> dict:
    """1.

    Téléverse l'image PNG générée directement depuis la mémoire vers Meta.
    2. Envoie l'image avec une légende (caption) à l'élève.
    """
    # ÉTAPE 1 : Upload du média PNG sur l'API Meta Graph
    upload_url = f"{META_GRAPH_URL}/{PHONE_NUMBER_ID}/media"
    upload_headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {"file": ("schema.png", image_bytes, "image/png")}
    data = {"messaging_product": "whatsapp"}

    upload_res = requests.post(
        upload_url, headers=upload_headers, data=data, files=files
    )
    upload_res.raise_for_status()
    media_id = upload_res.json().get("id")

    # ÉTAPE 2 : Envoi du message image WhatsApp
    msg_url = f"{META_GRAPH_URL}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_phone,
        "type": "image",
        "image": {"id": media_id, "caption": caption},
    }
    response = requests.post(msg_url, headers=HEADERS, json=payload)
    response.raise_for_status()
    return response.json()


async def send_audio_message(recipient_phone: str, text: str):
    """1. Génère la synthèse vocale XTTS fine-tunée en OGG/Opus.

    2. Téléverse le fichier vers l'API WhatsApp Media.
    3. Envoie la note vocale à l'élève.
    4. Supprime le fichier temporaire du disque.
    """
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # ÉTAPE 1 : Génération vocale
        await tts_service.generate_voice_note(text, tmp_path)

        # ÉTAPE 2 : Upload du fichier OGG sur Meta
        media_id = upload_whatsapp_media(tmp_path, mime_type="audio/ogg")

        # ÉTAPE 3 : Envoi sous forme de note vocale WhatsApp
        url = f"{META_GRAPH_URL}/{PHONE_NUMBER_ID}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "audio",
            "audio": {"id": media_id},
        }
        response = requests.post(url, headers=HEADERS, json=payload)
        response.raise_for_status()
        return response.json()

    except Exception as e:
        print(f"Erreur lors de l'envoi vocal : {e}")
        # Bascule automatique en message texte en cas de problème
        return send_text_message(recipient_phone, text)

    finally:
        # ÉTAPE 4 : Nettoyage du fichier temporaire
        if os.path.exists(tmp_path):
            os.remove(tmp_path)