import asyncio
from database import AsyncSessionLocal
from models import MessageLog
from services.user_service import get_or_create_utilisateur
from services.gemini_service import generate_aec_response

# Codes couleurs ANSI pour un affichage propre dans PowerShell
MAGENTA = "\033[95m"
CYAN = "\033[96m"
GRIS = "\033[90m"
RESET = "\033[0m"


async def boucle_discussion():
    print(f"{MAGENTA}=== ChatAEC - Mode Test Terminal ==={RESET}")
    print(f"{GRIS}Tape 'quit' ou 'exit' pour arrêter la discussion.{RESET}\n")

    async with AsyncSessionLocal() as db:
        # Récupère ou crée ton utilisateur de test en base
        user = await get_or_create_utilisateur(
    platform_id="cli_test_alexis",
    session_db=db,
    prenom_defaut="Alexis",
)

        while True:
            try:
                question = input(f"{CYAN}Toi > {RESET}").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nFin de la session.")
                break

            if not question:
                continue
            if question.lower() in {"quit", "exit", "q"}:
                print("À bientôt !")
                break

            # 1. Enregistre ton message dans messages_logs (pour la mémoire conversationnelle)
            db.add(MessageLog(
                id_utilisateur=user.id_utilisateur,
                role="user",
                contenu=question,
                type_media="texte",
            ))
            await db.commit()

                # 2. Génère la réponse via RAG + Gemini
            resultat = await generate_aec_response(
                utilisateur=user,
                user_input=question,
                session_db=db,
                media_type="texte",
            )
            reponse, tokens_in, tokens_out = resultat[0], resultat[1], resultat[2]
            

            # 3. Enregistre la réponse du bot dans messages_logs
            db.add(MessageLog(
                id_utilisateur=user.id_utilisateur,
                role="assistant",
                contenu=reponse,
                type_media="texte",
                tokens_entree=tokens_in,
                tokens_sortie=tokens_out,
            ))
            await db.commit()

            print(f"\n{MAGENTA}ChatAEC >{RESET} {reponse}")
            print(f"{GRIS}[Tokens : {tokens_in} in / {tokens_out} out]{RESET}\n")


if __name__ == "__main__":
    asyncio.run(boucle_discussion())