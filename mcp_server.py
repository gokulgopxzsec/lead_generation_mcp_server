"""
mcp_server.py — Full MCP (Model Context Protocol) Server
Exposes all lead automation tools via stdio transport.

Connect this to Claude Desktop, Continue.dev, or any MCP-compatible client.

TOOLS EXPOSED:
  add_lead             → Add a business lead manually
  score_lead           → Calculate opportunity score (phi4-mini)
  audit_lead           → Deep AI audit (qwen3:8b)
  generate_pitch       → Outreach message (llama3)
  scrape_leads         → Scrape Google/Justdial for leads
  list_leads           → View pipeline with filters
  bulk_prioritize      → Rank leads by opportunity (qwen3-8b-ctx8k)
  competitor_analysis  → Competitor intelligence (deephat)
  niche_analysis       → Niche opportunity score (deephat)
  market_report        → Full market report for a city/niche
  export_leads         → Export to CSV/Excel
  follow_up            → Generate follow-up messages
  get_stats            → Pipeline statistics
  mark_contacted       → Update lead status
  load_samples         → Load sample Kerala business leads
"""

import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

import database as db
from agents import scorer, auditor, writer, bulk, analyst
from scraper import scrape_and_save, load_sample_leads
from config import CHANNELS, MODELS


app = Server("lead-automation-mcp")


# ════════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ════════════════════════════════════════════════════════════════════════════

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [

        types.Tool(
            name="add_lead",
            description="Add a new business lead to the pipeline. Automatically calculates opportunity score.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":     {"type": "string",  "description": "Business name"},
                    "category": {"type": "string",  "description": "Restaurant|Clinic|Gym|Salon|Builder|Retail|Other"},
                    "phone":    {"type": "string",  "description": "Contact phone number"},
                    "rating":   {"type": "number",  "description": "Google rating 1.0-5.0"},
                    "reviews":  {"type": "integer", "description": "Number of Google reviews"},
                    "website":  {"type": "string",  "description": "Website URL, empty if none"},
                    "social":   {"type": "string",  "description": "none|inactive|low|active"},
                    "location": {"type": "string",  "description": "City, State"},
                    "source":   {"type": "string",  "description": "Google Maps|Justdial|Manual"},
                    "notes":    {"type": "string",  "description": "Additional context"},
                },
                "required": ["name"]
            }
        ),

        types.Tool(
            name="score_lead",
            description="Calculate opportunity score (0-100) for a lead using phi4-mini AI. Score 80+ = Hot lead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer", "description": "Lead ID from database"}
                },
                "required": ["lead_id"]
            }
        ),

        types.Tool(
            name="audit_lead",
            description="Run deep AI business audit using qwen3:8b. Returns problems, revenue loss estimate, improvements, and best service to pitch.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer", "description": "Lead ID to audit"},
                    "save":    {"type": "boolean", "description": "Save audit to database (default: true)"}
                },
                "required": ["lead_id"]
            }
        ),

        types.Tool(
            name="generate_pitch",
            description="Generate personalized outreach message using llama3. Channels: whatsapp, email, instagram_dm, cold_call_script.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id":  {"type": "integer", "description": "Lead ID"},
                    "channel":  {"type": "string",  "description": "whatsapp|email|instagram_dm|cold_call_script"},
                    "use_audit":{"type": "boolean", "description": "Include audit findings in pitch (default: true)"}
                },
                "required": ["lead_id", "channel"]
            }
        ),

        types.Tool(
            name="scrape_leads",
            description="Scrape Google/Justdial for local businesses. Auto-scores and saves all results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category":    {"type": "string",  "description": "e.g. dentist, restaurant, gym"},
                    "location":    {"type": "string",  "description": "e.g. Chalakudy Kerala, Thrissur"},
                    "max_results": {"type": "integer", "description": "Max leads to scrape (default: 20)"}
                },
                "required": ["category", "location"]
            }
        ),

        types.Tool(
            name="list_leads",
            description="List leads from pipeline with optional filters. Sorted by opportunity score.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter":    {"type": "string",  "description": "hot|warm|cold|all (default: all)"},
                    "category":  {"type": "string",  "description": "Filter by category"},
                    "audited":   {"type": "boolean", "description": "Show only audited leads"},
                    "limit":     {"type": "integer", "description": "Max results (default: 20)"}
                }
            }
        ),

        types.Tool(
            name="bulk_prioritize",
            description="Use qwen3-8b-ctx8k to analyze up to 20 leads at once and identify the best targets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "hot|warm|all (default: hot)"},
                    "limit":  {"type": "integer", "description": "Number of leads to analyze (max 20)"}
                }
            }
        ),

        types.Tool(
            name="competitor_analysis",
            description="Use deephat to analyze competitor landscape for a specific business lead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer", "description": "Lead ID"}
                },
                "required": ["lead_id"]
            }
        ),

        types.Tool(
            name="niche_analysis",
            description="Use deephat to score the opportunity in a specific business niche + location.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "e.g. dental clinic, restaurant"},
                    "location": {"type": "string", "description": "e.g. Thrissur, Kerala"}
                },
                "required": ["category", "location"]
            }
        ),

        types.Tool(
            name="market_report",
            description="Generate a full market opportunity report for a location using qwen3-8b-ctx8k.",
            inputSchema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "e.g. Chalakudy, Kerala"},
                    "limit":    {"type": "integer", "description": "Leads to include in analysis"}
                },
                "required": ["location"]
            }
        ),

        types.Tool(
            name="export_leads",
            description="Export pipeline to CSV or Excel file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "format":   {"type": "string", "description": "csv|excel (default: csv)"},
                    "filename": {"type": "string", "description": "Output filename without extension"}
                }
            }
        ),

        types.Tool(
            name="follow_up",
            description="Generate a follow-up WhatsApp message for a lead that hasn't responded.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id":      {"type": "integer", "description": "Lead ID"},
                    "days_since":   {"type": "integer", "description": "Days since last contact (default: 3)"}
                },
                "required": ["lead_id"]
            }
        ),

        types.Tool(
            name="get_stats",
            description="Get pipeline statistics: total leads, hot/warm/cold counts, audited, contacted.",
            inputSchema={"type": "object", "properties": {}}
        ),

        types.Tool(
            name="mark_contacted",
            description="Mark a lead as contacted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer", "description": "Lead ID"},
                    "notes":   {"type": "string",  "description": "Contact notes"}
                },
                "required": ["lead_id"]
            }
        ),

        types.Tool(
            name="load_samples",
            description="Load 8 sample Kerala local business leads for testing.",
            inputSchema={"type": "object", "properties": {}}
        ),
    ]


# ════════════════════════════════════════════════════════════════════════════
# TOOL HANDLERS
# ════════════════════════════════════════════════════════════════════════════

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    await db.init_db()

    # ── add_lead ─────────────────────────────────────────────────────────────
    if name == "add_lead":
        lead_data = {k: arguments.get(k, "") for k in
                     ["name","category","phone","email","rating","reviews",
                      "website","social","location","source","notes"]}
        score_result = scorer.calculate_score(lead_data)
        lead_data["score"] = score_result["score"]
        lead_id = await db.add_lead(lead_data)
        return [types.TextContent(type="text", text=json.dumps({
            "success": True,
            "lead_id": lead_id,
            "score": score_result["score"],
            "label": score_result["label"],
            "reasons": score_result["reasons"],
            "message": f"Lead '{lead_data['name']}' added. Score: {score_result['score']}/100 ({score_result['label']})"
        }, indent=2))]

    # ── score_lead ────────────────────────────────────────────────────────────
    elif name == "score_lead":
        lead = await db.get_lead(arguments["lead_id"])
        if not lead:
            return [types.TextContent(type="text", text="Error: Lead not found")]
        result = await scorer.score_lead(lead)
        await db.update_lead(lead["id"], {"score": result["score"]})
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── audit_lead ────────────────────────────────────────────────────────────
    elif name == "audit_lead":
        lead = await db.get_lead(arguments["lead_id"])
        if not lead:
            return [types.TextContent(type="text", text="Error: Lead not found")]
        print(f"🤖 Running audit for {lead['name']} using {MODELS['auditor']}...")
        audit_result = await auditor.run_audit(lead)
        if arguments.get("save", True):
            await db.save_audit(lead["id"], MODELS["auditor"], audit_result)
        output = {
            "lead": lead["name"],
            "model": MODELS["auditor"],
            **audit_result
        }
        return [types.TextContent(type="text", text=json.dumps(output, indent=2))]

    # ── generate_pitch ────────────────────────────────────────────────────────
    elif name == "generate_pitch":
        lead = await db.get_lead(arguments["lead_id"])
        if not lead:
            return [types.TextContent(type="text", text="Error: Lead not found")]
        channel = arguments.get("channel", "whatsapp")
        # Get latest audit if requested
        audit_data = None
        if arguments.get("use_audit", True):
            audits = await db.get_audits(lead["id"])
            if audits:
                audit_data = json.loads(audits[0].get("raw_output", "{}"))
        print(f"✍️ Generating {channel} pitch for {lead['name']} using {MODELS['writer']}...")
        message = await writer.generate_pitch(lead, channel, audit_data)
        await db.save_pitch(lead["id"], channel, MODELS["writer"], message)
        return [types.TextContent(type="text", text=json.dumps({
            "lead": lead["name"],
            "channel": channel,
            "model": MODELS["writer"],
            "message": message.strip()
        }, indent=2))]

    # ── scrape_leads ──────────────────────────────────────────────────────────
    elif name == "scrape_leads":
        result = await scrape_and_save(
            arguments["category"],
            arguments["location"],
            arguments.get("max_results", 20)
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ── list_leads ────────────────────────────────────────────────────────────
    elif name == "list_leads":
        f = arguments.get("filter", "all")
        min_score = {"hot": 80, "warm": 50, "cold": 0, "all": 0}.get(f, 0)
        max_score = {"hot": 100, "warm": 79, "cold": 49}.get(f, 100)
        leads = await db.list_leads(
            min_score=min_score,
            category=arguments.get("category"),
            limit=arguments.get("limit", 20)
        )
        if f in ("warm",):
            leads = [l for l in leads if l["score"] < 80]
        if f == "cold":
            leads = [l for l in leads if l["score"] < 50]
        if arguments.get("audited"):
            leads = [l for l in leads if l["audited"]]

        summary = [{
            "id": l["id"], "name": l["name"], "category": l["category"],
            "location": l["location"], "phone": l["phone"],
            "rating": l["rating"], "reviews": l["reviews"],
            "website": "✓" if l["website"] else "✗",
            "score": l["score"],
            "label": "🔥HOT" if l["score"]>=80 else "🟡WARM" if l["score"]>=50 else "🟢COLD",
            "audited": "✓" if l["audited"] else "✗",
            "contacted": "✓" if l["contacted"] else "✗",
        } for l in leads]
        return [types.TextContent(type="text", text=json.dumps({
            "total": len(summary), "leads": summary
        }, indent=2))]

    # ── bulk_prioritize ───────────────────────────────────────────────────────
    elif name == "bulk_prioritize":
        f = arguments.get("filter", "hot")
        min_score = {"hot": 80, "warm": 50, "all": 0}.get(f, 50)
        leads = await db.list_leads(min_score=min_score, limit=arguments.get("limit", 20))
        print(f"🧠 Bulk analyzing {len(leads)} leads using {MODELS['bulk']}...")
        result = await bulk.prioritize_leads(leads)
        return [types.TextContent(type="text", text=json.dumps({
            "analyzed": len(leads),
            "model": MODELS["bulk"],
            **result
        }, indent=2))]

    # ── competitor_analysis ───────────────────────────────────────────────────
    elif name == "competitor_analysis":
        lead = await db.get_lead(arguments["lead_id"])
        if not lead:
            return [types.TextContent(type="text", text="Error: Lead not found")]
        print(f"🔎 Competitor analysis for {lead['name']} using {MODELS['analyst']}...")
        analysis = await analyst.competitor_analysis(lead)
        return [types.TextContent(type="text", text=json.dumps({
            "lead": lead["name"],
            "model": MODELS["analyst"],
            "analysis": analysis
        }, indent=2))]

    # ── niche_analysis ────────────────────────────────────────────────────────
    elif name == "niche_analysis":
        print(f"📊 Niche analysis: {arguments['category']} in {arguments['location']}...")
        result = await analyst.niche_opportunity_score(
            arguments["category"], arguments["location"]
        )
        return [types.TextContent(type="text", text=json.dumps({
            "category": arguments["category"],
            "location": arguments["location"],
            "model": MODELS["analyst"],
            "analysis": result
        }, indent=2))]

    # ── market_report ─────────────────────────────────────────────────────────
    elif name == "market_report":
        location = arguments["location"]
        leads = await db.list_leads(limit=arguments.get("limit", 50))
        loc_leads = [l for l in leads if location.lower() in (l.get("location") or "").lower()]
        if not loc_leads:
            loc_leads = leads[:20]
        print(f"📈 Market report for {location} using {MODELS['bulk']}...")
        report = await bulk.generate_market_report(loc_leads, location)
        return [types.TextContent(type="text", text=report)]

    # ── export_leads ──────────────────────────────────────────────────────────
    elif name == "export_leads":
        fmt = arguments.get("format", "csv")
        fname = arguments.get("filename", "leads_export")
        df = await db.export_leads_to_df()
        if df.empty:
            return [types.TextContent(type="text", text="No leads to export")]
        if fmt == "excel":
            path = f"{fname}.xlsx"
            df.to_excel(path, index=False)
        else:
            path = f"{fname}.csv"
            df.to_csv(path, index=False)
        return [types.TextContent(type="text", text=json.dumps({
            "exported": len(df),
            "file": path,
            "format": fmt
        }, indent=2))]

    # ── follow_up ─────────────────────────────────────────────────────────────
    elif name == "follow_up":
        lead = await db.get_lead(arguments["lead_id"])
        if not lead:
            return [types.TextContent(type="text", text="Error: Lead not found")]
        days = arguments.get("days_since", 3)
        message = await writer.generate_follow_up(lead, days)
        return [types.TextContent(type="text", text=json.dumps({
            "lead": lead["name"],
            "follow_up_message": message.strip()
        }, indent=2))]

    # ── get_stats ─────────────────────────────────────────────────────────────
    elif name == "get_stats":
        stats = await db.get_stats()
        return [types.TextContent(type="text", text=json.dumps(stats, indent=2))]

    # ── mark_contacted ────────────────────────────────────────────────────────
    elif name == "mark_contacted":
        update = {"contacted": 1, "status": "contacted"}
        if arguments.get("notes"):
            update["notes"] = arguments["notes"]
        await db.update_lead(arguments["lead_id"], update)
        return [types.TextContent(type="text", text=json.dumps({
            "success": True,
            "lead_id": arguments["lead_id"],
            "message": "Lead marked as contacted"
        }, indent=2))]

    # ── load_samples ──────────────────────────────────────────────────────────
    elif name == "load_samples":
        count = await load_sample_leads()
        return [types.TextContent(type="text", text=json.dumps({
            "loaded": count,
            "message": f"Loaded {count} sample Kerala business leads with scores"
        }, indent=2))]

    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

async def main():
    await db.init_db()
    print("🚀 Lead Automation MCP Server starting...")
    print(f"📡 Models: {json.dumps(MODELS, indent=2)}")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
