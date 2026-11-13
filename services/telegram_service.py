import os
import httpx

# --- CHARGEMENT DU TOKEN & USERNAME ---
RAW_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or ""
BOT_TOKEN = RAW_TOKEN.strip().strip('"').strip("'")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "ChatAEC_bot").lstrip("@")

if BOT_TOKEN.startswith("bot"):
    BASE_URL = f"https://api.telegram.org/{BOT_TOKEN}"
    FILE_BASE_URL = f"https://api.telegram.org/file/{BOT_TOKEN}"
else:
    BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
    FILE_BASE_URL = f"https://api.telegram.org/file/bot{BOT_TOKEN}"


async def send_telegram_text(
    chat_id: int | str,
    text: str,
    show_mp_button: bool = False,
) -> dict:
    """Envoie un message en texte brut sur Telegram."""
    url = f"{BASE_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if show_mp_button:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {
                        "text": "Continuer la discussion avec moi en MP 👉",
                        "url": f"https://t.me/{BOT_USERNAME}?start=discussion",
                    }
                ]
            ]
        }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


async def download_telegram_file(file_id: str) -> bytes:
    """Récupère le binaire d'une photo ou d'un message vocal Telegram."""
    async with httpx.AsyncClient() as client:
        res_info = await client.get(
            f"{BASE_URL}/getFile", params={"file_id": file_id}
        )
        res_info.raise_for_status()
        file_path = res_info.json().get("result", {}).get("file_path")

        res_file = await client.get(f"{FILE_BASE_URL}/{file_path}")
        res_file.raise_for_status()
        return res_file.content