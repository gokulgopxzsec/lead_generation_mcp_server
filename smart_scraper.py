import asyncio
import json
import re
import httpx
from playwright.async_api import async_playwright

# ── CONFIG ──────────────────────────────────────────────────────────────────
OLLAMA_URL  = "http://127.0.0.1:11434/api/chat"
MODEL       = "qwen3:8b"
START_URL   = "https://www.google.com"
TASK        = "Search for 'AI startups in India' and extract company names and websites from the results."
MAX_STEPS   = 10
# ────────────────────────────────────────────────────────────────────────────

async def ask_llm(messages: list[dict]) -> str:
    """Send messages to Ollama using /api/generate (works on all versions)."""
    
    # Flatten message list into a single prompt string
    prompt = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages
    ) + "\nASSISTANT:"
    
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": 16000},
    }
    
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post("http://127.0.0.1:11434/api/generate", json=payload)
        r.raise_for_status()
        return r.json()["response"]  # ← "response" not "message.content"


def parse_action(raw: str) -> dict:
    """Extract JSON from LLM response, even if it has extra text."""
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find first {...} block
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"action": "done", "reason": "Could not parse LLM response"}


async def get_page_summary(page) -> str:
    """Extract visible text + interactive elements from the page."""
    summary = await page.evaluate("""() => {
        const getText = (el) => el.innerText?.trim().slice(0, 80) || '';

        // Visible text (first 2000 chars)
        const bodyText = document.body.innerText.slice(0, 2000);

        // Clickable / input elements
        const elements = [];
        const selectors = ['a', 'button', 'input', 'select', 'textarea', '[role=button]'];
        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach((el, i) => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    elements.push({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        name: el.name || el.getAttribute('aria-label') || getText(el),
                        type: el.type || '',
                        href: el.href || '',
                        placeholder: el.placeholder || ''
                    });
                }
            });
        });

        return JSON.stringify({ bodyText, elements: elements.slice(0, 40) });
    }""")
    return summary


async def run_agent():
    system_prompt = """You are a web automation agent. 
Given the current page content and your task, decide the next single action to take.

Respond ONLY with valid JSON in this exact format:
{
  "action": "<one of: click | type | navigate | scroll | extract | done>",
  "selector": "<CSS selector or link text for click/type actions>",
  "text": "<text to type, if action is type>",
  "url": "<URL if action is navigate>",
  "data": "<extracted leads/info as a string, if action is extract or done>",
  "reason": "<one sentence explanation>"
}

Rules:
- Use 'navigate' to go to a URL
- Use 'click' to click a link or button (use the link text or a CSS selector)
- Use 'type' to fill an input field, always follow with pressing Enter via text ending with \\n
- Use 'extract' to collect data from the current page
- Use 'done' when the task is complete, put all findings in 'data'
- Prefer link text over complex CSS selectors for clicking
"""

    messages = [{"role": "system", "content": system_prompt}]
    history = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page    = await browser.new_page()

        await page.goto(START_URL)
        print(f"🌐 Started at: {START_URL}\n")

        for step in range(MAX_STEPS):
            print(f"── Step {step + 1}/{MAX_STEPS} ──────────────────────────")

            page_summary = await get_page_summary(page)
            current_url  = page.url

            user_msg = f"""Task: {TASK}

Current URL: {current_url}

Page content & elements:
{page_summary}

History of actions taken so far:
{json.dumps(history, indent=2)}

What is the next action?"""

            messages_to_send = messages + [{"role": "user", "content": user_msg}]
            raw_response = await ask_llm(messages_to_send)
            print(f"🤖 LLM: {raw_response[:300]}")

            action = parse_action(raw_response)
            print(f"⚡ Action: {action.get('action')} | {action.get('reason','')}")
            history.append(action)

            # ── Execute the action ──────────────────────────────────────────
            try:
                match action.get("action"):
                    case "navigate":
                        await page.goto(action["url"])
                        await page.wait_for_load_state("domcontentloaded")

                    case "click":
                        sel = action.get("selector", "")
                        try:
                            # Try CSS selector first
                            await page.click(sel, timeout=5000)
                        except Exception:
                            # Fall back to finding by link text
                            await page.get_by_text(sel, exact=False).first.click()
                        await page.wait_for_load_state("domcontentloaded")

                    case "type":
                        sel  = action.get("selector", "")
                        text = action.get("text", "")
                        try:
                            await page.fill(sel, text.rstrip("\n"))
                        except Exception:
                            await page.get_by_placeholder(sel).fill(text.rstrip("\n"))
                        if text.endswith("\n"):
                            await page.keyboard.press("Enter")
                            await page.wait_for_load_state("domcontentloaded")

                    case "scroll":
                        await page.mouse.wheel(0, 800)
                        await asyncio.sleep(1)

                    case "extract":
                        print(f"\n📋 Extracted:\n{action.get('data','')}\n")

                    case "done":
                        print(f"\n✅ Task complete!\n📋 Results:\n{action.get('data','(none)')}")
                        break

            except Exception as e:
                print(f"⚠️  Action failed: {e} — letting LLM retry")
                history[-1]["error"] = str(e)

            await asyncio.sleep(1.5)  # polite delay

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_agent())