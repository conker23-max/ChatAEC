import asyncio
import os
import subprocess
import tempfile
import edge_tts

# =====================================================================
# ANCIEN MODÈLE XTTS FINE-TUNÉ (MIS EN PAUSE POUR LE CLOUD / BÊTA)
# =====================================================================
# import sys
# import torch
# import torchaudio
#
# DLL_PATH = r"C:\Users\alexi\Documents\bin"
# if os.path.exists(DLL_PATH):
#     os.add_dll_directory(DLL_PATH)
#
# from TTS.tts.configs.xtts_config import XttsConfig
# from TTS.tts.models.xtts import Xtts
# =====================================================================


class TTSService:
    _instance = None

    # Voix par défaut si non précisée
    DEFAULT_VOICE = "fr-FR-VivienneMultilingualNeural"

    def __init__(self):
        print("✅ Synthèse vocale Cloud haute fidélité prête (edge-tts).")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def generate_voice_note(
        self,
        text: str,
        output_ogg_path: str,
        voice: str | None = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ):
        """Génère une note vocale WhatsApp ultra-naturelle via Edge-TTS."""
        # 1. Nettoyage du texte Markdown
        texte_propre = (
            text.replace("*", "")
            .replace("#", "")
            .replace("`", "")
            .replace("_", "")
        )

        chosen_voice = voice or self.DEFAULT_VOICE

        with tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False
        ) as tmp_mp3:
            mp3_path = tmp_mp3.name

        try:
            # 2. Synthèse asynchrone directe (pas besoin de thread bloquant)
            communicate = edge_tts.Communicate(
                text=texte_propre, voice=chosen_voice, rate=rate, pitch=pitch
            )
            await communicate.save(mp3_path)

            # 3. Encodage Opus VoIP pour WhatsApp
            def convert_ffmpeg():
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        mp3_path,
                        "-c:a",
                        "libopus",
                        "-b:a",
                        "64k",
                        "-vbr",
                        "on",
                        "-application",
                        "voip",
                        output_ogg_path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )

            await asyncio.to_thread(convert_ffmpeg)

        finally:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)


# Variable importée par le reste du projet
tts_service = TTSService.get_instance()