import os
import re
import html
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

def formater_pour_telegram(texte: str) -> str:
    if not texte:
        return ""

    # 1. Nettoyer d'éventuelles balises HTML déjà présentes dans le texte brut
    texte = texte.replace("<b>", "**").replace("</b>", "**")
    texte = texte.replace("<i>", "_").replace("</i>", "_")

    # 2. Échapper uniquement <, > et & (quote=False évite les &#x27; sur les apostrophes !)
    texte_securise = html.escape(texte, quote=False)

    # 3. Convertir le gras (**texte**) et l'italique (_texte_) en HTML Telegram
    texte_securise = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", texte_securise)
    texte_securise = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", texte_securise)

    return texte_securise
async def send_telegram_text(
    chat_id: int | str,
    text: str,
    show_mp_button: bool = False,
) -> dict:
    """Envoie un message formaté en HTML sur Telegram."""
    url = f"{BASE_URL}/sendMessage"
    texte_formate = formater_pour_telegram(text)

    payload = {
        "chat_id": chat_id,
        "text": texte_formate[:4096],
        "parse_mode": "HTML",
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