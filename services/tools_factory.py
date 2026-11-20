import os
from PIL import Image, ImageDraw, ImageFont

# ==========================================================
# 1. BANQUE DE DONNÉES / CONFIGURATION DES CANDIDATS
# ==========================================================
ANNUAIRE_CANDIDATS = {
    "Jean-Luc Mélenchon": {
        "nom_affichage": "L'Avenir en Commun",
        "photo": "photo/jlm_detour.png",
        "couleur": "#7b13d6"
    },
    "Édouard Philippe": {
        "nom_affichage": "Édouard Philippe",
        "photo": "images/ep_detour.png",
        "couleur": "#1976D2"
    },
    "Marine Le Pen": {
        "nom_affichage": "Rassemblement National",
        "photo": "images/mlp_detour.png",
        "couleur": "#331909"
    }
}


# ==========================================================
# 2. DÉFINITION DES OUTILS (FONCTIONS)
# ==========================================================
def generer_visuel_comparatif(candidat_gauche: str, prop_gauche: str, candidat_droite: str, prop_droite: str) -> str:
    """
    Génère une infographie visuelle comparative au format image entre deux programmes de candidats.
    Utilise cet outil lorsque l'utilisateur demande explicitement un visuel, une image comparative, 
    ou un support graphique à afficher/partager entre deux candidats.
    
    Args:
        candidat_gauche: Le nom exact du premier candidat (ex: 'Jean-Luc Mélenchon').
        prop_gauche: La proposition ou le résumé synthétique pour la gauche.
        candidat_droite: Le nom exact du deuxième candidat (ex: 'Édouard Philippe').
        prop_droite: La proposition ou le résumé synthétique pour la droite.
    """
    output_path = "outputs/comparatif.png"
    
    # ... le reste de ton code de génération PIL ...
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fond.save(output_path)
    return f"Visuel généré avec succès à l'emplacement : {output_path}"

# ==========================================================
# 3. REGISTRE DES OUTILS (MAPPING POUR EXÉCUTION DYNAMIQUE)
# ==========================================================
# C'est ici que tu ajoutes tes futurs outils au fur et à mesure
REGISTRE_OUTILS = {
    "generer_visuel_comparatif": generer_visuel_comparatif
}

# La liste que tu passes directement à l'API Gemini dans la config
LISTE_TOOLS_GEMINI = [
    generer_visuel_comparatif
]