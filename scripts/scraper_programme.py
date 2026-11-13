import time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

START_URL = "https://melenchon2027.fr/programme/livre/"
ALLOWED_PREFIX = "https://melenchon2027.fr/programme/"
OUTPUT_FILE = "programme_aec.md"

visited = set()
to_visit = [START_URL]
pages_collectees = []


def nettoyer_url(url: str) -> str:
    parsed = urlparse(url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if not clean.endswith("/"):
        clean += "/"
    return clean


def extraire_contenu_propre(html_content: str) -> tuple[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")
    titre_tag = soup.find("h1")
    titre = titre_tag.get_text(strip=True) if titre_tag else "Section sans titre"

    zone_contenu = (
        soup.find("main")
        or soup.find("article")
        or soup.body
    )
    if not zone_contenu:
        return titre, ""

    for tag in zone_contenu(["nav", "header", "footer", "script", "style", "aside", "form", "button"]):
        tag.decompose()

    lignes = []
    for elem in zone_contenu.find_all(["h2", "h3", "h4", "p", "li"]):
        texte = elem.get_text(" ", strip=True)
        if not texte or len(texte) < 3:
            continue

        if elem.name == "h2":
            lignes.append(f"\n## {texte}\n")
        elif elem.name == "h3":
            lignes.append(f"\n### {texte}\n")
        elif elem.name == "h4":
            lignes.append(f"\n#### {texte}\n")
        elif elem.name == "li":
            lignes.append(f"- {texte}")
        else:
            lignes.append(texte)

    lignes_propres = []
    for l in lignes:
        if not lignes_propres or lignes_propres[-1] != l:
            lignes_propres.append(l)

    return titre, "\n\n".join(lignes_propres)


def crawler_avec_playwright():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        while to_visit:
            url_courante = nettoyer_url(to_visit.pop(0))
            if url_courante in visited:
                continue
            visited.add(url_courante)

            print(f"📥 Aspiration : {url_courante}")
            try:
                page.goto(url_courante, wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(800)  # Laisse le temps au JS de finaliser l'affichage

                html_rendu = page.content()
                titre, contenu = extraire_contenu_propre(html_rendu)

                if contenu.strip():
                    pages_collectees.append(
                        f"# {titre}\nSource : {url_courante}\n\n{contenu}"
                    )

                # Récupération de tous les liens rendus dans le DOM
                hrefs = page.eval_on_selector_all(
                    "a[href]", "elements => elements.map(e => e.getAttribute('href'))"
                )
                for href in hrefs:
                    if not href:
                        continue
                    url_absolue = nettoyer_url(urljoin(url_courante, href))
                    if (
                        url_absolue.startswith(ALLOWED_PREFIX)
                        and url_absolue not in visited
                        and url_absolue not in to_visit
                    ):
                        to_visit.append(url_absolue)

            except Exception as e:
                print(f"❌ Erreur sur {url_courante} : {e}")

        browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n\n---\n\n".join(pages_collectees))

    print(f"\n✅ Terminé ! {len(pages_collectees)} pages compilées dans '{OUTPUT_FILE}'.")


if __name__ == "__main__":
    crawler_avec_playwright()