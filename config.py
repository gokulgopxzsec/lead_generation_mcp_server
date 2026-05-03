"""
Lead AI System — Configuration
Each Ollama model has a specialized role based on its strengths.
"""

OLLAMA_BASE_URL = "http://localhost:11434"

# ─── Model Role Assignments ────────────────────────────────────────────────────
# phi4-mini  → fastest, used for quick scoring & classification
# llama3     → creative writing, outreach & pitch generation
# qwen3:8b   → deep reasoning, audit reports, competitor analysis
# qwen3-8b-ctx8k → long-context batch processing, multi-lead analysis
# deephat    → domain-specific analysis, niche expertise

MODELS = {
    "scorer":    "phi4-mini:latest",       # Fast, lightweight scoring
    "writer":    "llama3:latest",          # Creative outreach messages
    "auditor":   "qwen3:8b",              # Deep business audit & reasoning
    "bulk":      "qwen3-8b-ctx8k:latest", # Long context, bulk processing
    "analyst":   "deephat:latest",        # Niche/domain analysis
}

# ─── Opportunity Scoring Weights ──────────────────────────────────────────────
SCORE_WEIGHTS = {
    "no_website":          40,
    "low_rating":          20,   # rating < 4.0
    "no_social":           20,   # no or inactive social media
    "reviews_no_site":     20,   # 50+ reviews but no website
    "very_low_rating":     10,   # bonus if rating < 3.5
    "zero_social":         10,   # bonus if literally no social presence
}

# ─── Score Thresholds ─────────────────────────────────────────────────────────
SCORE_HOT  = 80   # 🔥 Contact immediately
SCORE_WARM = 50   # 🟡 Queue for outreach
SCORE_COLD = 0    # 🟢 Low priority

# ─── Pricing Templates (₹) ────────────────────────────────────────────────────
PRICING = {
    "website_basic":     "₹10,000 – ₹25,000",
    "website_premium":   "₹25,000 – ₹50,000",
    "seo_monthly":       "₹5,000 – ₹15,000/month",
    "whatsapp_bot":      "₹5,000 setup + ₹3,000/month",
    "review_agent":      "₹2,000/month",
    "full_retainer":     "₹15,000 – ₹30,000/month",
}

# ─── Database ─────────────────────────────────────────────────────────────────
DB_PATH = "leads.db"

# ─── Outreach Channels ────────────────────────────────────────────────────────
CHANNELS = ["whatsapp", "email", "instagram_dm", "cold_call_script"]
