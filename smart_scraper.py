import asyncio
import json
import re
import httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from urllib.parse import quote_plus

# ── CONFIG ──────────────────────────────────────────────────────────────────
OLLAMA_URL  = "http://127.0.0.1:11434/api/generate"
MODEL       = "qwen3:8b"
TARGET      = 20
# ────────────────────────────────────────────────────────────────────────────

# ── SEARCH: Bing instead of DuckDuckGo (Bing renders results without JS challenges)
BING_BASE = "https://www.bing.com/search?q={q}&count=10"

SEED_SEARCHES = [
    "site:instamojo.com/store ebook India buy",
    "site:gumroad.com India digital download course",
    "site:topmate.io India creator consultation",
    "site:graphy.com Indian online course creator",
    "site:teachable.com India course buy",
    "site:pages.razorpay.com digital course India",
    "India small business sell ebook template \"buy now\"",
    "India digital creator sell course \"link in bio\"",
    "site:payhip.com India digital product sell",
    "Indian freelancer sell design template gumroad OR payhip OR instamojo",
]

# Direct marketplace listing pages — SPA-aware with fallback selectors
SEED_URLS = [
    "https://www.instamojo.com/marketplace/",
    "https://topmate.io/explore/category/all",
    "https://payhip.com/discover",                  # static — easy to scrape
    "https://gumroad.com/discover",
    "https://pages.razorpay.com/stores/",
    "https://www.graphy.com/explore",
]

# ── REGEX ────────────────────────────────────────────────────────────────────
EMAIL_RE  = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE  = re.compile(r"(?:\+91[\s\-]?|(?<!\d)0)?[6-9]\d{9}(?!\d)")
WA_RE     = re.compile(r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)[/\?]?(\d+)", re.I)
INSTA_RE  = re.compile(r"(?:instagram\.com|instagr\.am)/([A-Za-z0-9_.]{2,30})/?", re.I)

# Regex to find seller profile URLs directly in page HTML (fallback when LLM fails)
SELLER_URL_PATTERNS = [
    re.compile(r"https?://[a-zA-Z0-9\-]+\.gumroad\.com(?:/[^\s\"'<>]*)?"),
    re.compile(r"https?://(?:www\.)?instamojo\.com/[a-zA-Z0-9_\-]{3,}/(?!marketplace|blog|pricing|features|login|signup)"),
    re.compile(r"https?://[a-zA-Z0-9\-]+\.graphy\.com(?:/[^\s\"'<>]*)?"),
    re.compile(r"https?://[a-zA-Z0-9\-]+\.teachable\.com(?:/[^\s\"'<>]*)?"),
    re.compile(r"https?://topmate\.io/([a-zA-Z0-9_\-]{3,})(?:/[^\s\"'<>]*)?"),
    re.compile(r"https?://payhip\.com/([a-zA-Z0-9_\-]{3,})"),
    re.compile(r"https?://pages\.razorpay\.com/[a-zA-Z0-9_\-]{3,}"),
]

JUNK_EMAIL = [".png",".jpg",".gif",".svg",".js",".css","example","sentry",
              "noreply","no-reply","domain.com","youremail","email@","user@"]
JUNK_DOMAINS = [
    "bing.com","google.com","linkedin.com","facebook.com","twitter.com",
    "crunchbase.com","tracxn.com","wikipedia.org","yourstory.com","techcrunch.com",
    "inc42.com","entrackr.com","medium.com","instagram.com","youtube.com",
    "t.me","x.com","apple.com","microsoft.com","amazon.com",
]
INSTA_SKIP = {"p","reel","reels","explore","accounts","stories","tv","ar",
              "shoppingbag","legal","about","help","hashtag","direct"}
CONTACT_PATHS = ["/contact","/contact-us","/about","/about-us","/team","/support","/reach-us"]

# Domains that are marketplace homes — skip them as seller URLs
MARKETPLACE_HOME = {
    "gumroad.com","instamojo.com","graphy.com","teachable.com",
    "topmate.io","payhip.com","pages.razorpay.com",
}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def is_real_seller_url(url: str) -> bool:
    """True if URL looks like an individual seller page (not a marketplace home)."""
    if not url or not url.startswith("http"):
        return False
    # Strip to host
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lstrip("www.")
        # Must NOT be a plain marketplace home
        if host in MARKETPLACE_HOME:
            return False
        # Subdomain seller URLs (e.g. xyz.gumroad.com)
        for mkt in MARKETPLACE_HOME:
            if host.endswith("." + mkt) and host != mkt:
                return True
        # Path-based seller URLs (e.g. instamojo.com/xyz/, topmate.io/xyz)
        path = urlparse(url).path.strip("/")
        parts = [p for p in path.split("/") if p]
        if parts and len(parts[0]) >= 3:
            return True
    except Exception:
        pass
    return False


def is_junk_domain(url: str) -> bool:
    return any(d in url for d in JUNK_DOMAINS)


def clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return ""


# ── SAFE NAV ─────────────────────────────────────────────────────────────────

async def safe_goto(page, url: str, timeout=25000, settle=2.0) -> bool:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        await asyncio.sleep(settle)
        return True
    except PWTimeout:
        print(f"  [timeout] {url[:80]}")
        return False
    except Exception:
        print(f"  [nav error] {url[:80]}")
        return False


async def wait_for_content(page, timeout=12000) -> None:
    """
    For SPAs: wait until body has meaningful text.
    Falls back gracefully if nothing loads.
    """
    try:
        # Wait for any of these common content markers
        await page.wait_for_function(
            "() => document.body.innerText.trim().length > 200",
            timeout=timeout
        )
    except PWTimeout:
        pass  # Use whatever rendered


# ── PAGE READER ───────────────────────────────────────────────────────────────

async def get_page_text(page, chars=5000) -> str:
    await wait_for_content(page)
    try:
        return await page.evaluate(f"() => document.body.innerText.slice(0, {chars})")
    except Exception:
        return ""


async def get_page_html(page) -> str:
    try:
        return await page.evaluate("() => document.documentElement.innerHTML.slice(0, 80000)")
    except Exception:
        return ""


async def get_page_links(page) -> list[dict]:
    try:
        return await page.evaluate("""() => {
            const links = [];
            document.querySelectorAll('a[href]').forEach(el => {
                const href = el.href || '';
                const text = (el.innerText || el.title || '').trim().slice(0, 100);
                if (href.startsWith('http') && text && href.length < 300) {
                    links.push({text, href});
                }
            });
            return links.slice(0, 150);
        }""")
    except Exception:
        return []


# ── REGEX FALLBACK SELLER EXTRACTOR ──────────────────────────────────────────

def regex_extract_seller_urls(html: str) -> list[str]:
    """
    Scan raw HTML for known marketplace seller URLs.
    This is the fallback when the LLM returns nothing.
    """
    found = set()
    for pattern in SELLER_URL_PATTERNS:
        for m in pattern.findall(html):
            url = m if m.startswith("http") else f"https://topmate.io/{m}"
            if is_real_seller_url(url):
                # Normalize: strip query string and fragment
                url = url.split("?")[0].split("#")[0].rstrip("/")
                found.add(url)
    return list(found)


def guess_name_from_url(url: str) -> str:
    """Extract a readable name from a seller URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path.strip("/").split("/")[0] if parsed.path.strip("/") else ""

    # Subdomain seller: xyz.gumroad.com → "Xyz"
    for mkt in MARKETPLACE_HOME:
        sub = host.replace("." + mkt, "").replace("www.", "")
        if sub and sub != host:
            return sub.replace("-", " ").replace("_", " ").title()

    # Path seller: topmate.io/rahul_sharma → "Rahul Sharma"
    if path:
        return path.replace("-", " ").replace("_", " ").title()

    return host


def guess_category_from_url(url: str) -> str:
    if "gumroad" in url:       return "digital downloads"
    if "instamojo" in url:     return "digital products"
    if "graphy" in url:        return "online courses"
    if "teachable" in url:     return "online courses"
    if "topmate" in url:       return "creator services"
    if "payhip" in url:        return "digital products"
    if "razorpay" in url:      return "digital products"
    return "digital products"


# ── LLM ──────────────────────────────────────────────────────────────────────

async def ask_llm(prompt: str) -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": 12000, "num_predict": 800},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(OLLAMA_URL, json=payload)
        r.raise_for_status()
        return r.json()["response"]


async def llm_extract_sellers(page_text: str, links: list[dict], source_url: str) -> list[dict]:
    """Ask LLM to identify real SME digital seller profiles."""
    if not page_text.strip():
        return []

    links_str = "\n".join(
        f"  {l['href']}  |  {l['text']}"
        for l in links[:60]
        if not is_junk_domain(l["href"])
    )

    prompt = f"""You are a B2B lead extractor. Analyse the page content from {source_url}.

Find INDIAN SMALL BUSINESSES or CREATORS who SELL digital products
(online courses, ebooks, templates, coaching, design assets, software tools).

Rules:
- Only include REAL seller/creator profile or store pages — not news articles, not how-to guides, not marketplace homepages.
- The "website" must be the seller's OWN URL (e.g. https://johndoe.graphy.com or https://instamojo.com/abc/).
- Ignore links to homepages: instamojo.com, graphy.com, gumroad.com (without subpath), google.com etc.
- Return ONLY a JSON array. If you find 0 sellers, return [].

PAGE TEXT:
{page_text[:3000]}

LINKS ON PAGE:
{links_str}

Return JSON array:
[
  {{"name": "Seller Name", "website": "https://...", "category": "product type"}}
]

ONLY JSON. No explanation."""

    raw = await ask_llm(prompt)
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            results = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                name    = str(item.get("name", "")).strip()
                website = str(item.get("website", "")).strip()
                cat     = str(item.get("category", "")).strip()
                if name and website.startswith("http") and is_real_seller_url(website):
                    results.append({"name": name, "website": website, "category": cat})
            return results
    except Exception:
        pass
    return []


# ── SELLER DISCOVERY ─────────────────────────────────────────────────────────

def merge_seller(seen: dict, name: str, website: str, category: str) -> bool:
    """Add seller to seen dict. Returns True if new."""
    # Deduplicate by website domain/path
    website = website.rstrip("/")
    for existing in seen.values():
        if existing["website"].rstrip("/") == website:
            return False
    if name not in seen:
        seen[name] = {
            "website": website, "category": category,
            "email": "", "phone": "", "whatsapp": "", "instagram": "",
        }
        return True
    return False


async def discover_from_page(page, seen: dict, source_url: str) -> int:
    """
    Scroll, extract text+links, try LLM then regex fallback.
    Returns count of new sellers added.
    """
    for _ in range(4):
        await page.mouse.wheel(0, 1200)
        await asyncio.sleep(1.0)

    text  = await get_page_text(page)
    html  = await get_page_html(page)
    links = await get_page_links(page)

    added = 0

    # — LLM path
    sellers = await llm_extract_sellers(text, links, source_url)
    for s in sellers:
        if merge_seller(seen, s["name"], s["website"], s["category"]):
            added += 1

    # — Regex fallback (runs always; LLM may have missed some)
    for url in regex_extract_seller_urls(html):
        name = guess_name_from_url(url)
        cat  = guess_category_from_url(url)
        if merge_seller(seen, name, url, cat):
            added += 1

    return added


# ── CONTACT SCRAPER ───────────────────────────────────────────────────────────

async def scrape_contacts(page, name: str, website: str) -> dict:
    result = {"email": "", "phone": "", "whatsapp": "", "instagram": ""}
    if not website.startswith("http") or is_junk_domain(website):
        return result

    base = website.rstrip("/")
    urls_to_try = [base] + [base + p for p in CONTACT_PATHS]

    for url in urls_to_try:
        ok = await safe_goto(page, url, timeout=15000, settle=1.0)
        if not ok:
            continue
        try:
            await wait_for_content(page, timeout=6000)
            text = await page.evaluate("() => document.body.innerText")
            html = await page.evaluate("() => document.documentElement.innerHTML")
        except Exception:
            continue

        if not result["email"]:
            emails = [e for e in EMAIL_RE.findall(text)
                      if not any(t in e.lower() for t in JUNK_EMAIL)]
            if emails:
                for pref in ["contact","info","hello","support","sales","enquir","help"]:
                    for e in emails:
                        if pref in e.lower():
                            result["email"] = e; break
                    if result["email"]: break
                if not result["email"]:
                    result["email"] = emails[0]

        if not result["phone"]:
            for rp in PHONE_RE.findall(text):
                cp = clean_phone(rp)
                if cp:
                    result["phone"] = cp; break

        if not result["whatsapp"]:
            wa = WA_RE.findall(html)
            if wa:
                num = wa[0]
                if len(num) == 12 and num.startswith("91"): num = num[2:]
                result["whatsapp"] = "+91" + num if len(num) == 10 else "+" + num
            elif result["phone"]:
                result["whatsapp"] = result["phone"]

        if not result["instagram"]:
            for handle in INSTA_RE.findall(html):
                if handle.lower() not in INSTA_SKIP and len(handle) > 2:
                    result["instagram"] = f"https://instagram.com/{handle}"
                    break

        if all(result.values()):
            break

    parts = []
    if result["email"]:     parts.append(f"email:{result['email']}")
    if result["phone"]:     parts.append(f"phone:{result['phone']}")
    if result["whatsapp"]:  parts.append(f"wa:{result['whatsapp']}")
    if result["instagram"]: parts.append(f"ig:{result['instagram']}")
    print(f"  {name[:35]:<35} {' | '.join(parts) or 'nothing found'}")
    return result


# ── MAIN ──────────────────────────────────────────────────────────────────────

async def run():
    seen: dict[str, dict] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()

        # ── Phase 1: crawl ────────────────────────────────────────────────────
        print("=" * 70)
        print("Phase 1: Discovering Indian SME digital product sellers")
        print("=" * 70)

        # Build seed list: Bing searches + direct marketplace pages
        all_seeds = (
            [BING_BASE.format(q=quote_plus(q)) for q in SEED_SEARCHES]
            + SEED_URLS
        )

        for seed_idx, seed_url in enumerate(all_seeds):
            if len(seen) >= TARGET:
                break

            print(f"\n[Seed {seed_idx + 1}/{len(all_seeds)}] {seed_url[:80]}")
            ok = await safe_goto(page, seed_url, timeout=30000, settle=3.0)
            if not ok:
                continue

            new = await discover_from_page(page, seen, page.url)
            print(f"  Found {new} new sellers on this page (total: {len(seen)})")

            # For Bing: follow top result links one level deep
            if "bing.com" in seed_url:
                links = await get_page_links(page)
                result_links = [
                    l["href"] for l in links
                    if not is_junk_domain(l["href"])
                    and "bing.com" not in l["href"]
                ][:6]

                for link in result_links:
                    if len(seen) >= TARGET:
                        break
                    print(f"  -> {link[:70]}")
                    ok2 = await safe_goto(page, link, timeout=20000, settle=2.0)
                    if not ok2:
                        continue
                    added = await discover_from_page(page, seen, page.url)
                    if added:
                        print(f"     +{added} sellers (total: {len(seen)})")

        print(f"\nPhase 1 complete — {len(seen)} sellers discovered.")

        # ── Phase 2: scrape contacts ──────────────────────────────────────────
        print("\n" + "=" * 70)
        print("Phase 2: Scraping contacts (email / phone / WhatsApp / Instagram)")
        print("=" * 70 + "\n")

        contact_page = await ctx.new_page()
        for name, info in seen.items():
            contacts = await scrape_contacts(contact_page, name, info["website"])
            info.update(contacts)
        await contact_page.close()

        # ── Results ───────────────────────────────────────────────────────────
        total   = len(seen)
        w_email = sum(1 for v in seen.values() if v["email"])
        w_phone = sum(1 for v in seen.values() if v["phone"])
        w_wa    = sum(1 for v in seen.values() if v["whatsapp"])
        w_insta = sum(1 for v in seen.values() if v["instagram"])

        print(f"\n{'='*110}")
        print(f"FINAL RESULTS — {total} Indian SME Digital Product Sellers")
        print(f"{'='*110}")
        header = (f"  {'#':<4} {'Business':<28} {'Category':<22} "
                  f"{'Email':<28} {'Phone':<14} {'WhatsApp':<14} Instagram")
        print(header)
        print("  " + "-" * 106)
        for i, (name, v) in enumerate(seen.items(), 1):
            print(
                f"  {i:<4} {name[:27]:<28} {v['category'][:21]:<22} "
                f"{(v['email'] or '—')[:27]:<28} {(v['phone'] or '—'):<14} "
                f"{(v['whatsapp'] or '—'):<14} {v['instagram'] or '—'}"
            )
        print(f"\n  Emails found    : {w_email}/{total}")
        print(f"  Phones found    : {w_phone}/{total}")
        print(f"  WhatsApp found  : {w_wa}/{total}")
        print(f"  Instagram found : {w_insta}/{total}")
        print(f"{'='*110}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())