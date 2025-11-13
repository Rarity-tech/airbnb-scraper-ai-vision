# scrape_listings.py - Phase 1: Scraper les annonces Airbnb
import os, csv, re, time, datetime
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

START_URL = os.getenv("START_URL", "https://www.airbnb.com/s/Dubai/homes")
MAX_LIST = int(os.getenv("MAX_LISTINGS", "20"))
MAX_MINUTES = float(os.getenv("MAX_MINUTES", "15"))
OUT_CSV = "airbnb_results.csv"

def now_iso():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()

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

def goto_search_with_retry(page):
    candidates = [START_URL]
    last_err = None
    
    for url in candidates:
        for _ in range(2):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                click_if_present(page, 'button:has-text("Accepter")', 4000) or \
                click_if_present(page, 'button:has-text("I agree")', 4000) or \
                click_if_present(page, 'button:has-text("Accept")', 4000)
                page.wait_for_selector('a[href^="/rooms/"]', timeout=30000)
                print(f"✓ Navigation réussie")
                return
            except Exception as e:
                last_err = e
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                except:
                    pass
    raise last_err if last_err else RuntimeError("Navigation failed")

def collect_listing_urls(page, max_items, max_minutes):
    goto_search_with_retry(page)
    
    start = time.time()
    seen = set()
    last_h = 0
    
    print(f"🔍 Collecte des URLs d'annonces...")
    
    while len(seen) < max_items and (time.time() - start) < (max_minutes * 60):
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
    print(f"✅ Trouvé {len(urls)} annonces")
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
        # Scroll pour charger le bloc hôte
        for _ in range(3):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(250)
        
        # Chercher liens /users/profile/ ou /users/show/
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
        print(f"⚠️ Erreur extraction host URL: {e}")
    
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
        
        # Titre
        try:
            data["title"] = page.locator('meta[property="og:title"]').first.get_attribute("content") or ""
        except:
            data["title"] = get_text_safe(page.locator("h1"))
        
        # Licence
        data["license_code"] = extract_license_code(page)
        
        # URL hôte
        data["host_profile_url"] = find_host_url(page, url)
        
    except Exception as e:
        print(f"❌ Erreur pour {url}: {e}")
    
    return data

def main():
    print("\n🚀 PHASE 1 : SCRAPING DES ANNONCES")
    print("=" * 60)
    
    rows = []
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
        
        urls = collect_listing_urls(page, MAX_LIST, MAX_MINUTES)
        
        print(f"\n📋 Scraping de {len(urls)} annonces...")
        for i, u in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] {u}")
            result = parse_listing(page, u)
            rows.append(result)
            print(f"  ✓ Titre: {result['title'][:50] if result['title'] else 'N/A'}...")
            print(f"  ✓ Licence: {result['license_code'] or 'Non trouvée'}")
            print(f"  ✓ Host URL: {result['host_profile_url'][:50] if result['host_profile_url'] else 'Non trouvée'}...")
        
        write_csv(rows)
        print(f"\n✅ {len(rows)} annonces sauvegardées dans {OUT_CSV}")
        
        context.close()
        browser.close()

if __name__ == "__main__":
    main()
