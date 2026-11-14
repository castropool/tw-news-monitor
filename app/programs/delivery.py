"""Scheduled drafting and Feishu delivery for the research programs.

Decisions recorded in the design doc (2026-11-14): output is for a team, in
the existing Feishu group; a daily digest each morning; weekly assessments on
Sunday; immediate alerts only for PLA activity anomalies and facts at formal
decision or higher on the evidence ladder; drafts are never sent, only
analyst-approved assessments.

Called from every regular run (``run_safe``); each part has its own switch
and does nothing until set:

- ``WEEKLY_AUTO_DRAFT=true``: on Sunday from ``WEEKLY_DRAFT_HOUR`` (default 9,
  Taipei) generate the draft for the week ending Saturday, for each program
  in ``WEEKLY_PROGRAMS`` (default P4), if it does not exist yet.
- ``PROGRAM_DELIVERY_ENABLED=true``: send to the regular notifier (the
  existing Feishu group when ``NOTIFIER=feishu``):
    * approved weekly assessments not yet sent;
    * the daily digest for yesterday, from ``DAILY_DIGEST_HOUR`` (default 8);
    * new alerts (classes in ``DAILY_ALERT_CLASSES``) for today and yesterday.

Every message is sent at most once (table ``program_deliveries``), and
``DISABLE_FEISHU_SEND`` still suppresses sending (nothing is marked sent,
so it goes out once sending is re-enabled).  Never blocks news delivery.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

from .. import programs as prog
from ..time_utils import TAIPEI

logger = logging.getLogger(__name__)
HEADER = "【台海戰略研判】"


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS program_deliveries (key TEXT PRIMARY KEY, kind TEXT NOT NULL, sent_at TEXT NOT NULL)"
    )
    conn.commit()


def already_sent(conn: sqlite3.Connection, key: str) -> bool:
    ensure_table(conn)
    return conn.execute("SELECT 1 FROM program_deliveries WHERE key = ?", (key,)).fetchone() is not None


def send_once(conn: sqlite3.Connection, key: str, kind: str, text: str, notifier, *, now: datetime) -> str:
    """Send ``text`` unless ``key`` was sent before. Returns sent / duplicate / disabled / failed."""
    if already_sent(conn, key):
        return "duplicate"
    if _flag("DISABLE_FEISHU_SEND"):
        return "disabled"
    try:
        ok = notifier.send_long(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Program delivery %s failed: %s", key, exc)
        return "failed"
    if ok is False:
        return "failed"
    conn.execute("INSERT INTO program_deliveries(key, kind, sent_at) VALUES (?, ?, ?)", (key, kind, now.isoformat()))
    conn.commit()
    return "sent"


# ── weekly ────────────────────────────────────────────────────────────


def weekly_programs() -> list[str]:
    raw = os.getenv("WEEKLY_PROGRAMS", "P4")
    return [p.strip() for p in raw.split(",") if p.strip() in prog.PROGRAM_IDS]


def last_saturday(today: date) -> date:
    return today - timedelta(days=(today.weekday() - 5) % 7 or 7)


def auto_draft(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """Sunday drafts for the week ending Saturday; returns programs drafted."""
    from ..pilot import weekly as pw

    local = now.astimezone(TAIPEI)
    if local.weekday() != 6 or local.hour < _int("WEEKLY_DRAFT_HOUR", 9):
        return []
    week_end = last_saturday(local.date())
    done = []
    for pid in weekly_programs():
        if pw.read_meta(week_end, pid) is not None:
            continue
        if pid == "P4":
            draft = pw.build_draft(conn, week_end, now=now)
        else:
            from . import weekly as gw

            draft = gw.build_draft(conn, pid, week_end, now=now)
        pw.write_draft(draft, week_end, now=now)
        done.append(pid)
    return done


def approved_assessments() -> list[tuple[str, date, str]]:
    """(program, week_end, approved text) for every approved assessment on disk."""
    from ..pilot import weekly as pw

    out = []
    for pid in prog.PROGRAM_IDS:
        folder = pw.assessment_dir(pid)
        for meta_path in sorted(folder.glob("*/meta.json")) if folder.exists() else []:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            approved = meta_path.parent / "approved.md"
            if meta.get("status") == "approved" and approved.exists():
                out.append((pid, date.fromisoformat(meta["week_end"]), approved.read_text(encoding="utf-8")))
    return out


def weekly_message(pid: str, week_end: date, text: str) -> str:
    name = prog.program(pid)["name"]
    return f"{HEADER}{pid} {name} 週報（已經分析員核准）截至 {week_end}\n\n{text}"


# ── daily digest and alerts ───────────────────────────────────────────


def alert_key(day: date, item: dict) -> str:
    digest = hashlib.sha256(f"{item['class']}|{item['text']}".encode("utf-8")).hexdigest()[:16]
    return f"alert:{day}:{digest}"


def alert_message(day: date, item: dict) -> str:
    src = f"\n來源：{item['source']}" if item.get("source") else ""
    return f"{HEADER}即時警示 {day}\n[{','.join(item['programs'])}] {item['text']}{src}"


def daily_message(day: date, markdown: str) -> str:
    return (f"{HEADER}每日戰略摘要 {day}\n（自動整理，含未經分析員審核的自動抽取事實）\n\n"
            + markdown.split("\n", 2)[-1].lstrip())


def run(db, *, now: datetime | None = None, notifier=None) -> dict:
    from . import daily

    now = now or datetime.now(timezone.utc)
    conn = db.conn
    result: dict = {}
    if _flag("WEEKLY_AUTO_DRAFT"):
        result["drafted"] = auto_draft(conn, now)
    if not _flag("PROGRAM_DELIVERY_ENABLED"):
        return result
    if notifier is None:
        from ..notifier import create_notifier

        notifier = create_notifier()
    sent: list[str] = []
    for pid, week_end, text in approved_assessments():
        key = f"weekly:{pid}:{week_end}"
        if send_once(conn, key, "weekly", weekly_message(pid, week_end, text), notifier, now=now) == "sent":
            sent.append(key)
    local = now.astimezone(TAIPEI)
    for day in (local.date() - timedelta(days=1), local.date()):
        for item in daily.collect(conn, day):
            if item["alert"]:
                key = alert_key(day, item)
                if send_once(conn, key, "alert", alert_message(day, item), notifier, now=now) == "sent":
                    sent.append(key)
    if local.hour >= _int("DAILY_DIGEST_HOUR", 8):
        day = local.date() - timedelta(days=1)
        key = f"daily:{day}"
        if not already_sent(conn, key):
            items = daily.collect(conn, day)
            markdown = daily.render(day, items, now)
            daily.write(day, markdown, items)
            if send_once(conn, key, "daily", daily_message(day, markdown), notifier, now=now) == "sent":
                sent.append(key)
    result["sent"] = sent
    return result


def run_safe(db, **kw) -> dict:
    if db is None:
        return {}
    try:
        return run(db, **kw)
    except Exception as exc:  # noqa: BLE001 - must never block news delivery
        logger.warning("Program scheduling/delivery failed safely: %s", exc)
        return {"status": "error", "error": str(exc)}
