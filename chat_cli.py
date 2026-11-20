import asyncio
import os
from dotenv import load_dotenv

# Charge les variables du fichier .env (GEMINI_API_KEY, SUPABASE_URL, etc.)
load_dotenv()

# Importe ta fonction principale et tes modèles depuis ton main.py
from main import generate_aec_response
from models import Utilisateur

# Classe factice simulant l'utilisateur connecté en local
class UtilisateurCLI(Utilisateur):
    def __init__(self):
        # Initialise les attributs minimaux nécessaires pour ton bot
        self.id_utilisateur = 1
        self.prenom_eleve = "Alexis"

async def main():
    utilisateur = UtilisateurCLI()
    
    # session_db=None : pas d'écriture en base, mais le RAG s'exécute de son côté 
    # (via son client Supabase ou ses propres requêtes de lecture)
    session_db_fictive = None 
    
    print("=" * 60)
    print("🤖 CHATAEC - MODE CLI LOCAL (RAG Actif / Lecture seule)")
    print("Tape 'exit' ou 'quit' pour quitter.")
    print("=" * 60)
    
    while True:
        try:
            user_input = input("\n👤 Toi : ").strip()
            
            if user_input.lower() in ["exit", "quit"]:
                print("👋 Fin du test local. À plus !")
                break
                
            if not user_input:
                continue
                
            print("\n⏳ Interrogation du RAG et analyse des outils...")
            
            # En mettant is_group=True, tu évites l'écriture des messages dans la table d'historique,
            # tout en permettant au RAG d'aller chercher les chunks dans Supabase en lecture !
            reponse, est_vocal, p_tokens, c_tokens, texte_transcrit = await generate_aec_response(
                utilisateur=utilisateur,
                user_input=user_input,
                session_db=session_db_fictive,
                media_type="texte",
                is_group=True 
            )
            
            print(f"\n🤖 ChatAEC :\n{reponse}")
            print(f"\n[📊 Tokens -> Prompt: {p_tokens} | Réponse: {c_tokens}]")
            
        except KeyboardInterrupt:
            print("\n👋 Interruption. Sortie du CLI.")
            break
        except Exception as e:
            print(f"\n❌ Erreur technique : {e}")

if __name__ == "__main__":
    asyncio.run(main())