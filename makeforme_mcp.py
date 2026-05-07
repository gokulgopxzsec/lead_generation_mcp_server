"""
makeforme_agent.py — Local Ollama-powered funnel agent
=======================================================
No MCP. No Claude Desktop. Just Ollama + your tool functions.

Usage:
    python makeforme_agent.py

Requirements:
    pip install httpx
    ollama pull qwen3:8b          # or llama3.1:8b / mistral

How it works:
    1. You type a task in plain English
    2. Ollama picks which tools to call (function-calling format)
    3. This script runs the tools locally
    4. Results feed back to Ollama
    5. Loop repeats until Ollama gives a final answer
"""

import asyncio
import json
import os
import random
import re
import sqlite3
import string
import textwrap
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx

# ── CONFIG ────────────────────────────────────────────────────────────────────
OLLAMA_URL    = "http://127.0.0.1:11434/api/chat"
MODEL         = "qwen3:8b"          # change to llama3.1:8b if preferred
DB_PATH       = Path(__file__).parent / "makeforme_crm.db"
BASE_URL      = os.getenv("MAKEFORME_BASE_URL", "https://makeforme.in")
WA_TOKEN      = os.getenv("WHATSAPP_TOKEN", "")
WA_PHONE_ID   = os.getenv("WHATSAPP_PHONE_ID", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # optional: for richer content gen
MAX_TOOL_ROUNDS = 8   # safety limit on agentic loops
# ─────────────────────────────────────────────────────────────────────────────


# ── DATABASE ──────────────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id              TEXT PRIMARY KEY,
            name            TEXT,
            instagram       TEXT,
            phone           TEXT,
            niche           TEXT,
            stage           TEXT DEFAULT 'tofu',
            trial_start     TEXT,
            trial_day       INTEGER DEFAULT 0,
            affiliate_code  TEXT UNIQUE,
            referrer_id     TEXT,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id     TEXT,
            channel     TEXT,
            template    TEXT,
            status      TEXT,
            sent_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id     TEXT,
            event       TEXT,
            payload     TEXT,
            occurred_at TEXT DEFAULT (datetime('now'))
        );
        """)


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _lead_id() -> str:
    return "lead_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

def _aff_code(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", name.lower())[:8]
    return slug + "".join(random.choices(string.digits, k=4))


# ── CONTENT GENERATION ───────────────────────────────────────────────────────
# Uses Anthropic if key present, otherwise falls back to Ollama itself.

async def _generate(system: str, user: str) -> str:
    if ANTHROPIC_KEY:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1024,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            r.raise_for_status()
            return r.json()["content"][0]["text"]
    else:
        # Use Ollama for content gen (no tools, just a direct prompt)
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(
                OLLAMA_URL,
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.7, "num_ctx": 8192},
                },
            )
            r.raise_for_status()
            return r.json()["message"]["content"]


async def _send_whatsapp_api(phone: str, message: str) -> dict:
    if not WA_TOKEN or not WA_PHONE_ID:
        return {"status": "stub", "note": "set WHATSAPP_TOKEN + WHATSAPP_PHONE_ID", "phone": phone, "message_preview": message[:80]}
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        digits = "91" + digits
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": digits, "type": "text", "text": {"body": message}},
        )
        return r.json()


# ── TOOL FUNCTIONS ────────────────────────────────────────────────────────────

async def find_leads(niche: str, location: str = "India", limit: int = 10) -> dict:
    """Find Indian creators selling via DMs — prime makeforme.in prospects."""
    stub_profiles = [
        {"username": f"{niche.replace(' ','').lower()}_maker{i}",
         "bio": f"Handmade {niche} | DM to order 📦 | Ships across India",
         "followers": random.randint(400, 9000),
         "link_in_bio": None,
         "source": "instagram_hashtag"}
        for i in range(1, min(limit, 8) + 1)
    ]
    return {
        "niche": niche, "location": location,
        "total_found": len(stub_profiles),
        "profiles": stub_profiles,
        "next_step": "Call upsert_lead for each profile to add them to CRM.",
    }


async def gen_ad_copy(niche: str, stage: str = "tofu", variants: int = 3) -> dict:
    """Generate Instagram Reel hooks + ad copy for a maker niche."""
    stage_goal = {
        "tofu": "stop the scroll, expose the DM-chaos pain point",
        "mofu": "social proof, tease the URL claim, mention ₹30/month",
        "bofu": "urgency, one-tap signup, specific price ₹30/month",
    }.get(stage, stage)

    system = (
        "Performance marketing copywriter for Indian D2C creator brands. "
        "Write punchy vernacular-aware copy for Indian solopreneurs. "
        "Use ₹ not $. Hooks ≤8 words. No corporate language."
    )
    user = textwrap.dedent(f"""
        Product: makeforme.in — zero-commission Indian store builder, ₹30/month.
        Niche: {niche} sellers. Goal: {stage_goal}.
        Write {variants} ad copy variants.
        Each must have: hook (≤8 words), body (2-3 sentences), cta (≤4 words).
        Return ONLY a JSON array: [{{"hook":"...","body":"...","cta":"..."}}]
    """)
    raw = await _generate(system, user)
    try:
        data = json.loads(re.sub(r"```(?:json)?|```", "", raw).strip())
    except Exception:
        data = [{"hook": "Stop selling in DMs!", "body": raw[:200], "cta": "Start Free"}]
    return {"niche": niche, "stage": stage, "variants": data}


async def gen_blog_post(topic: str, product_type: str) -> dict:
    """Draft an SEO blog post for an Indian maker niche."""
    system = (
        "SEO content writer for Indian e-commerce SaaS. "
        "Simple English, short paragraphs. Mention UPI, WhatsApp, Instagram naturally. "
        "Keyword density ~1.5%."
    )
    user = textwrap.dedent(f"""
        Write a 600-word SEO blog post with title, meta description, and 4 H2 sections.
        Topic: {topic}
        Product type: {product_type}
        Naturally mention makeforme.in as the solution in the last section.
        Return ONLY JSON: {{"title":"...","meta":"...","body":"..."}}
    """)
    raw = await _generate(system, user)
    try:
        post = json.loads(re.sub(r"```(?:json)?|```", "", raw).strip())
    except Exception:
        post = {"title": topic, "meta": "", "body": raw}
    return post


async def upsert_lead(
    name: str, instagram: str = "", phone: str = "",
    niche: str = "", stage: str = "tofu", notes: str = "",
) -> dict:
    """Add or update a lead in the CRM. Returns the full lead record with its id."""
    with db() as con:
        existing = con.execute(
            "SELECT * FROM leads WHERE instagram=? OR (phone!='' AND phone=?)",
            (instagram, phone),
        ).fetchone()
        if existing:
            con.execute(
                """UPDATE leads SET
                   name=COALESCE(NULLIF(?,''),(name)),
                   instagram=COALESCE(NULLIF(?,''),(instagram)),
                   phone=COALESCE(NULLIF(?,''),(phone)),
                   niche=COALESCE(NULLIF(?,''),(niche)),
                   stage=?, notes=?, updated_at=datetime('now')
                   WHERE id=?""",
                (name, instagram, phone, niche, stage, notes, existing["id"]),
            )
            lead_id = existing["id"]
            action = "updated"
        else:
            lead_id = _lead_id()
            con.execute(
                "INSERT INTO leads (id,name,instagram,phone,niche,stage,notes) VALUES (?,?,?,?,?,?,?)",
                (lead_id, name, instagram, phone, niche, stage, notes),
            )
            action = "created"
        row = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return {"action": action, "lead": dict(row)}


async def check_url(username: str) -> dict:
    """Check if makeforme.in/<username> is available + return a retargeting message."""
    clean = re.sub(r"[^a-z0-9_\-]", "", username.lower())
    available = random.random() > 0.2  # stub — replace with real DB lookup
    message = (
        f"Hey! Your store link makeforme.in/{clean} is still available. "
        f"Claim it today — 4 minutes setup, ₹30/month, 0% commission. 🎉"
        if available else
        f"makeforme.in/{clean} is taken, but makeforme.in/{clean}shop is free! Reply to claim."
    )
    return {"username": clean, "available": available, "url": f"{BASE_URL}/{clean}", "retargeting_message": message}


async def send_whatsapp(lead_id: str, template: str, params: dict | None = None) -> dict:
    """Send a WhatsApp message to a lead using a named template."""
    TEMPLATES = {
        "pricing_calc":  "Hey {name}! Free Pricing Calculator for Indian makers — figure your profit margin in 2 mins: {BASE_URL}/tools/pricing-calc 📊",
        "url_claim":     "Hi {name}! Your store makeforme.in/{username} is waiting. Claim it free — live in 4 mins, ₹30/mo after trial. 👉 {BASE_URL}/signup",
        "onboarding_d0": "Welcome to makeforme.in, {name}! 🎉 Send me a photo of your best product and I'll write the perfect listing for it. Or upload here: {BASE_URL}/dashboard/products/new",
        "trial_d5":      "Hey {name}, day 5 of your trial! One sale of ₹360+ pays for a whole YEAR at ₹30/month. 😮 Upgrade now: {BASE_URL}/upgrade",
        "trial_ending":  "Hi {name}, your trial ends in 24h. Upgrade to Pro for ₹30/month — ₹1/day. Don't lose your store: {BASE_URL}/upgrade",
        "first_sale":    "🎊 Congrats {name}! First sale on makeforme.in! Share your experience and we'll feature you as Maker of the Week on Instagram!",
    }
    if template not in TEMPLATES:
        return {"error": f"Unknown template '{template}'. Valid: {list(TEMPLATES)}"}

    with db() as con:
        lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            return {"error": f"Lead '{lead_id}' not found"}

    ctx = {
        "name": lead["name"] or "there",
        "username": re.sub(r"[^a-z0-9_\-]", "", (lead["instagram"] or lead["name"] or "yourname").lower()),
        "BASE_URL": BASE_URL,
        **(params or {}),
    }
    body = TEMPLATES[template].format(**ctx)
    result = await _send_whatsapp_api(lead["phone"] or "", body)

    with db() as con:
        con.execute(
            "INSERT INTO messages (lead_id, channel, template, status) VALUES (?,?,?,?)",
            (lead_id, "whatsapp", template, result.get("status", "sent")),
        )
    return {"lead_id": lead_id, "template": template, "message": body, "api_response": result}


async def get_trial_status(lead_id: str) -> dict:
    """Return trial day + recommended next WhatsApp nudge for a lead."""
    with db() as con:
        lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            return {"error": f"Lead '{lead_id}' not found"}
        msgs = con.execute(
            "SELECT template, sent_at FROM messages WHERE lead_id=? ORDER BY sent_at",
            (lead_id,),
        ).fetchall()

    if not lead["trial_start"]:
        day, recommended = 0, "onboarding_d0"
    else:
        day = min((datetime.utcnow() - datetime.fromisoformat(lead["trial_start"])).days, 7)
        schedule = {0: "onboarding_d0", 5: "trial_d5", 6: "trial_ending"}
        sent = {m["template"] for m in msgs}
        recommended = next((t for d, t in sorted(schedule.items()) if d <= day and t not in sent), None)

    return {
        "lead_id": lead_id, "name": lead["name"], "stage": lead["stage"],
        "trial_day": day, "trial_expires_in_days": max(0, 7 - day),
        "messages_sent": [{"template": m["template"], "sent_at": m["sent_at"]} for m in msgs],
        "recommended_next_message": recommended,
    }


async def trigger_onboarding(lead_id: str) -> dict:
    """Start 7-day trial sequence for a new signup. Sets trial_start, sends day-0 WhatsApp."""
    with db() as con:
        lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            return {"error": f"Lead '{lead_id}' not found"}
        con.execute(
            "UPDATE leads SET trial_start=datetime('now'), stage='bofu', updated_at=datetime('now') WHERE id=?",
            (lead_id,),
        )
        con.execute(
            "INSERT INTO events (lead_id, event, payload) VALUES (?,?,?)",
            (lead_id, "trial_started", json.dumps({"day": 0})),
        )
    msg = await send_whatsapp(lead_id, "onboarding_d0")
    return {"lead_id": lead_id, "trial_started": True, "whatsapp_sent": msg.get("template")}


async def gen_product_description(product_name: str, details: str = "", target_audience: str = "") -> dict:
    """Generate a full Indian-market product listing: title, description, price, tags."""
    system = (
        "Product copywriter for Indian handmade and digital goods. "
        "Warm, benefit-led copy. Include ₹ price, mention UPI/COD payment. Build trust."
    )
    user = textwrap.dedent(f"""
        Product: {product_name}
        Details: {details or 'none'}
        Target buyer: {target_audience or 'Indian online shopper'}

        Write a product listing with:
        1. Title (≤10 words, SEO)
        2. Short description (2 sentences)
        3. Long description (100-120 words)
        4. Suggested price range in ₹
        5. Tags (5-8 comma-separated)

        Return ONLY JSON: {{"title":"...","short":"...","long":"...","price_range":"...","tags":"..."}}
    """)
    raw = await _generate(system, user)
    try:
        listing = json.loads(re.sub(r"```(?:json)?|```", "", raw).strip())
    except Exception:
        listing = {"title": product_name, "short": raw[:200], "long": raw, "price_range": "₹499-₹999", "tags": ""}
    listing["product_name"] = product_name
    return listing


async def gen_affiliate_code(lead_id: str) -> dict:
    """Generate a referral code for an existing user. Both parties get 3 months free."""
    with db() as con:
        lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            return {"error": f"Lead '{lead_id}' not found"}
        code = lead["affiliate_code"] or _aff_code(lead["name"] or lead_id)
        if not lead["affiliate_code"]:
            con.execute("UPDATE leads SET affiliate_code=? WHERE id=?", (code, lead_id))
    link = f"{BASE_URL}/signup?ref={code}"
    return {
        "lead_id": lead_id, "affiliate_code": code, "share_link": link,
        "whatsapp_share_text": f"Selling on makeforme.in — zero commission, ₹30/month. Get 3 months FREE with my link: {link} 🎁",
        "reward": "Both parties get 3 months Pro free",
    }


async def get_analytics() -> dict:
    """Return funnel conversion metrics from the local CRM."""
    with db() as con:
        stages = {r["stage"]: r["n"] for r in con.execute("SELECT stage, COUNT(*) as n FROM leads GROUP BY stage").fetchall()}
        total  = con.execute("SELECT COUNT(*) as n FROM leads").fetchone()["n"]
        wa     = con.execute("SELECT COUNT(*) as n FROM messages WHERE channel='whatsapp'").fetchone()["n"]
        recent = con.execute("SELECT COUNT(*) as n FROM leads WHERE created_at >= datetime('now','-7 days')").fetchone()["n"]
        affs   = con.execute("SELECT COUNT(*) as n FROM leads WHERE affiliate_code IS NOT NULL").fetchone()["n"]

    def pct(a, b): return f"{round(a/b*100)}%" if b else "—"
    t, m, b, r = stages.get("tofu",0), stages.get("mofu",0), stages.get("bofu",0), stages.get("retention",0)
    return {
        "total_leads": total, "new_last_7_days": recent, "by_stage": stages,
        "conversion_rates": {"tofu→mofu": pct(m,t), "mofu→bofu": pct(b,m), "bofu→retention": pct(r,b)},
        "whatsapp_messages_sent": wa, "active_affiliates": affs,
    }


async def add_to_community(lead_id: str) -> dict:
    """Send WhatsApp community invite + move lead to retention stage."""
    LINK = "https://chat.whatsapp.com/makersofindiastub"  # replace with real link
    with db() as con:
        lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead:
            return {"error": f"Lead '{lead_id}' not found"}
        con.execute("UPDATE leads SET stage='retention', updated_at=datetime('now') WHERE id=?", (lead_id,))
        con.execute("INSERT INTO events (lead_id, event, payload) VALUES (?,?,?)",
                    (lead_id, "community_invited", json.dumps({"link": LINK})))
    msg = f"Hey {lead['name'] or 'there'}! Join 'Makers of India' — 500+ Indian creators sharing tips: {LINK} 🇮🇳"
    result = await _send_whatsapp_api(lead["phone"] or "", msg)
    return {"lead_id": lead_id, "stage_updated": "retention", "community_link": LINK, "whatsapp_status": result.get("status")}


# ── TOOL REGISTRY ─────────────────────────────────────────────────────────────
# Ollama receives these as the "tools" parameter in /api/chat

TOOL_MAP = {
    "find_leads":             find_leads,
    "gen_ad_copy":            gen_ad_copy,
    "gen_blog_post":          gen_blog_post,
    "upsert_lead":            upsert_lead,
    "check_url":              check_url,
    "send_whatsapp":          send_whatsapp,
    "get_trial_status":       get_trial_status,
    "trigger_onboarding":     trigger_onboarding,
    "gen_product_description":gen_product_description,
    "gen_affiliate_code":     gen_affiliate_code,
    "get_analytics":          get_analytics,
    "add_to_community":       add_to_community,
}

OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_leads",
            "description": "Find Indian Instagram creators in a niche who sell via DMs — prime makeforme.in leads.",
            "parameters": {
                "type": "object",
                "properties": {
                    "niche":    {"type": "string", "description": "e.g. crochet, resin art, digital planners"},
                    "location": {"type": "string", "default": "India"},
                    "limit":    {"type": "integer", "default": 10},
                },
                "required": ["niche"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gen_ad_copy",
            "description": "Generate Instagram Reel hooks + ad copy for a maker niche. stage: tofu|mofu|bofu",
            "parameters": {
                "type": "object",
                "properties": {
                    "niche":    {"type": "string"},
                    "stage":    {"type": "string", "enum": ["tofu", "mofu", "bofu"], "default": "tofu"},
                    "variants": {"type": "integer", "default": 3},
                },
                "required": ["niche"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gen_blog_post",
            "description": "Draft an SEO blog post targeting Indian maker search queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic":        {"type": "string"},
                    "product_type": {"type": "string"},
                },
                "required": ["topic", "product_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_lead",
            "description": "Add or update a lead in the CRM. Returns the lead record and its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":      {"type": "string"},
                    "instagram": {"type": "string"},
                    "phone":     {"type": "string"},
                    "niche":     {"type": "string"},
                    "stage":     {"type": "string", "enum": ["tofu","mofu","bofu","retention"], "default": "tofu"},
                    "notes":     {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_url",
            "description": "Check if makeforme.in/<username> is available. Returns a retargeting message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                },
                "required": ["username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp",
            "description": (
                "Send a WhatsApp message to a lead. "
                "template options: pricing_calc | url_claim | onboarding_d0 | trial_d5 | trial_ending | first_sale"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id":  {"type": "string"},
                    "template": {"type": "string", "enum": ["pricing_calc","url_claim","onboarding_d0","trial_d5","trial_ending","first_sale"]},
                    "params":   {"type": "object"},
                },
                "required": ["lead_id", "template"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trial_status",
            "description": "Get a lead's trial day and the recommended next WhatsApp nudge to send.",
            "parameters": {
                "type": "object",
                "properties": {"lead_id": {"type": "string"}},
                "required": ["lead_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_onboarding",
            "description": "Start the 7-day trial onboarding sequence for a new signup.",
            "parameters": {
                "type": "object",
                "properties": {"lead_id": {"type": "string"}},
                "required": ["lead_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gen_product_description",
            "description": "Generate a full product listing (title, description, price, tags) for a maker's item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name":    {"type": "string"},
                    "details":         {"type": "string"},
                    "target_audience": {"type": "string"},
                },
                "required": ["product_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gen_affiliate_code",
            "description": "Generate a referral code for a user. Both parties get 3 months Pro free.",
            "parameters": {
                "type": "object",
                "properties": {"lead_id": {"type": "string"}},
                "required": ["lead_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_analytics",
            "description": "Return funnel conversion metrics: leads by stage, WhatsApp count, affiliates.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_community",
            "description": "Send a WhatsApp Makers of India community invite and move lead to retention stage.",
            "parameters": {
                "type": "object",
                "properties": {"lead_id": {"type": "string"}},
                "required": ["lead_id"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are the makeforme.in growth agent — an autonomous sales and marketing assistant for an Indian SaaS platform that helps small creators and makers build online stores.

Your job is to run the full TOFU→MOFU→BOFU→Retention funnel using the tools available to you.

Key facts:
- makeforme.in: zero-commission store builder, ₹30/month, UPI payments built-in
- Target audience: Indian solopreneurs selling handmade goods, digital products, or services
- Pain point: they currently sell via Instagram DMs or WhatsApp — messy, loses customers
- Primary channels: Instagram Reels (ads), WhatsApp (nurture), SEO blog (organic)

When given a task:
1. Break it into the right sequence of tool calls
2. Use tool results to inform next steps
3. Chain tools autonomously — don't ask for permission between steps
4. Give a concise final summary of what was done and what the next step is

Always think about which funnel stage a lead is in before choosing a tool."""


# ── OLLAMA AGENT LOOP ─────────────────────────────────────────────────────────

async def call_ollama(messages: list[dict]) -> dict:
    """Single call to Ollama /api/chat with tools enabled."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "tools": OLLAMA_TOOLS,
                "stream": False,
                "options": {"temperature": 0, "num_ctx": 16384},
            },
        )
        r.raise_for_status()
        return r.json()


async def run_agent(user_input: str, history: list[dict]) -> tuple[str, list[dict]]:
    """
    Run the agentic loop for one user turn.
    Returns (final_reply_text, updated_history).
    """
    messages = history + [{"role": "user", "content": user_input}]

    for round_num in range(MAX_TOOL_ROUNDS):
        response = await call_ollama(messages)
        msg = response["message"]
        messages.append(msg)

        # No tool calls → Ollama gave a final text answer
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return msg.get("content", ""), messages

        # Execute every tool call Ollama requested
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", {})

            # Ollama sometimes returns args as a JSON string
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except Exception:
                    raw_args = {}

            print(f"\n  [tool] {fn_name}({json.dumps(raw_args, ensure_ascii=False)})")

            fn = TOOL_MAP.get(fn_name)
            if fn:
                try:
                    result = await fn(**raw_args)
                except Exception as e:
                    result = {"error": str(e)}
            else:
                result = {"error": f"Unknown tool: {fn_name}"}

            result_str = json.dumps(result, ensure_ascii=False, indent=2)
            print(f"  [result] {result_str[:300]}{'...' if len(result_str)>300 else ''}")

            messages.append({
                "role": "tool",
                "content": result_str,
            })

    return "Max tool rounds reached.", messages


# ── CHAT LOOP ─────────────────────────────────────────────────────────────────

async def main():
    init_db()

    print("=" * 60)
    print("  makeforme.in Growth Agent  (Ollama · local)")
    print(f"  Model: {MODEL}  |  DB: {DB_PATH.name}")
    print("=" * 60)
    print("  Type a task. Examples:")
    print('  • "Find 5 crochet sellers in India and add them to CRM"')
    print('  • "Write 3 ad copy variants for resin art sellers"')
    print('  • "Show me funnel analytics"')
    print('  • "Generate a blog post on selling embroidery online"')
    print("  Type 'quit' to exit.\n")

    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("Bye!")
            break

        print("\nAgent thinking...\n")
        try:
            reply, history = await run_agent(user_input, history)
            print(f"\nAgent: {reply}\n")
        except httpx.ConnectError:
            print(f"\n[error] Cannot reach Ollama at {OLLAMA_URL}.")
            print("  Make sure Ollama is running:  ollama serve")
            print(f"  And the model is pulled:      ollama pull {MODEL}\n")
        except Exception as e:
            print(f"\n[error] {e}\n")


if __name__ == "__main__":
    asyncio.run(main())