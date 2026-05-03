"""
main.py — Rich CLI for the Lead AI System.
Run directly without MCP client for terminal-based workflow.

Usage:
  python main.py                    # Interactive menu
  python main.py samples            # Load sample leads
  python main.py list               # List all leads
  python main.py audit 1            # Audit lead #1
  python main.py pitch 1 whatsapp   # Generate pitch for lead #1
  python main.py scrape "dentist" "Chalakudy Kerala"
  python main.py stats              # Pipeline stats
  python main.py bulk               # Bulk prioritize hot leads
  python main.py export             # Export to CSV
"""

import asyncio
import sys
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich import print as rprint
from rich.progress import Progress, SpinnerColumn, TextColumn

import database as db
from agents import scorer, auditor, writer, bulk, analyst
from scraper import scrape_and_save, load_sample_leads
from contact_finder import enrich_lead, enrich_all_leads
from config import MODELS, CHANNELS

console = Console()


def score_color(s: int) -> str:
    if s >= 80: return "bold red"
    if s >= 50: return "bold yellow"
    return "bold green"


def score_emoji(s: int) -> str:
    if s >= 80: return "🔥"
    if s >= 50: return "🟡"
    return "🟢"


async def cmd_stats():
    stats = await db.get_stats()
    console.print(Panel.fit(
        f"[bold]Total leads:[/bold] {stats['total']}\n"
        f"[red]🔥 Hot (80+):[/red]    {stats['hot']}\n"
        f"[yellow]🟡 Warm (50-79):[/yellow] {stats['warm']}\n"
        f"[cyan]🌐 No website:[/cyan]  {stats['no_website']}\n"
        f"[blue]🔍 Audited:[/blue]     {stats['audited']}\n"
        f"[green]✅ Contacted:[/green]  {stats['contacted']}",
        title="[bold cyan]Pipeline Stats[/bold cyan]"
    ))


async def cmd_list(filter_by: str = "all", limit: int = 30):
    min_score = {"hot": 80, "warm": 50}.get(filter_by, 0)
    leads = await db.list_leads(min_score=min_score, limit=limit)
    if filter_by == "warm":
        leads = [l for l in leads if l["score"] < 80]
    if filter_by == "cold":
        leads = [l for l in leads if l["score"] < 50]

    table = Table(title=f"Pipeline — {filter_by.upper()} ({len(leads)} leads)", show_lines=True)
    table.add_column("ID",       style="dim", width=4)
    table.add_column("Score",    width=7)
    table.add_column("Business", style="bold")
    table.add_column("Category", width=12)
    table.add_column("Rating",   width=8)
    table.add_column("Reviews",  width=8)
    table.add_column("Website",  width=8)
    table.add_column("Social",   width=10)
    table.add_column("Flags",    width=12)

    for l in leads:
        s = l["score"]
        flags = ("✅ " if l["audited"] else "") + ("📞 " if l["contacted"] else "")
        table.add_row(
            str(l["id"]),
            f"[{score_color(s)}]{score_emoji(s)} {s}[/]",
            l["name"],
            l.get("category", ""),
            f"★ {l.get('rating', '')}",
            str(l.get("reviews", "")),
            "✓" if l.get("website") else "[red]✗[/red]",
            l.get("social", ""),
            flags or "—"
        )
    console.print(table)


async def cmd_audit(lead_id: int):
    lead = await db.get_lead(lead_id)
    if not lead:
        console.print(f"[red]Lead {lead_id} not found[/red]")
        return

    console.print(f"\n[cyan]Auditing:[/cyan] {lead['name']} using [bold]{MODELS['auditor']}[/bold]...")

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console) as p:
        task = p.add_task("Running deep audit...", total=None)
        result = await auditor.run_audit(lead)
        await db.save_audit(lead_id, MODELS["auditor"], result)
        p.stop()

    urgency_color = {"high": "red", "medium": "yellow", "low": "green"}.get(result.get("urgency", "medium"), "yellow")

    console.print(Panel(
        f"[bold]Business:[/bold] {lead['name']} ({lead.get('category')})\n"
        f"[bold]Urgency:[/bold] [{urgency_color}]{result.get('urgency','').upper()}[/{urgency_color}]\n\n"
        f"[bold red]Problems:[/bold red]\n{result.get('problems','')}\n\n"
        f"[bold yellow]Revenue Loss:[/bold yellow] {result.get('revenueLoss','')}\n\n"
        f"[bold blue]Improvements:[/bold blue]\n{result.get('improvements','')}\n\n"
        f"[bold green]Quick Win:[/bold green] {result.get('quickWin','')}\n\n"
        f"[bold]Best Service to Pitch:[/bold] {result.get('bestService','')}\n"
        f"[bold]Deal Value:[/bold] {result.get('estimatedDealValue','')}\n\n"
        f"[bold]Competitor Risk:[/bold]\n{result.get('competitorRisk','')}",
        title=f"[bold cyan]AI Audit Report — {lead['name']}[/bold cyan]",
        border_style="cyan"
    ))


async def cmd_pitch(lead_id: int, channel: str = "whatsapp"):
    lead = await db.get_lead(lead_id)
    if not lead:
        console.print(f"[red]Lead {lead_id} not found[/red]")
        return

    audits = await db.get_audits(lead_id)
    audit_data = json.loads(audits[0]["raw_output"]) if audits else None

    console.print(f"\n[cyan]Generating {channel} pitch for:[/cyan] {lead['name']} using [bold]{MODELS['writer']}[/bold]...")

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console) as p:
        task = p.add_task(f"Writing {channel} message...", total=None)
        message = await writer.generate_pitch(lead, channel, audit_data)
        await db.save_pitch(lead_id, channel, MODELS["writer"], message)
        p.stop()

    console.print(Panel(
        message.strip(),
        title=f"[bold green]{channel.upper()} Pitch — {lead['name']}[/bold green]",
        border_style="green"
    ))


async def cmd_scrape(category: str, location: str, max_results: int = 20):
    console.print(f"\n[cyan]Scraping:[/cyan] {category} in {location}...")
    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console) as p:
        p.add_task(f"Searching {category} {location}...", total=None)
        result = await scrape_and_save(category, location, max_results)
        p.stop()
    console.print(Panel(
        f"Scraped: {result['scraped']}\nUnique: {result['unique']}\nSaved: {result['saved']}",
        title="[bold]Scrape Complete[/bold]"
    ))


async def cmd_bulk():
    leads = await db.list_leads(min_score=50, limit=20)
    if not leads:
        console.print("[yellow]No warm/hot leads found. Add some leads first.[/yellow]")
        return
    console.print(f"\n[cyan]Bulk analyzing {len(leads)} leads using [bold]{MODELS['bulk']}[/bold]...[/cyan]")
    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console) as p:
        p.add_task("Running batch analysis...", total=None)
        result = await bulk.prioritize_leads(leads)
        p.stop()
    console.print(Panel(
        json.dumps(result, indent=2),
        title="[bold cyan]Bulk Prioritization Results[/bold cyan]",
        border_style="cyan"
    ))


async def cmd_export():
    df = await db.export_leads_to_df()
    if df.empty:
        console.print("[yellow]No leads to export[/yellow]")
        return
    path = "leads_export.csv"
    df.to_csv(path, index=False)
    console.print(f"[green]✅ Exported {len(df)} leads to {path}[/green]")


async def cmd_enrich(lead_id: int = None):
    """Find email + phone for one lead or all hot/warm leads."""
    if lead_id:
        lead = await db.get_lead(lead_id)
        if not lead:
            console.print(f"[red]Lead {lead_id} not found[/red]")
            return

        console.print(f"\n[cyan]🔍 Searching contacts for:[/cyan] [bold]{lead['name']}[/bold]")
        console.print("[dim]Checking DuckDuckGo, Justdial, Sulekha, website...[/dim]\n")

        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console) as p:
            p.add_task("Scanning sources...", total=None)
            result = await enrich_lead(lead_id)

        # Display results
        found_phones = result.get("all_phones", [])
        found_emails = result.get("all_emails", [])
        sources = result.get("sources", [])

        table = Table(title=f"Contact Results — {lead['name']}", show_lines=True)
        table.add_column("Type",   style="bold cyan", width=10)
        table.add_column("Value",  style="bold white", width=35)
        table.add_column("Source", style="dim", width=20)

        if found_phones:
            for i, p_num in enumerate(found_phones):
                table.add_row(
                    "📞 Phone" if i == 0 else "",
                    f"[green]{p_num}[/green]" if i == 0 else p_num,
                    sources[0] if i == 0 and sources else ""
                )
        else:
            table.add_row("📞 Phone", "[red]Not found[/red]", "—")

        if found_emails:
            for i, email in enumerate(found_emails):
                table.add_row(
                    "📧 Email" if i == 0 else "",
                    f"[green]{email}[/green]" if i == 0 else email,
                    sources[-1] if i == 0 and sources else ""
                )
        else:
            table.add_row("📧 Email", "[red]Not found[/red]", "—")

        console.print(table)

        if sources:
            console.print(f"\n[dim]Sources checked: {', '.join(sources)}[/dim]")
        if found_phones or found_emails:
            console.print("[green]✅ Best contacts saved to lead record[/green]")
        else:
            console.print("[yellow]⚠️  No contacts found. Try manual search on Justdial.com[/yellow]")

    else:
        # Enrich all hot+warm leads
        console.print("\n[cyan]🔍 Enriching all hot + warm leads...[/cyan]")
        console.print("[dim]This takes ~30 seconds per lead to avoid rate limits.[/dim]\n")

        results = await enrich_all_leads(min_score=50)

        table = Table(title=f"Enrichment Results — {len(results)} leads", show_lines=True)
        table.add_column("Lead",  style="bold", width=28)
        table.add_column("Phone", style="green", width=20)
        table.add_column("Email", style="cyan",  width=30)
        table.add_column("Sources", style="dim", width=20)

        for r in results:
            table.add_row(
                r.get("lead_name", "")[:26],
                r.get("phone", "[red]not found[/red]"),
                r.get("email", "[red]not found[/red]"),
                ", ".join(r.get("sources", [])) or "—"
            )
        console.print(table)
        found = sum(1 for r in results if r.get("phone") or r.get("email"))
        console.print(f"\n[green]✅ Found contacts for {found}/{len(results)} leads[/green]")


async def interactive_menu():
    console.print(Panel.fit(
        "[bold cyan]Lead AI System[/bold cyan]\n"
        f"Models: scorer=[green]{MODELS['scorer']}[/green] | "
        f"writer=[yellow]{MODELS['writer']}[/yellow] | "
        f"auditor=[red]{MODELS['auditor']}[/red]\n"
        f"bulk=[blue]{MODELS['bulk']}[/blue] | "
        f"analyst=[magenta]{MODELS['analyst']}[/magenta]",
        title="🚀 Agentic Lead Automation"
    ))

    while True:
        console.print("\n[bold]What do you want to do?[/bold]")
        console.print(" 1. View pipeline stats")
        console.print(" 2. List leads")
        console.print(" 3. Load sample leads")
        console.print(" 4. Scrape new leads")
        console.print(" 5. Audit a lead")
        console.print(" 6. Generate pitch message")
        console.print(" 7. Bulk prioritize")
        console.print(" 8. Export to CSV")
        console.print(" 9. Niche analysis")
        console.print(" E. Find emails & phones (enrich)")
        console.print(" 0. Exit")

        choice = Prompt.ask("\nChoice", choices=["0","1","2","3","4","5","6","7","8","9","E","e"])

        if choice == "0":
            console.print("[cyan]Bye![/cyan]")
            break
        elif choice == "1":
            await cmd_stats()
        elif choice == "2":
            f = Prompt.ask("Filter", choices=["all","hot","warm","cold"], default="all")
            await cmd_list(f)
        elif choice == "3":
            count = await load_sample_leads()
            console.print(f"[green]✅ Loaded {count} sample leads[/green]")
        elif choice == "4":
            cat = Prompt.ask("Category (e.g. dentist, restaurant)")
            loc = Prompt.ask("Location (e.g. Chalakudy Kerala)")
            await cmd_scrape(cat, loc)
        elif choice == "5":
            await cmd_list("hot")
            lead_id = IntPrompt.ask("Enter Lead ID to audit")
            await cmd_audit(lead_id)
        elif choice == "6":
            await cmd_list("all")
            lead_id = IntPrompt.ask("Enter Lead ID")
            channel = Prompt.ask("Channel", choices=CHANNELS, default="whatsapp")
            await cmd_pitch(lead_id, channel)
        elif choice == "7":
            await cmd_bulk()
        elif choice == "8":
            await cmd_export()
        elif choice == "9":
            cat = Prompt.ask("Category")
            loc = Prompt.ask("Location")
            result = await analyst.niche_opportunity_score(cat, loc)
            console.print(Panel(result, title=f"[cyan]Niche: {cat} in {loc}[/cyan]"))
        elif choice in ("E", "e"):
            console.print(" [dim]Enter lead ID to enrich one, or press Enter for all hot+warm[/dim]")
            lead_input = Prompt.ask("Lead ID (or blank for all)", default="")
            lead_id = int(lead_input) if lead_input.strip().isdigit() else None
            await cmd_enrich(lead_id)


async def run_cli():
    await db.init_db()

    args = sys.argv[1:]
    if not args:
        await interactive_menu()
        return

    cmd = args[0].lower()

    if cmd == "stats":
        await cmd_stats()
    elif cmd == "list":
        f = args[1] if len(args) > 1 else "all"
        await cmd_list(f)
    elif cmd == "samples":
        count = await load_sample_leads()
        console.print(f"[green]✅ Loaded {count} sample leads[/green]")
    elif cmd == "audit":
        lead_id = int(args[1]) if len(args) > 1 else None
        if not lead_id:
            console.print("Usage: python main.py audit <lead_id>")
        else:
            await cmd_audit(lead_id)
    elif cmd == "pitch":
        lead_id = int(args[1]) if len(args) > 1 else None
        channel = args[2] if len(args) > 2 else "whatsapp"
        if not lead_id:
            console.print("Usage: python main.py pitch <lead_id> <channel>")
        else:
            await cmd_pitch(lead_id, channel)
    elif cmd == "scrape":
        category = args[1] if len(args) > 1 else "restaurant"
        location = args[2] if len(args) > 2 else "Chalakudy Kerala"
        await cmd_scrape(category, location)
    elif cmd == "bulk":
        await cmd_bulk()
    elif cmd == "export":
        await cmd_export()
    elif cmd == "enrich":
        lead_id = int(args[1]) if len(args) > 1 else None
        await cmd_enrich(lead_id)
    else:
        console.print(f"[red]Unknown command: {cmd}[/red]")
        console.print("Commands: stats | list | samples | audit | pitch | scrape | bulk | export")


if __name__ == "__main__":
    asyncio.run(run_cli())
