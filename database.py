"""
database.py — Async SQLite persistence for all leads, audits, pitches.
"""

import aiosqlite
import json
import asyncio
from datetime import datetime
from config import DB_PATH


CREATE_LEADS = """
CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    category    TEXT,
    phone       TEXT,
    email       TEXT,
    rating      REAL DEFAULT 0,
    reviews     INTEGER DEFAULT 0,
    website     TEXT,
    social      TEXT DEFAULT 'none',
    location    TEXT,
    source      TEXT DEFAULT 'manual',
    notes       TEXT,
    score       INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'new',
    contacted   INTEGER DEFAULT 0,
    audited     INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
)
"""

CREATE_AUDITS = """
CREATE TABLE IF NOT EXISTS audits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER REFERENCES leads(id),
    model_used  TEXT,
    problems    TEXT,
    revenue_loss TEXT,
    improvements TEXT,
    urgency     TEXT,
    quick_win   TEXT,
    raw_output  TEXT,
    created_at  TEXT
)
"""

CREATE_PITCHES = """
CREATE TABLE IF NOT EXISTS pitches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER REFERENCES leads(id),
    channel     TEXT,
    model_used  TEXT,
    message     TEXT,
    created_at  TEXT
)
"""

CREATE_SCRAPE_JOBS = """
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT,
    location    TEXT,
    leads_found INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'pending',
    created_at  TEXT
)
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_LEADS)
        await db.execute(CREATE_AUDITS)
        await db.execute(CREATE_PITCHES)
        await db.execute(CREATE_SCRAPE_JOBS)
        await db.commit()


async def add_lead(data: dict) -> int:
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO leads
              (name,category,phone,email,rating,reviews,website,social,
               location,source,notes,score,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("name"), data.get("category","Other"),
            data.get("phone",""), data.get("email",""),
            float(data.get("rating",0)), int(data.get("reviews",0)),
            data.get("website",""), data.get("social","none"),
            data.get("location",""), data.get("source","manual"),
            data.get("notes",""), data.get("score",0),
            "new", now, now
        ))
        await db.commit()
        return cur.lastrowid


async def get_lead(lead_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_leads(
    status: str = None,
    min_score: int = 0,
    category: str = None,
    limit: int = 100
) -> list[dict]:
    query = "SELECT * FROM leads WHERE score >= ?"
    params = [min_score]
    if status:
        query += " AND status=?"
        params.append(status)
    if category:
        query += " AND category=?"
        params.append(category)
    query += " ORDER BY score DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def update_lead(lead_id: int, data: dict):
    data["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in data)
    vals = list(data.values()) + [lead_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE leads SET {sets} WHERE id=?", vals)
        await db.commit()


def _to_str(val, fallback: str = "") -> str:
    """Safely convert any audit field to a SQLite-safe string."""
    if val is None:
        return fallback
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return "\n".join(str(v) for v in val)
    if isinstance(val, dict):
        return json.dumps(val)
    return str(val)


async def save_audit(lead_id: int, model: str, result: dict):
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO audits
              (lead_id,model_used,problems,revenue_loss,improvements,
               urgency,quick_win,raw_output,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            lead_id, model,
            _to_str(result.get("problems")),
            _to_str(result.get("revenueLoss")),
            _to_str(result.get("improvements")),
            _to_str(result.get("urgency"), "medium"),
            _to_str(result.get("quickWin")),
            json.dumps(result), now
        ))
        await db.execute(
            "UPDATE leads SET audited=1, updated_at=? WHERE id=?",
            (now, lead_id)
        )
        await db.commit()


async def save_pitch(lead_id: int, channel: str, model: str, message: str):
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO pitches (lead_id,channel,model_used,message,created_at)
            VALUES (?,?,?,?,?)
        """, (lead_id, channel, model, message, now))
        await db.commit()


async def get_audits(lead_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM audits WHERE lead_id=? ORDER BY created_at DESC",
            (lead_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        stats = {}
        for key, sql in {
            "total":     "SELECT COUNT(*) FROM leads",
            "hot":       f"SELECT COUNT(*) FROM leads WHERE score>=80",
            "warm":      f"SELECT COUNT(*) FROM leads WHERE score>=50 AND score<80",
            "audited":   "SELECT COUNT(*) FROM leads WHERE audited=1",
            "contacted": "SELECT COUNT(*) FROM leads WHERE contacted=1",
            "no_website":"SELECT COUNT(*) FROM leads WHERE (website IS NULL OR website='')",
        }.items():
            async with db.execute(sql) as cur:
                row = await cur.fetchone()
                stats[key] = row[0] if row else 0
        return stats


async def export_leads_to_df():
    import pandas as pd
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leads ORDER BY score DESC") as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return pd.DataFrame(rows) if rows else pd.DataFrame()
