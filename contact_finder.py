"""
contact_finder.py — Multi-source contact extraction for Indian local businesses.

Sources tried (in parallel):
  1. Bing search snippets  — most reliable, shows Google Business Profile phone
  2. Google search snippets — Knowledge Panel often has phone directly
  3. Justdial direct page  — encoded phone extraction from HTML data attrs
  4. Sulekha listing page  — cleaner HTML, JSON-LD structured data
  5. Yellow Pages India     — yellowpages.in simple HTML
  6. IndiaMART listing     — for B2B (builders, contractors)
  7. Business website      — /contact, /about, mailto:, tel: links

WHY SAMPLE LEADS RETURN NOTHING:
  The 8 sample leads ("Sri Krishna Restaurant" etc.) are FAKE businesses
  with fake phone numbers. They don't exist on Justdial/Google/anywhere.
  This module works on REAL businesses you scrape or add manually.

USAGE:
  python main.py enrich 1      # one lead by ID
  python main.py enrich        # all hot + warm leads
"""

import httpx
import asyncio
import re
import base64
import json
from bs4 import BeautifulSoup
from database import get_lead, update_lead, list_leads

HEADERS_CHROME = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

HEADERS_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/90.0.4430.91 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TIMEOUT = 20.0

PHONE_PATTERNS = [
    r"\+91[\s\-\.]?\d{5}[\s\-\.]?\d{5}",
    r"\+91[\s\-\.]?\d{10}",
    r"(?<!\d)0\d{2,4}[\s\-\.]\d{6,8}(?!\d)",
    r"(?<!\d)91[\-\s]?[6-9]\d{9}(?!\d)",
    r"(?<!\d)[6-9]\d{9}(?!\d)",
]

EMAIL_PATTERN = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"

EMAIL_BLACKLIST = {
    "example.com", "test.com", "domain.com", "email.com",
    "youremail.com", "yourname.com", "sentry.io", "noreply.com",
    "w3schools.com", "schema.org", "wordpress.com", "wixpress.com",
    "squarespace.com", "placeholder.com", "user.com",
}

PHONE_BLACKLIST = {
    "9999999999", "8888888888", "1234567890", "0000000000",
    "1111111111", "9876543210", "9800000000", "9000000000",
}


def extract_phones(text: str) -> list:
    found = []
    for pattern in PHONE_PATTERNS:
        for m in re.finditer(pattern, text):
            raw = m.group().strip()
            digits = re.sub(r"\D", "", raw)
            if digits.startswith("91") and len(digits) == 12:
                digits = digits[2:]
            if len(digits) == 10 and digits not in PHONE_BLACKLIST:
                found.append("+91 " + digits[:5] + " " + digits[5:])
            elif len(digits) >= 7 and digits not in PHONE_BLACKLIST:
                found.append(raw.strip())
    seen, result = set(), []
    for p in found:
        key = re.sub(r"\D", "", p)
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def extract_emails(text: str) -> list:
    found = re.findall(EMAIL_PATTERN, text)
    seen, result = set(), []
    for email in found:
        email = email.lower().strip(".,;:")
        domain = email.split("@")[-1]
        if domain not in EMAIL_BLACKLIST and email not in seen:
            seen.add(email)
            result.append(email)
    return result


def best_phone(phones: list) -> str:
    if not phones:
        return ""
    def score(p):
        d = re.sub(r"\D", "", p)
        if d.startswith("91"): d = d[2:]
        return 10 if (len(d) == 10 and d[0] in "6789") else 5
    return sorted(phones, key=score, reverse=True)[0]


async def fetch_html(client, url: str, headers=None) -> str:
    try:
        r = await client.get(url, headers=headers or HEADERS_CHROME,
                             timeout=TIMEOUT, follow_redirects=True)
        if r.status_code == 200:
            return r.text
        if r.status_code in (403, 429):
            r2 = await client.get(url, headers=HEADERS_MOBILE,
                                  timeout=TIMEOUT, follow_redirects=True)
            return r2.text if r2.status_code == 200 else ""
    except Exception:
        pass
    return ""


async def source_bing(client, name: str, location: str) -> dict:
    """Bing search — most reliable. Google Business Profile phone shows in snippets."""
    phones, emails = [], []
    city = location.split(",")[0].strip()

    queries = [
        f'"{name}" {city} phone number contact',
        f'{name} {location} email contact number',
        f'"{name}" {city} site:justdial.com OR site:sulekha.com',
    ]
    for query in queries:
        url = "https://www.bing.com/search?q=" + query.replace(" ", "+") + "&setlang=en-IN&cc=IN"
        html = await fetch_html(client, url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for sel in [".b_factrow", ".li_fact", ".b_vCard", ".rich_action", ".b_rs", ".b_entityTP"]:
            for el in soup.select(sel):
                t = el.get_text(" ")
                phones += extract_phones(t)
                emails += extract_emails(t)
        full = soup.get_text(" ")
        phones += extract_phones(full)
        emails += extract_emails(full)
        await asyncio.sleep(1.2)

    return {"source": "bing",
            "phones": list(dict.fromkeys(phones)),
            "emails": list(dict.fromkeys(emails))}


async def source_google(client, name: str, location: str) -> dict:
    """Google Knowledge Panel often shows business phone directly."""
    phones, emails = [], []
    city = location.split(",")[0].strip()

    queries = [
        f"{name} {city} Kerala contact phone",
        f"{name} {location} email",
    ]
    for query in queries:
        url = "https://www.google.com/search?q=" + query.replace(" ", "+") + "&gl=in&hl=en"
        html = await fetch_html(client, url, headers={**HEADERS_CHROME, "Accept-Language": "en-IN,en;q=0.9"})
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for sel in [".Z0LcW", ".LrzXr", ".BNeawe", ".fl.iUh30", "span[data-dtype='d3ph']", ".kp-header"]:
            for el in soup.select(sel):
                t = el.get_text(" ")
                phones += extract_phones(t)
                emails += extract_emails(t)
        full = soup.get_text(" ")
        phones += extract_phones(full)
        emails += extract_emails(full)
        await asyncio.sleep(2.0)

    return {"source": "google",
            "phones": list(dict.fromkeys(phones)),
            "emails": list(dict.fromkeys(emails))}


async def source_justdial(client, name: str, location: str) -> dict:
    """Justdial encodes phones in data-* attributes. Try multiple URL patterns."""
    phones, emails = [], []
    city = location.split(",")[0].strip()
    name_slug = re.sub(r"[^a-zA-Z0-9\s]", "", name).strip().replace(" ", "-")
    city_slug  = city.replace(" ", "-")

    urls = [
        f"https://www.justdial.com/{city_slug}/{name_slug}",
        f"https://www.justdial.com/Kerala/{city_slug}/{name_slug}",
        f"https://www.justdial.com/{city_slug}/{name_slug}/nct-11281160",
        f"https://www.justdial.com/functions/ajaxsearch.php?national=0&keyword={name.replace(' ', '+')}&where={city.replace(' ', '+')}",
    ]

    for url in urls:
        html = await fetch_html(client, url)
        if not html or len(html) < 300:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Phone in data attributes (sometimes base64 encoded)
        for attr in ["data-phone", "data-mob", "data-sms", "data-msisdn", "data-number"]:
            for tag in soup.find_all(attrs={attr: True}):
                raw = tag.get(attr, "")
                try:
                    decoded = base64.b64decode(raw + "==").decode("utf-8", errors="ignore")
                    phones += extract_phones(decoded)
                except Exception:
                    pass
                phones += extract_phones(raw)

        # JSON-LD structured data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, list):
                    data = data[0] if data else {}
                ph = data.get("telephone", "") or data.get("phone", "")
                em = data.get("email", "")
                if ph: phones += extract_phones(str(ph))
                if em: emails.append(str(em).lower().strip())
            except Exception:
                pass

        text = soup.get_text(" ")
        phones += extract_phones(text)
        emails += extract_emails(text)

    return {"source": "justdial",
            "phones": list(dict.fromkeys(phones)),
            "emails": list(dict.fromkeys(emails))}


async def source_sulekha(client, name: str, location: str) -> dict:
    phones, emails = [], []
    city = location.split(",")[0].strip().lower().replace(" ", "-")
    name_slug = re.sub(r"[^a-zA-Z0-9\s]", "", name).lower().replace(" ", "-")

    urls = [
        f"https://www.sulekha.com/{city}/{name_slug}",
        f"https://www.sulekha.com/local-search?searchCategory={name.replace(' ', '+')}&location={city}",
    ]
    for url in urls:
        html = await fetch_html(client, url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, list): data = data[0] if data else {}
                ph = data.get("telephone", "") or data.get("phone", "")
                em = data.get("email", "")
                if ph: phones += extract_phones(str(ph))
                if em: emails.append(str(em).lower().strip())
            except Exception:
                pass
        text = soup.get_text(" ")
        phones += extract_phones(text)
        emails += extract_emails(text)

    return {"source": "sulekha",
            "phones": list(dict.fromkeys(phones)),
            "emails": list(dict.fromkeys(emails))}


async def source_yellowpages(client, name: str, location: str) -> dict:
    phones, emails = [], []
    city = location.split(",")[0].strip()
    url = (f"https://www.yellowpages.in/search?keyword={name.replace(' ', '+')}"
           f"&location={city.replace(' ', '+')}")
    html = await fetch_html(client, url)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ")
        phones += extract_phones(text)
        emails += extract_emails(text)
    return {"source": "yellowpages",
            "phones": list(dict.fromkeys(phones)),
            "emails": list(dict.fromkeys(emails))}


async def source_indiamart(client, name: str, location: str, category: str = "") -> dict:
    """Only for B2B categories like builders, contractors, manufacturers."""
    b2b = {"builder", "contractor", "manufacturer", "supplier", "dealer", "wholesaler"}
    if not any(c in category.lower() for c in b2b):
        return {"source": "indiamart", "phones": [], "emails": []}

    phones, emails = [], []
    city = location.split(",")[0].strip()
    url = (f"https://www.indiamart.com/search.mp?ss={name.replace(' ', '+')}"
           f"&cq={city.replace(' ', '+')}")
    html = await fetch_html(client, url)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ")
        phones += extract_phones(text)
        emails += extract_emails(text)
    return {"source": "indiamart",
            "phones": list(dict.fromkeys(phones)),
            "emails": list(dict.fromkeys(emails))}


async def source_website(client, website: str) -> dict:
    if not website or not website.startswith("http"):
        return {"source": "website", "phones": [], "emails": []}

    phones, emails = [], []
    base = website.rstrip("/")
    pages = [base,
             f"{base}/contact", f"{base}/contact-us",
             f"{base}/about",   f"{base}/about-us",
             f"{base}/reach-us", f"{base}/contact.html"]

    for url in pages:
        html = await fetch_html(client, url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("mailto:"):
                em = href.replace("mailto:", "").split("?")[0].strip().lower()
                if "@" in em: emails.append(em)
            if href.startswith("tel:"):
                phones += extract_phones(href.replace("tel:", ""))
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, list): data = data[0] if data else {}
                ph = data.get("telephone", "") or data.get("phone", "")
                em = data.get("email", "")
                if ph: phones += extract_phones(str(ph))
                if em: emails.append(str(em).lower().strip())
            except Exception:
                pass
        text = soup.get_text(" ")
        phones += extract_phones(text)
        emails += extract_emails(text)

    return {"source": "website",
            "phones": list(dict.fromkeys(phones)),
            "emails": list(dict.fromkeys(emails))}


async def find_contacts(lead: dict) -> dict:
    name     = lead.get("name", "")
    location = lead.get("location", "Kerala")
    website  = lead.get("website", "")
    category = lead.get("category", "")

    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS_CHROME,
                                 follow_redirects=True) as client:
        tasks = [
            source_bing(client, name, location),
            source_google(client, name, location),
            source_justdial(client, name, location),
            source_sulekha(client, name, location),
            source_yellowpages(client, name, location),
            source_indiamart(client, name, location, category),
        ]
        if website:
            tasks.append(source_website(client, website))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_phones, all_emails, sources_hit = [], [], []
    for r in results:
        if not isinstance(r, dict):
            continue
        all_phones += r.get("phones", [])
        all_emails += r.get("emails", [])
        if r.get("phones") or r.get("emails"):
            sources_hit.append(r.get("source", ""))

    # Deduplicate
    seen_p, seen_e = set(), set()
    uniq_phones, uniq_emails = [], []
    for p in all_phones:
        key = re.sub(r"\D", "", p)
        if key not in seen_p:
            seen_p.add(key)
            uniq_phones.append(p)
    for e in all_emails:
        e = e.lower().strip()
        if e not in seen_e:
            seen_e.add(e)
            uniq_emails.append(e)

    return {
        "phone":      best_phone(uniq_phones),
        "email":      uniq_emails[0] if uniq_emails else "",
        "all_phones": uniq_phones[:6],
        "all_emails": uniq_emails[:6],
        "sources":    list(dict.fromkeys(sources_hit)),
    }


async def enrich_lead(lead_id: int) -> dict:
    lead = await get_lead(lead_id)
    if not lead:
        return {"error": f"Lead {lead_id} not found"}

    result = await find_contacts(lead)

    updates = {}
    if result["phone"]:
        updates["phone"] = result["phone"]
    if result["email"] and not lead.get("email"):
        updates["email"] = result["email"]

    extra = []
    if result["all_phones"]:
        extra.append("📞 " + " | ".join(result["all_phones"]))
    if result["all_emails"]:
        extra.append("📧 " + " | ".join(result["all_emails"]))
    if extra:
        existing = (lead.get("notes") or "").strip()
        if "📞" not in existing and "📧" not in existing:
            updates["notes"] = (existing + "\n" + "\n".join(extra)).strip()

    if updates:
        await update_lead(lead_id, updates)
    return result


async def enrich_all_leads(min_score: int = 50) -> list:
    leads = await list_leads(min_score=min_score, limit=50)
    results = []
    for lead in leads:
        print(f"  🔍 {lead['name']} ({lead.get('location', '')})...")
        result = await enrich_lead(lead["id"])
        result["lead_name"] = lead["name"]
        result["lead_id"]   = lead["id"]
        results.append(result)
        await asyncio.sleep(3)
    return results
