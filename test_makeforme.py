"""
test_makeforme.py — run all tools directly without Claude Desktop.

Usage:
    python test_makeforme.py

This imports the tool functions directly and calls them, bypassing MCP transport.
You should see JSON output for each tool if everything is wired correctly.
"""

import asyncio
import json
import sys
import os

# ── Make sure the MCP server file is importable ──────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

# Import tool functions directly (not the MCP server machinery)
from makeforme_mcp import (
    init_db,
    find_leads,
    gen_ad_copy,
    upsert_lead,
    check_url,
    send_whatsapp,
    get_trial_status,
    trigger_onboarding,
    gen_product_description,
    gen_affiliate_code,
    get_analytics,
    add_to_community,
)

def pp(label: str, data: dict):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(json.dumps(data, ensure_ascii=False, indent=2))

async def run_tests():
    print("Initialising database...")
    init_db()
    print("  OK — makeforme_crm.db created/verified\n")

    # ── TOFU ─────────────────────────────────────────────────────────────────
    print(">>> TOFU TOOLS")

    result = await find_leads(niche="crochet", limit=4)
    pp("find_leads(niche='crochet')", result)

    result = await gen_ad_copy(niche="resin art", stage="tofu", variants=2)
    pp("gen_ad_copy(niche='resin art', stage='tofu')", result)

    # ── MOFU ─────────────────────────────────────────────────────────────────
    print("\n>>> MOFU TOOLS")

    lead = await upsert_lead(
        name="Priya Crochet",
        instagram="priyacrochet",
        phone="9876543210",
        niche="crochet",
        stage="mofu",
    )
    pp("upsert_lead(name='Priya Crochet')", lead)
    lead_id = lead["lead"]["id"]
    print(f"\n  lead_id to use in subsequent calls: {lead_id}")

    result = await check_url(username="priyacrochet")
    pp("check_url(username='priyacrochet')", result)

    result = await send_whatsapp(lead_id=lead_id, template="pricing_calc")
    pp(f"send_whatsapp(lead_id, 'pricing_calc')", result)

    result = await send_whatsapp(lead_id=lead_id, template="url_claim")
    pp(f"send_whatsapp(lead_id, 'url_claim')", result)

    # ── BOFU ─────────────────────────────────────────────────────────────────
    print("\n>>> BOFU TOOLS")

    result = await trigger_onboarding(lead_id=lead_id)
    pp(f"trigger_onboarding(lead_id)", result)

    result = await get_trial_status(lead_id=lead_id)
    pp(f"get_trial_status(lead_id)", result)

    result = await gen_product_description(
        product_name="Handmade Crochet Tote Bag",
        details="Cotton yarn, pastel colors, 12x14 inches",
        target_audience="Young women aged 18-35 who love boho fashion",
    )
    pp("gen_product_description('Handmade Crochet Tote Bag')", result)

    # ── RETENTION ─────────────────────────────────────────────────────────────
    print("\n>>> RETENTION TOOLS")

    result = await gen_affiliate_code(lead_id=lead_id)
    pp(f"gen_affiliate_code(lead_id)", result)

    result = await add_to_community(lead_id=lead_id)
    pp(f"add_to_community(lead_id)", result)

    result = await get_analytics()
    pp("get_analytics()", result)

    print("\n" + "="*60)
    print("  ALL TESTS COMPLETE")
    print("="*60)
    print("\nIf you saw JSON for each tool above, the server is working correctly.")
    print("Next step: register it in Claude Desktop config and relaunch.")
    print(f"\nClaude Desktop config path (Windows):")
    print(f"  %APPDATA%\\Claude\\claude_desktop_config.json")
    print(f'\nAdd this block:')
    print(json.dumps({
        "mcpServers": {
            "makeforme": {
                "command": "python",
                "args": [os.path.abspath("makeforme_mcp.py")]
            }
        }
    }, indent=2))

if __name__ == "__main__":
    asyncio.run(run_tests())