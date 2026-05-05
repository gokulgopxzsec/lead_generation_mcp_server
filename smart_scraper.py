import asyncio
import json
import re
import httpx
from playwright.async_api import async_playwright
from urllib.parse import quote_plus

# ── CONFIG ──────────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://127.0.0.1:11434/api/generate"
MODEL        = "qwen3:8b"
SEARCH_QUERY = "AI startups in India"
MAX_STEPS    = 20
MAX_RETRIES  = 3
# ────────────────────────────────────────────────────────────────────────────

TASK = """Extract company names and their OFFICIAL websites for AI startups in India.
Rules for websites:
- Find REAL company URLs (e.g. uniphore.com, qure.ai) — NOT internal directory links like ai-startups.pro/video/...
- If a page only shows directory links, navigate into the company page to find the real URL.
Collect at least 15 unique companies with real websites."""

START_URL = f"https://duckduckgo.com/?q={quote_plus(SEARCH_QUERY)}&ia=web"


# ── LLM ─────────────────────────────────────────────────────────────────────

async def ask_llm(messages: list[dict]) -> str:
    prompt = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages
    ) + "\nASSISTANT:"

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_ctx": 16000,
            "num_predict": 600,
        },
    }

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(OLLAMA_URL, json=payload)
        r.raise_for_status()
        return r.json()["response"]


# ── PARSERS ──────────────────────────────────────────────────────────────────

def parse_action(raw: str) -> dict:
    """Extract and validate action JSON from LLM response."""
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    data = {}

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except Exception:
                pass

    # Ensure all fields exist and are strings
    for field in ["action", "selector", "text", "url", "data", "reason"]:
        if field not in data:
            data[field] = ""
        elif not isinstance(data[field], str):
            data[field] = str(data[field])

    # Sanitize action
    valid_actions = {"click", "type", "navigate", "scroll", "extract", "done"}
    if data.get("action") not in valid_actions:
        data["action"] = "scroll"

    return data


def parse_extracted_data(raw_data: str) -> list[dict]:
    """
    Parse LLM extracted data into list of {name, website} dicts.
    Handles: JSON arrays, JSON dicts, single-quote JSON, plain text lines.
    """
    raw_data = raw_data.strip()
    if not raw_data:
        return []

    # Fix common LLM habit of using single quotes instead of double
    def fix_quotes(s):
        return s.replace("'", '"')

    # Try JSON parse
    for attempt in [raw_data, fix_quotes(raw_data)]:
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, list):
                results = []
                for item in parsed:
                    if isinstance(item, dict):
                        name    = str(item.get("name", "")).strip()
                        website = str(item.get("website", item.get("url", ""))).strip()
                        if name:
                            results.append({"name": name, "website": website})
                return results
            if isinstance(parsed, dict) and parsed.get("name"):
                return [{"name": str(parsed["name"]).strip(),
                         "website": str(parsed.get("website", parsed.get("url", ""))).strip()}]
        except Exception:
            pass

    # Fallback: plain text lines  "Company - https://..."  or  "Company: https://..."
    results = []
    for line in raw_data.splitlines():
        line = line.strip().lstrip("0123456789.-) ")
        if not line:
            continue
        for sep in [" - ", ": ", " | "]:
            if sep in line:
                parts = line.split(sep, 1)
                results.append({"name": parts[0].strip(), "website": parts[1].strip()})
                break
        else:
            results.append({"name": line, "website": ""})
    return results


def is_real_website(url: str) -> bool:
    """Return False for internal directory links, True for real company URLs."""
    if not url or not url.startswith("http"):
        return False
    junk_domains = [
        "ai-startups.pro", "crunchbase.com", "linkedin.com",
        "tracxn.com", "yourstory.com", "techcrunch.com",
        "duckduckgo.com", "ycombinator.com",
    ]
    return not any(d in url for d in junk_domains)


# ── HELPERS ──────────────────────────────────────────────────────────────────

def detect_loop(history: list[dict]) -> bool:
    if len(history) < MAX_RETRIES:
        return False
    last = history[-MAX_RETRIES:]
    return all(
        a.get("action") == last[0].get("action") and
        a.get("selector") == last[0].get("selector") and
        a.get("url") == last[0].get("url")
        for a in last
    )


async def get_page_summary(page) -> str:
    for attempt in range(3):
        try:
            await page.wait_for_load_state("domcontentloaded")
            return await page.evaluate("""() => {
                const bodyText = document.body.innerText.slice(0, 3000);
                const elements = [];
                const selectors = ['a[href]', 'button', 'input', '[role=button]'];
                selectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.top < 2000) {
                            const text = (el.innerText || el.getAttribute('aria-label') || el.placeholder || '').trim().slice(0, 80);
                            if (text) {
                                elements.push({
                                    tag: el.tagName.toLowerCase(),
                                    text,
                                    href: el.href || '',
                                    id: el.id || '',
                                    placeholder: el.placeholder || ''
                                });
                            }
                        }
                    });
                });
                return JSON.stringify({ bodyText, elements: elements.slice(0, 50) });
            }""")
        except Exception as e:
            if attempt == 2:
                raise
            print(f"⚠️  Page context unstable (attempt {attempt + 1}), retrying...")
            await asyncio.sleep(2)


def print_results(seen: dict[str, str], label: str):
    print(f"\n{label} {len(seen)} unique companies:\n")
    print(f"  {'#':<4} {'Company':<35} {'Website'}")
    print(f"  {'-'*4} {'-'*35} {'-'*40}")
    for i, (name, website) in enumerate(seen.items(), 1):
        flag = "✅" if is_real_website(website) else "⚠️ "
        print(f"  {i:<4} {name:<35} {flag} {website}")
    real = sum(1 for w in seen.values() if is_real_website(w))
    print(f"\n  Real websites: {real}/{len(seen)}")


# ── AGENT ────────────────────────────────────────────────────────────────────

async def run_agent():
    system_prompt = f"""You are a precise web scraping agent. Your ONLY job:
TASK: {TASK}
Extract company names, OFFICIAL websites, and contact emails for AI startups in India.
Rules for websites:
- Find REAL company URLs (e.g. uniphore.com, qure.ai) — NOT internal directory links like ai-startups.pro/video/...
- If a page only shows directory links, navigate into the company page to find the real URL.
Collect at least 15 unique companies with real websites.
STRICT RULES:
1. Respond ONLY with a single valid JSON object — no extra text, no markdown, no explanation.
2. Only use selectors/links that appear in the "elements" list provided. NEVER invent selectors.
3. Get MORE results by scrolling, or navigating to the next page URL.
4. Use "extract" to save visible results NOW. Use "done" only when you have 15+ real companies.
5. IMPORTANT: Provide REAL company websites in extracted data, not directory/article links.
   Good: {{"name": "Uniphore", "website": "https://www.uniphore.com"}}
   Bad:  {{"name": "Uniphore", "website": "https://ai-startups.pro/video/uniphore/"}}
6. If stuck, use "navigate" to a different source URL.
7. NEVER navigate to google.com.

RESPONSE FORMAT (always exactly this, nothing else):
{{
  "action": "click | type | navigate | scroll | extract | done",
  "selector": "exact visible link text or CSS selector from the elements list",
  "text": "text to type (only for type action)",
  "url": "full URL starting with https:// (only for navigate action)",
  "data": "JSON array of {{name, website}} objects (for extract/done actions)",
  "reason": "one sentence"
}}"""

    messages = [{"role": "system", "content": system_prompt}]
    history: list[dict] = []
    seen_companies: dict[str, str] = {}   # name -> website, auto-deduplicates

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"🔍 Searching DuckDuckGo: {SEARCH_QUERY}")
        await page.goto(START_URL, wait_until="networkidle")
        await asyncio.sleep(2)
        print(f"🌐 Loaded: {page.url}\n")

        for step in range(MAX_STEPS):
            print(f"── Step {step + 1}/{MAX_STEPS} ──────────────────────────")

            # Loop detection
            if detect_loop(history):
                print("🔁 Loop detected — forcing navigation back to DuckDuckGo")
                await page.goto(START_URL, wait_until="networkidle")
                await asyncio.sleep(2)
                history = history[:-MAX_RETRIES]
                continue

            page_summary = await get_page_summary(page)
            current_url  = page.url

            # Show LLM only the last 5 collected companies so context stays small
            recent_found = list(seen_companies.items())[-5:]
            recent_str   = "\n".join(f"  - {n}: {w}" for n, w in recent_found) or "None yet"

            user_msg = f"""Current URL: {current_url}
Companies found so far: {len(seen_companies)} (need 15+)
Last 5 found:
{recent_str}

Page content & elements:
{page_summary}

Recent actions (last 5):
{json.dumps(history[-5:], indent=2)}

What is the next action?"""

            messages_to_send = messages + [{"role": "user", "content": user_msg}]

            try:
                raw_response = await ask_llm(messages_to_send)
            except Exception as e:
                print(f"❌ LLM error: {e}")
                break

            print(f"🤖 LLM: {raw_response[:300]}")
            action = parse_action(raw_response)
            print(f"⚡ Action: {action['action']} | {action.get('reason', '')}")
            history.append({k: action[k] for k in ["action", "selector", "url", "reason"]})

            # ── Execute ──────────────────────────────────────────────────────
            try:
                match action["action"]:

                    case "navigate":
                        url = action["url"].strip()
                        if not url.startswith("http"):
                            url = "https://" + url
                        if "google.com" in url:
                            print("🚫 Blocked Google — redirecting to DuckDuckGo")
                            url = START_URL
                        await page.goto(url, wait_until="networkidle", timeout=30000)
                        await asyncio.sleep(1.5)

                    case "click":
                        sel     = action["selector"]
                        clicked = False
                        for attempt in [
                            lambda: page.click(sel, timeout=4000),
                            lambda: page.get_by_text(sel, exact=False).first.click(timeout=4000),
                        ]:
                            try:
                                await attempt()
                                clicked = True
                                break
                            except Exception:
                                pass
                        if not clicked:
                            print(f"⚠️  Could not click '{sel}' — scrolling instead")
                            await page.mouse.wheel(0, 800)
                        else:
                            await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(1.5)

                    case "type":
                        sel  = action["selector"]
                        text = action["text"].rstrip("\n")
                        typed = False
                        for attempt in [
                            lambda: page.fill(sel, text),
                            lambda: page.get_by_placeholder(sel).fill(text),
                        ]:
                            try:
                                await attempt()
                                typed = True
                                break
                            except Exception:
                                pass
                        if not typed:
                            print(f"⚠️  Could not type into '{sel}'")
                        elif action["text"].endswith("\n"):
                            await page.keyboard.press("Enter")
                            await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(1.5)

                    case "scroll":
                        await page.mouse.wheel(0, 1200)
                        await asyncio.sleep(2)

                    case "extract" | "done":
                        raw_data = action.get("data", "")
                        items    = parse_extracted_data(raw_data)
                        new_count = 0
                        for item in items:
                            name    = item["name"].strip()
                            website = item["website"].strip()
                            if name and name not in seen_companies:
                                seen_companies[name] = website
                                new_count += 1

                        if action["action"] == "extract":
                            if new_count == 0:
                                print("⚠️  No new companies — scrolling for more")
                                await page.mouse.wheel(0, 1200)
                                await asyncio.sleep(1.5)
                            else:
                                print(f"\n📋 +{new_count} new companies (total: {len(seen_companies)})\n")

                        if action["action"] == "done":
                            print_results(seen_companies, "✅ Task complete!")
                            break

            except Exception as e:
                print(f"⚠️  Action failed: {e}")
                if history:
                    history[-1]["error"] = str(e)

            await asyncio.sleep(1)

        else:
            print_results(seen_companies, "⏱️ Max steps reached —")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_agent())