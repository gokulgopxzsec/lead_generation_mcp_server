# Lead AI System — Setup & Usage Guide
# Agentic Lead Automation with Local Ollama Models on Windows

## ─── Model Role Assignment ────────────────────────────────────────────────────

| Model              | Role          | What it does                              |
|--------------------|---------------|-------------------------------------------|
| phi4-mini:latest   | SCORER        | Fast lead scoring & classification        |
| llama3:latest      | WRITER        | Personalized pitches & outreach messages  |
| qwen3:8b           | AUDITOR       | Deep business audit reports               |
| qwen3-8b-ctx8k     | BULK ANALYST  | Batch analysis of 20 leads at once        |
| deephat:latest     | ANALYST       | Niche & competitor intelligence           |


## ─── Step 1: Create Project Folder ──────────────────────────────────────────

Open PowerShell:

  mkdir C:\Users\gokul\lead_ai_system
  cd C:\Users\gokul\lead_ai_system

Copy all .py files into this folder.


## ─── Step 2: Install Python Dependencies ────────────────────────────────────

In PowerShell (run as Administrator if needed):

  pip install mcp requests beautifulsoup4 aiohttp aiosqlite rich typer pandas openpyxl httpx

Or use the requirements.txt:

  pip install -r requirements.txt


## ─── Step 3: Make sure Ollama is running ─────────────────────────────────────

  ollama serve

(Keep this running in a separate PowerShell window)

Verify your models are loaded:

  ollama list


## ─── Step 4A: Run as CLI Tool (Easiest) ─────────────────────────────────────

  cd C:\Users\gokul\lead_ai_system

  # Interactive menu (recommended for beginners)
  python main.py

  # Load 8 sample Kerala leads
  python main.py samples

  # View all leads
  python main.py list

  # View only hot leads (score 80+)
  python main.py list hot

  # Audit lead #1 (uses qwen3:8b)
  python main.py audit 1

  # Generate WhatsApp pitch for lead #1 (uses llama3)
  python main.py pitch 1 whatsapp

  # Generate email for lead #2
  python main.py pitch 2 email

  # Scrape dentists in Chalakudy
  python main.py scrape "dentist" "Chalakudy Kerala"

  # Bulk prioritize all hot leads (uses qwen3-8b-ctx8k)
  python main.py bulk

  # Export pipeline to CSV
  python main.py export

  # Pipeline stats
  python main.py stats


## ─── Step 4B: Connect to Claude Desktop (MCP) ───────────────────────────────

1. Install Claude Desktop from https://claude.ai/desktop

2. Open this file:
   C:\Users\gokul\AppData\Roaming\Claude\claude_desktop_config.json

3. Paste this content (update path if needed):
   {
     "mcpServers": {
       "lead-automation": {
         "command": "python",
         "args": ["C:\\Users\\gokul\\lead_ai_system\\mcp_server.py"]
       }
     }
   }

4. Restart Claude Desktop

5. Now in Claude Desktop you can say:
   - "Load sample leads and show me the pipeline stats"
   - "Audit lead number 3"
   - "Generate a WhatsApp pitch for the restaurant with the lowest rating"
   - "Scrape dental clinics in Thrissur Kerala"
   - "Which lead should I contact first?"
   - "Generate a follow-up message for lead 2"
   - "Export all leads to CSV"


## ─── Workflow: Zero to First Client ─────────────────────────────────────────

Day 1 — Setup & Load Leads:
  python main.py samples        # Load sample leads
  python main.py stats          # Check pipeline
  python main.py list hot       # See hot leads

Day 1 — Audit Top Leads:
  python main.py audit 1        # Deep audit (qwen3:8b)
  python main.py audit 2
  python main.py audit 5

Day 1 — Generate Pitches:
  python main.py pitch 1 whatsapp
  python main.py pitch 1 email

Day 2 — Scrape Real Leads:
  python main.py scrape "dentist" "Chalakudy Kerala"
  python main.py scrape "restaurant" "Thrissur Kerala"
  python main.py scrape "gym" "Ernakulam Kerala"

Day 2 — Bulk Analysis:
  python main.py bulk           # qwen3-8b-ctx8k ranks all leads

Day 3 — Export & Contact:
  python main.py export         # Get CSV
  # Open CSV, copy pitches, send via WhatsApp


## ─── MCP Tools Available ─────────────────────────────────────────────────────

When connected to Claude Desktop, these tools are available:

  add_lead             → Add a business lead
  score_lead           → Score with phi4-mini
  audit_lead           → Deep audit with qwen3:8b
  generate_pitch       → Outreach with llama3
  scrape_leads         → Auto-scrape Google/Justdial
  list_leads           → View pipeline
  bulk_prioritize      → Rank with qwen3-8b-ctx8k
  competitor_analysis  → Compete intel with deephat
  niche_analysis       → Niche score with deephat
  market_report        → Full market report
  export_leads         → CSV/Excel export
  follow_up            → Follow-up messages
  get_stats            → Pipeline overview
  mark_contacted       → Update status
  load_samples         → Sample Kerala leads


## ─── Troubleshooting ─────────────────────────────────────────────────────────

Q: "Connection refused" error
A: Run "ollama serve" in a separate PowerShell window first

Q: Model response is slow
A: phi4-mini is fastest — temporarily set all models to phi4-mini in config.py

Q: "Module not found" error
A: Run: pip install mcp aiosqlite rich httpx beautifulsoup4

Q: MCP not connecting to Claude Desktop
A: Check the path in claude_desktop_config.json — use double backslashes \\

Q: Scraper returns 0 results
A: DuckDuckGo may rate-limit. Wait 60 seconds and try again, or add leads manually.
