import asyncio
from playwright.async_api import async_playwright

URL_CIBLE = "https://www.edouardphilippe.fr/programme"
OUTPUT_FILE = "programme_edouard_philippe.md"

async def scraper_programme_integral():
    async with async_playwright() as p:
        # Lance le navigateur (mets headless=False si tu veux voir le navigateur cliquer tout seul à l'écran)
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        print(f"🌐 Navigation sur : {URL_CIBLE}")
        await page.goto(URL_CIBLE, wait_until="networkidle")
        
        # 1. Clique de façon répétée sur les boutons "Voir plus" ou "Charger plus" / accordéons s'il y en a
        print("🖱️ Recherche et clic sur les boutons 'Voir plus' ou accordéons...")
        for i in range(15): # Boucle de sécurité pour s'assurer de tout ouvrir
            try:
                # Cible large pour attraper les boutons de chargement ou les menus repliés
                boutons = page.locator("button:has-text('Voir plus'), button:has-text('Afficher'), summary, .accordion-toggle")
                count = await boutons.count()
                
                if count > 0:
                    clicked = False
                    for j in range(count):
                        btn = boutons.nth(j)
                        if await btn.is_visible():
                            await btn.click()
                            await page.wait_for_timeout(800) # Laisse le temps au DOM de charger le contenu déplié
                            clicked = true
                    if not clicked:
                        break
                else:
                    break
            except Exception:
                break

        # 2. Extraction ciblée du contenu structuré (Titres et paragraphes/listes de propositions)
        print("📄 Extraction minutieuse du contenu de la page...")
        
        donnees_extraites = await page.evaluate("""() => {
            let markdown = [];
            // On cible les grands blocs thématiques de la page
            let sections = document.querySelectorAll('section, article, div.theme-block, div.proposal-card, main div');
            
            if (sections.length === 0) {
                sections = [document.body];
            }

            sections.forEach(sec => {
                let titreEl = sec.querySelector('h1, h2, h3, h4');
                let titre = titreEl ? titreEl.innerText.trim() : "Mesures générales";
                
                markdown.push(`\\n## ${titre}\\n`);

                let items = sec.querySelectorAll('p, li, span.description');
                items.forEach(item => {
                    let texte = item.innerText.trim();
                    // On filtre les textes trop courts ou les menus de navigation parasites
                    if (texte.length > 30 && !texte.includes("Cookies") && !texte.includes("Connexion")) {
                        markdown.push(`- ${texte}`);
                    }
                });
            });

            return markdown.join('\\n');
        }""")

        await browser.close()
        return donnees_extraites

if __name__ == "__main__":
    contenu_brut = asyncio.run(scraper_programme_integral())
    
    # En-tête propre pour ton fichier Markdown
    en_tete_md = f"# Programme : Édouard Philippe 2027\nSource officielle : {URL_CIBLE}\n\n---\n\n"
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(en_tete_md + contenu_brut)
        
    print(f"✅ Fichier Markdown généré avec succès : {OUTPUT_FILE}")