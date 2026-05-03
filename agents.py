"""
agents.py — Multi-agent system.
Each agent uses the right Ollama model for its specialized task.

AGENT ROSTER:
  ScorerAgent   → phi4-mini    (fast, lightweight decisions)
  WriterAgent   → llama3       (creative outreach & pitch messages)
  AuditorAgent  → qwen3:8b     (deep business intelligence & audit)
  BulkAgent     → qwen3-8b-ctx8k (batch processing, long context)
  AnalystAgent  → deephat      (competitor & niche analysis)
"""

import json
import re
from ollama_client import ollama
from config import SCORE_WEIGHTS, SCORE_HOT, SCORE_WARM, PRICING


# ════════════════════════════════════════════════════════════════════════════
# 1. SCORER AGENT  —  phi4-mini
# ════════════════════════════════════════════════════════════════════════════

class ScorerAgent:
    """
    Fast lead scoring using phi4-mini.
    Calculates rule-based score + AI validation.
    """

    def calculate_score(self, lead: dict) -> dict:
        score = 0
        reasons = []

        has_website = bool(lead.get("website", "").strip())
        rating = float(lead.get("rating") or 0)
        reviews = int(lead.get("reviews") or 0)
        social = lead.get("social", "none")

        if not has_website:
            score += SCORE_WEIGHTS["no_website"]
            reasons.append("No website (+40)")

        if rating > 0 and rating < 4.0:
            score += SCORE_WEIGHTS["low_rating"]
            reasons.append(f"Low rating {rating} (+20)")

        if rating > 0 and rating < 3.5:
            score += SCORE_WEIGHTS["very_low_rating"]
            reasons.append("Very low rating bonus (+10)")

        if social in ("none", "inactive"):
            score += SCORE_WEIGHTS["no_social"]
            reasons.append(f"Social: {social} (+20)")

        if social == "none":
            score += SCORE_WEIGHTS["zero_social"]
            reasons.append("Zero social bonus (+10)")

        if reviews >= 50 and not has_website:
            score += SCORE_WEIGHTS["reviews_no_site"]
            reasons.append(f"{reviews} reviews, no site (+20)")

        score = min(100, score)
        label = "🔥 HOT" if score >= SCORE_HOT else "🟡 WARM" if score >= SCORE_WARM else "🟢 COLD"

        return {
            "score": score,
            "label": label,
            "reasons": reasons,
            "priority": "immediate" if score >= SCORE_HOT else "queue" if score >= SCORE_WARM else "low"
        }

    async def ai_validate(self, lead: dict, base_score: dict) -> str:
        """Ask phi4-mini for a quick second opinion on the lead."""
        prompt = f"""Rate this local business lead opportunity. Be brief (1-2 sentences max).

Business: {lead.get('name')} | {lead.get('category')} | {lead.get('location')}
Rating: {lead.get('rating')} ({lead.get('reviews')} reviews)
Website: {'YES' if lead.get('website') else 'NONE'}
Social: {lead.get('social', 'unknown')}
Rule-based score: {base_score['score']}/100

Give ONE sentence: is this a good sales target? Why or why not?"""

        return await ollama.score(prompt)

    async def score_lead(self, lead: dict) -> dict:
        base = self.calculate_score(lead)
        validation = await self.ai_validate(lead, base)
        base["ai_note"] = validation.strip()
        return base


# ════════════════════════════════════════════════════════════════════════════
# 2. AUDITOR AGENT  —  qwen3:8b
# ════════════════════════════════════════════════════════════════════════════

class AuditorAgent:
    """
    Deep business audit using qwen3:8b.
    Produces structured JSON audit reports.
    """

    async def run_audit(self, lead: dict) -> dict:
        system = """You are a senior digital marketing consultant specializing in local business growth in India.
Analyze businesses and produce JSON audit reports. Be specific, realistic, and actionable.
Always respond with valid JSON only — no markdown, no extra text."""

        user = f"""Audit this local business and return a JSON object:

Business:
  Name: {lead.get('name')}
  Category: {lead.get('category')}
  Location: {lead.get('location', 'India')}
  Phone: {lead.get('phone', 'unknown')}
  Google Rating: {lead.get('rating', 'unknown')} stars ({lead.get('reviews', 0)} reviews)
  Website: {lead.get('website') or 'NONE — no website exists'}
  Social media: {lead.get('social', 'unknown')}
  Notes: {lead.get('notes', '—')}

Return this exact JSON structure:
{{
  "problems": "3 specific online presence problems this business faces right now",
  "revenueLoss": "estimated monthly revenue loss in INR due to poor online presence (e.g. ₹20,000–₹35,000/month lost)",
  "improvements": "top 3 concrete improvements with expected impact",
  "urgency": "low|medium|high",
  "quickWin": "one free action they can take in 30 minutes to see results",
  "bestService": "which service to pitch first: website|seo|whatsapp_bot|review_agent|full_retainer",
  "estimatedDealValue": "realistic monthly retainer value in INR for this specific business",
  "competitorRisk": "brief note on what competitors are doing that this business is missing"
}}"""

        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user}
        ]

        raw = await ollama.audit(messages)
        try:
            # Strip qwen3 <think>...</think> reasoning blocks
            clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
            # Strip markdown code fences
            clean = re.sub(r"```json|```", "", clean).strip()
            # Extract JSON object even if surrounded by extra text
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                clean = match.group(0)
            result = json.loads(clean)

            # Normalize: ensure all fields are strings (qwen3 may return lists)
            str_fields = ["problems", "revenueLoss", "improvements",
                          "urgency", "quickWin", "bestService",
                          "estimatedDealValue", "competitorRisk"]
            for field in str_fields:
                val = result.get(field)
                if isinstance(val, list):
                    result[field] = "\n".join(str(v) for v in val)
                elif isinstance(val, dict):
                    result[field] = json.dumps(val)
                elif val is None:
                    result[field] = ""

        except (json.JSONDecodeError, AttributeError):
            result = {
                "problems": raw[:500],
                "revenueLoss": "Unable to estimate",
                "improvements": "See raw output below",
                "urgency": "medium",
                "quickWin": "Claim Google Business Profile",
                "bestService": "website",
                "estimatedDealValue": "₹5,000\u2013\u201515,000/month",
                "competitorRisk": "Unknown",
                "_raw": raw
            }
        return result


# ════════════════════════════════════════════════════════════════════════════
# 3. WRITER AGENT  —  llama3
# ════════════════════════════════════════════════════════════════════════════

class WriterAgent:
    """
    Personalized outreach message generation using llama3.
    Generates WhatsApp, email, Instagram DM, and cold call scripts.
    """

    TEMPLATES = {
        "whatsapp": "Write a conversational WhatsApp message (3-4 sentences max). Casual but professional. Mention their specific situation. No emojis. End with a soft call-to-action.",
        "email":    "Write a cold email with subject line. Professional tone. 4-5 sentences. Mention their specific gap. Include clear CTA.",
        "instagram_dm": "Write an Instagram DM (2-3 sentences max). Very casual, friendly. Mention seeing their profile. Ask a question to start conversation.",
        "cold_call_script": "Write a 60-second cold call script. Include: opener, pain point mention, value prop, ask. Make it sound natural, not robotic.",
    }

    async def generate_pitch(self, lead: dict, channel: str, audit: dict = None) -> str:
        channel_instruction = self.TEMPLATES.get(channel, self.TEMPLATES["whatsapp"])

        audit_context = ""
        if audit:
            audit_context = f"""
Audit findings:
- Main problems: {audit.get('problems', '')}
- Revenue loss: {audit.get('revenueLoss', '')}
- Best service to pitch: {audit.get('bestService', 'website')}"""

        system = "You are an expert sales copywriter specializing in local business outreach in India. You write personalized, non-spammy messages that get responses. Never use generic templates — always reference something specific about the business."

        user = f"""{channel_instruction}

Business details:
  Name: {lead.get('name')}
  Category: {lead.get('category')}
  Location: {lead.get('location', 'India')}
  Rating: {lead.get('rating')} ({lead.get('reviews')} Google reviews)
  Website: {'None' if not lead.get('website') else lead.get('website')}
  Social media: {lead.get('social', 'none')}{audit_context}

Your name is from a digital growth agency. Write the {channel} message now:"""

        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user}
        ]

        return await ollama.write(messages)

    async def generate_follow_up(self, lead: dict, days_since: int = 3) -> str:
        messages = [
            {"role": "system", "content": "You write brief, friendly follow-up messages for sales outreach."},
            {"role": "user", "content": f"Write a short WhatsApp follow-up for {lead.get('name')} ({lead.get('category')}) who hasn't responded in {days_since} days. 2 sentences max. Friendly, no pressure."}
        ]
        return await ollama.write(messages)


# ════════════════════════════════════════════════════════════════════════════
# 4. BULK AGENT  —  qwen3-8b-ctx8k
# ════════════════════════════════════════════════════════════════════════════

class BulkAgent:
    """
    Batch processing using qwen3-8b-ctx8k (long context window).
    Analyzes multiple leads at once, finds patterns, prioritizes list.
    """

    async def prioritize_leads(self, leads: list[dict]) -> dict:
        """Analyze up to 20 leads at once and return ranked priority list."""
        if not leads:
            return {"ranked": [], "insights": "No leads to analyze"}

        leads_text = "\n".join([
            f"{i+1}. {l.get('name')} | {l.get('category')} | Rating:{l.get('rating')} | "
            f"Reviews:{l.get('reviews')} | Website:{'YES' if l.get('website') else 'NO'} | "
            f"Social:{l.get('social','none')} | Score:{l.get('score',0)}"
            for i, l in enumerate(leads[:20])
        ])

        messages = [
            {"role": "system", "content": "You are a lead generation strategist. Analyze lead lists and identify the highest-value targets. Respond in JSON only."},
            {"role": "user", "content": f"""Analyze these {len(leads)} business leads and return JSON:

{leads_text}

Return:
{{
  "top_3_indices": [list of 1-based indices of top 3 leads],
  "pattern": "common pattern/opportunity you notice across these leads",
  "fastest_close": "which lead index (1-based) is easiest to close and why",
  "avoid": "which lead index (1-based) to deprioritize and why",
  "market_insight": "one key market insight from this batch",
  "recommended_niche": "best niche to focus on based on this data"
}}"""
        }]

        raw = await ollama.bulk_analyze(messages)
        try:
            clean = re.sub(r"```json|```", "", raw).strip()
            return json.loads(clean)
        except Exception:
            return {"pattern": raw[:200], "top_3_indices": [1, 2, 3]}

    async def generate_market_report(self, leads: list[dict], location: str) -> str:
        """Generate a market analysis report for a location/niche."""
        summary = f"{len(leads)} leads in {location}"
        categories = {}
        for l in leads:
            cat = l.get("category", "Other")
            categories[cat] = categories.get(cat, 0) + 1

        cat_text = ", ".join(f"{k}:{v}" for k, v in categories.items())

        messages = [
            {"role": "system", "content": "You are a market research analyst specializing in SME digital adoption in India."},
            {"role": "user", "content": f"""Write a 200-word market opportunity report for:
Location: {location}
Leads analyzed: {summary}
Business breakdown: {cat_text}
Average score: {sum(l.get('score',0) for l in leads)//max(len(leads),1)}
No-website count: {sum(1 for l in leads if not l.get('website'))}

Include: market gap, best niche, revenue opportunity, recommended approach."""}
        ]

        return await ollama.bulk_analyze(messages)


# ════════════════════════════════════════════════════════════════════════════
# 5. ANALYST AGENT  —  deephat
# ════════════════════════════════════════════════════════════════════════════

class AnalystAgent:
    """
    Competitor & niche analysis using deephat.
    Finds gaps, competitor weaknesses, positioning opportunities.
    """

    async def competitor_analysis(self, lead: dict) -> str:
        messages = [
            {"role": "system", "content": "You are a competitive intelligence analyst for local businesses in India."},
            {"role": "user", "content": f"""Analyze the competitive landscape for:

Business: {lead.get('name')} ({lead.get('category')}) in {lead.get('location', 'India')}
Their weakness: {'No website' if not lead.get('website') else 'Poor online presence'}
Rating: {lead.get('rating')} stars

Give a 3-bullet competitor analysis:
1. What their likely competitors are doing online
2. The specific gap this business has vs competitors
3. The pitch angle to use — what should this business FEAR losing?

Be specific to {lead.get('category')} businesses in India."""}
        ]
        return await ollama.analyze(messages)

    async def niche_opportunity_score(self, category: str, location: str) -> str:
        messages = [
            {"role": "system", "content": "You analyze local business niches for digital service opportunity."},
            {"role": "user", "content": f"""Rate the opportunity for selling digital services to {category} businesses in {location}, India.

Score 1-10 on:
- Digital adoption gap (higher = more businesses without websites)
- Budget/willingness to pay
- Decision-making speed
- Competition from other agencies

Give brief reasoning and one SPECIFIC pitch angle for this niche."""}
        ]
        return await ollama.analyze(messages)


# ════════════════════════════════════════════════════════════════════════════
# Agent Registry
# ════════════════════════════════════════════════════════════════════════════

scorer  = ScorerAgent()
auditor = AuditorAgent()
writer  = WriterAgent()
bulk    = BulkAgent()
analyst = AnalystAgent()
