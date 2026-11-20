import requests
from bs4 import BeautifulSoup

URL_PLAN = "https://melenchon2027.fr/plans-thematiques/pauvrete//"
FICHIER_SORTIE = "plan_pauvrete.md"

def extraire_page_en_markdown(url: str, output_file: str):
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Cibler le corps principal de l'article WordPress
    contenu = soup.find("main") or soup.find("article") or soup.body

    # Supprimer les éléments parasites (nav, footer, formulaires, scripts)
    for tag in contenu.find_all(["nav", "footer", "script", "style", "form", "aside"]):
        tag.decompose()

    lignes = [f"Source : {url}\n"]

    for el in contenu.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        texte = el.get_text(" ", strip=True)
        if not texte or len(texte) < 3:
            continue

        if el.name == "h1":
            lignes.append(f"\n---\n# {texte}\nSource : {url}\n")
        elif el.name == "h2":
            lignes.append(f"\n## {texte}\n")
        elif el.name in ["h3", "h4"]:
            lignes.append(f"\n### {texte}\n")
        elif el.name == "li":
            lignes.append(f"- {texte}")
        else:
            lignes.append(f"{texte}\n")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))

    print(f"✅ Fichier '{output_file}' généré avec succès.")

if __name__ == "__main__":
    extraire_page_en_markdown(URL_PLAN, FICHIER_SORTIE)