"""
scraper.py — Lead scraper using DuckDuckGo HTML search + parsing.
No API key required. Extracts business info from search results.

For production: upgrade to SerpAPI, Apify, or Playwright browser automation.
"""

import httpx
import asyncio
import re
import json
from bs4 import BeautifulSoup
from database import add_lead
from agents import scorer
from config import MODELS


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


async def scrape_duckduckgo(query: str, max_results: int = 10) -> list[dict]:
    """
    Scrape DuckDuckGo HTML search for business listings.
    Returns raw business data extracted from search snippets.
    """
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    leads = []

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=HEADERS) as client:
            resp = await client.get(url)
            soup = BeautifulSoup(resp.text, "html.parser")
            results = soup.find_all("div", class_="result")

            for result in results[:max_results]:
                title_el = result.find("a", class_="result__a")
                snippet_el = result.find("a", class_="result__snippet")

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                link = title_el.get("href", "")

                # Extract phone from snippet
                phone = extract_phone(snippet)
                # Extract rating
                rating = extract_rating(snippet)

                lead = {
                    "name": clean_business_name(title),
                    "phone": phone,
                    "website": clean_url(link),
                    "rating": rating,
                    "reviews": extract_review_count(snippet),
                    "notes": snippet[:200],
                    "source": "duckduckgo",
                    "social": detect_social(link, snippet),
                }
                leads.append(lead)

    except Exception as e:
        print(f"Scrape error: {e}")

    return leads


async def scrape_justdial_style(category: str, location: str, max_results: int = 15) -> list[dict]:
    """
    Scrape Justdial-style results via search.
    """
    query = f"{category} {location} site:justdial.com OR site:sulekha.com"
    return await scrape_duckduckgo(query, max_results)


async def scrape_google_maps_style(category: str, location: str, max_results: int = 15) -> list[dict]:
    """
    Scrape Google Maps listings via search.
    Queries: "dentist Chalakudy Kerala reviews phone"
    """
    query = f"{category} {location} Kerala reviews contact phone"
    return await scrape_duckduckgo(query, max_results)


# ─── Helper parsers ──────────────────────────────────────────────────────────

def extract_phone(text: str) -> str:
    patterns = [
        r"\+91[\s-]?\d{5}[\s-]?\d{5}",
        r"0\d{2,4}[\s-]\d{6,8}",
        r"\b[6-9]\d{9}\b",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group().strip()
    return ""


def extract_rating(text: str) -> float:
    m = re.search(r"(\d\.?\d?)\s*/\s*5|rating[:\s]+(\d\.?\d?)|(\d\.?\d?)\s*star", text, re.I)
    if m:
        val = m.group(1) or m.group(2) or m.group(3)
        try:
            return round(float(val), 1)
        except Exception:
            pass
    return 0.0


def extract_review_count(text: str) -> int:
    m = re.search(r"(\d[\d,]+)\s*review", text, re.I)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0


def clean_business_name(title: str) -> str:
    # Remove common suffixes from search result titles
    title = re.sub(r"\s*[-|–]\s*(Justdial|Sulekha|Google|Maps|IndiaMART).*", "", title, flags=re.I)
    return title.strip()[:100]


def clean_url(url: str) -> str:
    if not url or url.startswith("//duckduckgo"):
        return ""
    if url.startswith("http"):
        # Check if it's a known directory site (not the business's own site)
        known_dirs = ["justdial", "sulekha", "indiamart", "tradeindia", "google.com"]
        if any(d in url for d in known_dirs):
            return ""
    return url[:200]


def detect_social(url: str, snippet: str) -> str:
    combined = (url + snippet).lower()
    if "instagram" in combined:
        return "instagram"
    if "facebook" in combined:
        return "facebook"
    if "twitter" in combined or "x.com" in combined:
        return "twitter"
    return "none"


# ─── Main scraping pipeline ──────────────────────────────────────────────────

async def scrape_and_save(
    category: str,
    location: str,
    max_results: int = 20,
    auto_score: bool = True,
) -> dict:
    """
    Full pipeline: scrape → deduplicate → score → save to DB.
    Returns summary stats.
    """
    print(f"🔍 Scraping: {category} in {location}...")

    # Run multiple search strategies in parallel
    results = await asyncio.gather(
        scrape_google_maps_style(category, location, max_results // 2),
        scrape_justdial_style(category, location, max_results // 2),
        return_exceptions=True
    )

    all_leads = []
    for r in results:
        if isinstance(r, list):
            all_leads.extend(r)

    # Deduplicate by name similarity
    seen_names = set()
    unique = []
    for lead in all_leads:
        key = lead["name"].lower().strip()[:20]
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


# ─── Manual lead import ──────────────────────────────────────────────────────

SAMPLE_LEADS = [
    {"name": "Sri Krishna Restaurant", "category": "Restaurant", "phone": "+91 98456 11111",
     "rating": 3.6, "reviews": 210, "website": "", "social": "inactive",
     "location": "Chalakudy, Kerala", "source": "Google Maps",
     "notes": "Popular locally, no online ordering or website"},

    {"name": "Dr. Anoop Dental Clinic", "category": "Clinic", "phone": "+91 94471 22222",
     "rating": 4.1, "reviews": 87, "website": "", "social": "none",
     "location": "Thrissur, Kerala", "source": "Justdial",
     "notes": "Good reviews but zero online presence"},

    {"name": "FitZone Gym", "category": "Gym", "phone": "+91 80123 33333",
     "rating": 3.9, "reviews": 145, "website": "https://fitzonegym.in",
     "social": "low", "location": "Ernakulam, Kerala",
     "notes": "Website outdated — last updated 2021"},

    {"name": "Glamour Salon & Spa", "category": "Salon", "phone": "+91 97446 44444",
     "rating": 4.4, "reviews": 302, "website": "", "social": "active",
     "location": "Palakkad, Kerala",
     "notes": "Great Instagram but no bookings page, misses walk-ins"},

    {"name": "Manohar Builders", "category": "Builder", "phone": "+91 94003 55555",
     "rating": 3.2, "reviews": 45, "website": "", "social": "none",
     "location": "Kozhikode, Kerala",
     "notes": "Low rating, unanswered negative reviews, no digital presence"},

    {"name": "Family Care Clinic", "category": "Clinic", "phone": "+91 98001 66666",
     "rating": 3.8, "reviews": 178, "website": "", "social": "none",
     "location": "Chalakudy, Kerala",
     "notes": "Busy clinic, no appointment booking, patients complain about wait times"},

    {"name": "Spice Garden Restaurant", "category": "Restaurant", "phone": "+91 91234 77777",
     "rating": 3.4, "reviews": 89, "website": "", "social": "inactive",
     "location": "Thrissur, Kerala",
     "notes": "No menu online, no Swiggy/Zomato listing"},

    {"name": "Prime Fitness Center", "category": "Gym", "phone": "+91 88765 88888",
     "rating": 4.0, "reviews": 203, "website": "", "social": "none",
     "location": "Ernakulam, Kerala",
     "notes": "200+ reviews, no website, loses leads to competitors daily"},
]


async def load_sample_leads() -> int:
    """Load sample leads into the database with scores."""
    count = 0
    for lead in SAMPLE_LEADS:
        score_result = scorer.calculate_score(lead)
        lead["score"] = score_result["score"]
        await add_lead(lead)
        count += 1
    return count
