# scrape_listings.py - Phase 1: Scraper les annonces Airbnb
import os, csv, re, time, datetime
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SEARCH_URLS_FILE = "search_urls.txt"
MAX_LIST_PER_URL = int(os.getenv("MAX_LISTINGS", "20"))
MAX_MINUTES = float(os.getenv("MAX_MINUTES", "15"))
OUT_CSV = "airbnb_results.csv"

def now_iso():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()

def read_search_urls():
    """Lire les URLs depuis search_urls.txt"""
    if not os.path.exists(SEARCH_URLS_FILE):
        raise FileNotFoundError(f"❌ Fichier {SEARCH_URLS_FILE} introuvable!")
    
    with open(SEARCH_URLS_FILE, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    if not urls:
        raise ValueError(f"❌ Aucune URL valide dans {SEARCH_URLS_FILE}")
    
    print(f"✅ {len(urls)} page(s) de recherche à traiter")
    return urls

def write_csv(rows, path=OUT_CSV):
    header = ["url", "title", "license_code", "host_profile_url", "scraped_at"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})

def click_if_present(page, selector, timeout=3000):
    try:
        el = page.locator(selector).first
        el.wait_for(state="visible", timeout=timeout)
        el.click()
        return True
    except:
        return False

def get_text_safe(loc, timeout=2500):
    try:
        return loc.inner_text(timeout=timeout).strip()
    except:
        return ""

def goto_search_with_retry(page, url):
    """Navigate vers une page de recherche"""
    for attempt in range(3):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            click_if_present(page, 'button:has-text("Accepter")', 4000) or \
            click_if_present(page, 'button:has-text("I agree")', 4000) or \
            click_if_present(page, 'button:has-text("Accept")', 4000)
            page.wait_for_selector('a[href^="/rooms/"]', timeout=30000)
            print(f"  ✓ Navigation réussie")
            return True
        except Exception as e:
            if attempt < 2:
                print(f"  ⚠️ Tentative {attempt + 1} échouée, retry...")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                except:
                    pass
            else:
                print(f"  ❌ Échec après 3 tentatives: {e}")
                return False
    return False

def collect_listing_urls(page, search_url, max_items):
    """Collecte les URLs d'annonces depuis UNE page de recherche"""
    if not goto_search_with_retry(page, search_url):
        return []
    
    seen = set()
    last_h = 0
    start = time.time()
    
    print(f"  🔍 Collecte des annonces...")
    
    # Scroll et collecte (max 2 minutes par page)
    while len(seen) < max_items and (time.time() - start) < 120:
        for a in page.locator('a[href^="/rooms/"]').all():
            try:
                href = a.get_attribute("href") or ""
                if not href or "experiences" in href:
                    continue
                full = urljoin(page.url, href.split("?")[0])
                if "/rooms/" in full:
                    seen.add(full)
                    if len(seen) >= max_items:
                        break
            except:
                continue
        
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        page.wait_for_timeout(700)
        h = page.evaluate("document.body.scrollHeight")
        if h == last_h:
            break
        last_h = h
    
    urls = list(seen)[:max_items]
    print(f"  ✅ {len(urls)} annonces trouvées")
    return urls

RE_LICENSES = [
    re.compile(r"\b[A-Z]{3}-[A-Z]{3}-[A-Z0-9]{4,6}\b"),
    re.compile(r"\b\d{5,8}\b"),
    re.compile(r"\b[A-Z0-9]{5,}\b"),
]

LABEL_PATTERNS = [
    "Infos d'enregistrement", "Détails de l'enregistrement",
    "Registration details", "License", "Licence", "Permit"
]

def extract_license_code(page):
    opened = (
        click_if_present(page, 'button:has-text("Lire la suite")') or
        click_if_present(page, 'button:has-text("Read more")') or
        click_if_present(page, 'button:has-text("Afficher plus")')
    )
    
    text_scope = ""
    if opened:
        try:
            dlg = page.locator('[role="dialog"], [aria-modal="true"]').first
            dlg.wait_for(state="visible", timeout=3000)
            text_scope = get_text_safe(dlg, timeout=3000)
        except:
            pass
    
    if not text_scope:
        text_scope = get_text_safe(page.locator("body"), timeout=6000)
    
    if any(lbl in text_scope for lbl in LABEL_PATTERNS):
        for lbl in LABEL_PATTERNS:
            i = text_scope.find(lbl)
            if i >= 0:
                text_scope = text_scope[i:i+800]
                break
    
    for rx in RE_LICENSES:
        m = rx.search(text_scope)
        if m:
            return m.group(0)
    return ""

def find_host_url(page, listing_url):
    """Trouve l'URL du profil hôte"""
    try:
        for _ in range(3):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(250)
        
        all_links = page.locator('a[href*="/users/profile/"], a[href*="/users/show/"]').all()
        
        if all_links:
            for link in all_links:
                try:
                    href = link.get_attribute("href")
                    if href and ("/users/profile/" in href or "/users/show/" in href):
                        return urljoin(listing_url, href.split("?")[0])
                except:
                    continue
    except Exception as e:
        print(f"    ⚠️ Erreur extraction host URL: {e}")
    
    return ""

def parse_listing(page, url):
    data = {
        "url": url,
        "title": "",
        "license_code": "",
        "host_profile_url": "",
        "scraped_at": now_iso()
    }
    
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(600)
        
        try:
            data["title"] = page.locator('meta[property="og:title"]').first.get_attribute("content") or ""
        except:
            data["title"] = get_text_safe(page.locator("h1"))
        
        data["license_code"] = extract_license_code(page)
        data["host_profile_url"] = find_host_url(page, url)
        
    except Exception as e:
        print(f"    ❌ Erreur: {e}")
    
    return data

def main():
    print("\n🚀 PHASE 1 : SCRAPING DES ANNONCES")
    print("=" * 60)
    
    # Lire les URLs de recherche depuis le fichier
    search_urls = read_search_urls()
    
    all_listings = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="fr-FR",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 1600},
            timezone_id="Europe/Paris",
        )
        page = context.new_page()
        
        # Bloquer images/fonts
        page.route("**/*", lambda route: route.abort() 
                   if route.request.resource_type in ["image", "font", "media"]
                   else route.continue_())
        
        # POUR CHAQUE PAGE DE RECHERCHE
        for page_idx, search_url in enumerate(search_urls, 1):
            print(f"\n{'='*60}")
            print(f"📄 PAGE {page_idx}/{len(search_urls)}")
            print(f"🔗 {search_url}")
            print(f"{'='*60}")
            
            # Collecter les URLs d'annonces
            listing_urls = collect_listing_urls(page, search_url, MAX_LIST_PER_URL)
            
            if not listing_urls:
                print(f"  ⚠️ Aucune annonce trouvée, passage à la page suivante")
                continue
            
            # Scraper chaque annonce
            print(f"\n  📋 Scraping de {len(listing_urls)} annonces...\n")
            for i, listing_url in enumerate(listing_urls, 1):
                print(f"  [{i}/{len(listing_urls)}] {listing_url}")
                result = parse_listing(page, listing_url)
                all_listings.append(result)
                print(f"    ✓ {result['title'][:50] if result['title'] else 'N/A'}...")
                print(f"    ✓ Licence: {result['license_code'] or 'N/A'}")
                print(f"    ✓ Host: {result['host_profile_url'][:50] if result['host_profile_url'] else 'N/A'}...")
            
            # Pause entre pages
            if page_idx < len(search_urls):
                print(f"\n  ⏳ Pause 5s avant la page suivante...")
                time.sleep(5)
        
        write_csv(all_listings)
        print(f"\n{'='*60}")
        print(f"✅ PHASE 1 TERMINÉE")
        print(f"📊 {len(all_listings)} annonces sauvegardées dans {OUT_CSV}")
        print(f"{'='*60}\n")
        
        context.close()
        browser.close()

if __name__ == "__main__":
    main()
