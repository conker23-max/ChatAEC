import asyncio
import os
from dotenv import load_dotenv

# Charge tes variables d'environnement locales (.env)
load_dotenv()

# Importe tes fonctions internes existantes
from services.rag_service import rechercher_passages_programme
from services.gemini_service import generate_aec_response # ou ta fonction d'appel LLM directe

# Tes 10 questions de test militantes (le "Golden Dataset")
QUESTIONS_TEST = [
    "Quelle est la stratégie exacte vis-à-vis des traités européens si nos partenaires refusent de négocier la dérogation ?",
    "Comment est structurée la progressivité de l'impôt sur le revenu avec le passage à 14 tranches ?",
    "Quelle est la trajectoire précise pour atteindre 100% d'énergies renouvelables et comment gère-t-on la transition du parc nucléaire existant ?",
    "Quel est le mécanisme juridique prévu pour donner un droit de veto ou de codirection aux salariés en cas de fermeture d'usine rentable ?",
    "Quels traités de libre-échange précis le programme s'engage-t-il à dénoncer immédiatement ?",
    "Que prévoit le programme concernant la surtaxe sur les logements vacants et l'interdiction des expulsions sans relogement ?",
    "Quel est le sort exact réservé au Sénat et comment s'organise l'élection de la Constituante ?",
    "Quelle est la mesure exacte concernant le remboursement à 100% par la Sécurité sociale et la fin des dépassements d'honoraires ?",
    "Quelles sont les dispositions spécifiques prévues pour l'autonomie énergétique et la continuité territoriale dans les Outre-mer ?",
    "Comment le programme répond-il à l'argument du 'coût de la transition' et de la hausse des prélèvements sur les superprofits ?"
]

async def lancer_banc_de_test():
    print("🚀 Lancement du banc de test de non-hallucination (RAG Check)...\n")
    
    for i, question in enumerate(QUESTIONS_TEST, 1):
        print(f"--------------------------------------------------")
        print(f"Test {i}/10 : {question}")
        print(f"--------------------------------------------------")
        
        try:
            # 1. Test du RAG (récupération des chunks Supabase)
            extraits = await rechercher_passages_programme(question)
            
            if not extraits:
                print("❌ ALERTE RAG : Aucun extrait trouvé dans Supabase pour cette question !")
                continue
            
            print(f"✅ RAG OK : {len(extraits)} extraits remontés.")
            
            # Affiche un petit aperçu du premier extrait pour voir si c'est pertinent
            # Au lieu de faire :
            # apercu = extraits[0].get("contenu", "")[:150]

            # Fais simplement ça :
            premier_extrait = extraits[0] if isinstance(extraits[0], str) else str(extraits[0])
            apercu = premier_extrait[:150].replace("\n", " ")
            print(f"📄 Extrait source #1 : \"{apercu}...\"")
            
            # 2. Tu peux simuler ou appeler ton LLM ici pour voir la réponse générée
            # bot_reponse, ... = await generate_aec_response(...)
            # print(f"🤖 Réponse IA : {bot_reponse[:200]}...")
            
        except Exception as e:
            print(f"❌ ERREUR CRITIQUE sur ce test : {e}")
        
        print("\n")

if __name__ == "__main__":
    asyncio.run(lancer_banc_de_test())