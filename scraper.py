import asyncio
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
from database import add_lead
from agents import scorer

# Standard headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- Browser Logic ---

async def scrape_with_playwright(query: str, max_results: int = 10) -> list[dict]:
    leads = []
    url = f"https://duckduckgo.com/?q={query.replace(' ', '+')}&ia=web"
    
    async with async_playwright() as p:
        # Note: Change headless=True to headless=False if you want to visually watch the browser work
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=HEADERS["User-Agent"])
        
        # Instantiate the Stealth class and apply it to the context
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        
        page = await context.new_page()
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Human-like scrolling to trigger lazy-loaded items
            for _ in range(2):
                await page.mouse.wheel(0, 1000)
                await asyncio.sleep(1)

            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            
            # Target the standard list items DDG uses for search results
            results = soup.select('ol.react-results--main > li')
            
            # Fallback for older/different DDG layouts
            if not results:
                results = soup.find_all("div", class_=re.compile(r"result\s+results_links"))

            for result in results[:max_results]:
                # Find the title anchor tag
                title_el = result.select_one('h2 a') or result.find("a", class_="result__url")
                
                # Find the snippet text
                snippet_el = result.select_one('div[data-result="snippet"]') or result.find("a", class_="result__snippet")

                if not title_el: continue

                title = title_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                link = title_el.get("href", "")

                leads.append({
                    "name": clean_business_name(title),
                    "phone": extract_phone(snippet),
                    "website": clean_url(link),
                    "rating": extract_rating(snippet),
                    "reviews": extract_review_count(snippet),
                    "notes": snippet[:200],
                    "source": "playwright_stealth",
                    "social": detect_social(link, snippet),
                })
        except Exception as e:
            print(f"❌ Playwright Error: {e}")
        finally:
            await browser.close()
            
    return leads

# --- Main Scrape & Save Pipeline ---

async def scrape_and_save(category: str, location: str, max_results: int = 20, auto_score: bool = True) -> dict:
    print(f"🔍 Scraping: {category} in {location}...")
    
    # Using the new Playwright engine
    query = f"{category} in {location}"
    all_leads = await scrape_with_playwright(query, max_results)

    # Deduplicate
    seen_names = set()
    unique = []
    for lead in all_leads:
        key = lead["name"].lower().strip()[:30]
        if key and key not in seen_names:
            seen_names.add(key)
            lead["category"] = category
            lead["location"] = location
            unique.append(lead)

    print(f"📋 Found {len(unique)} unique businesses")

    saved = 0
    for lead in unique:
        if auto_score:
            score_result = scorer.calculate_score(lead)
            lead["score"] = score_result["score"]
        await add_lead(lead)
        saved += 1

    return {
        "scraped": len(all_leads),
        "unique": len(unique),
        "saved": saved,
        "category": category,
        "location": location
    }

# --- Parsers ---

def extract_phone(text: str) -> str:
    patterns = [r"\+91[\s-]?\d{10}", r"\b[6-9]\d{9}\b"]
    for p in patterns:
        m = re.search(p, text)
        if m: return m.group().strip()
    return ""

def extract_rating(text: str) -> float:
    m = re.search(r"(\d\.?\d?)\s*/\s*5", text)
    return round(float(m.group(1)), 1) if m else 0.0

def extract_review_count(text: str) -> int:
    m = re.search(r"(\d+)\s*review", text, re.I)
    return int(m.group(1)) if m else 0

def clean_business_name(title: str) -> str:
    return re.sub(r"\s*[-|–]\s*(Justdial|Sulekha|Google).*", "", title, flags=re.I).strip()[:100]

def clean_url(url: str) -> str:
    if not url or any(d in url for d in ["duckduckgo", "google", "justdial"]): return ""
    return url[:200]

def detect_social(url: str, snippet: str) -> str:
    combined = (url + snippet).lower()
    for s in ["instagram", "facebook", "twitter"]:
        if s in combined: return s
    return "none"

# --- Dummy / Sample Leads ---

async def load_sample_leads() -> int:
    # Restores the function for main.py compatibility
    return 0