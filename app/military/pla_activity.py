"""Daily PLA activity counts from the Ministry of National Defense report.

MND publishes one report a day, "中共解放軍臺海周邊海、空域動態", covering
06:00 to 06:00 Taipei time.  Its body follows a fixed wording::

    一、日期：中華民國114年11月13日（星期四）0600時至中華民國114年11月14日（星期五）0600時止。
    二、臺海周邊海、空域動態：共機13架次、共艦6艘、公務船1艘。
    三、共機12架次逾越中線進入我北部、中部及西南空域。

Parsing works on page text, not on CSS classes, so a site redesign that keeps
the wording keeps working: list pages are scanned for links whose text names
the report, and detail pages are reduced to text before the regexes run.
Counts that the report does not mention stay ``None`` (unknown), never 0,
except where the report says none were detected (未偵獲).

Rows go to ``pla_activity_daily`` in the news DB (created here, not in
``Database.connect()``), one per report date, updated if MND revises the text.

The HTML handling has NOT yet been checked against the live site (it was not
reachable from the development environment), so the feature is off by
default.  On a machine that can reach MND, check it first::

    python -m app.military.pla_activity --check

then set ``PLA_ACTIVITY_ENABLED=true`` to collect during the regular run.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..election2026.hanzi_utils import to_traditional
from ..time_utils import TAIPEI

logger = logging.getLogger(__name__)

DEFAULT_LIST_URL = "https://www.mnd.gov.tw/news/plaactlist"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
MAX_DETAIL_PAGES = 5
REPORT_TITLE_RE = re.compile(r"臺海周邊海[、，,]?\s*空域動態")
ROC_DATE_RE = re.compile(r"(?:中華)?民國\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
AIRCRAFT_RE = re.compile(r"共機\s*(\d+)\s*架次")
# A sortie count followed by 逾越/進入 before the sentence ends or another
# count starts: "共機12架次逾越中線進入我北部…空域".
CROSSED_RE = re.compile(r"(\d+)\s*架次(?:(?!架次)[^。；\n])*?(?:逾越|進入)")
VESSELS_RE = re.compile(r"共艦\s*(\d+)\s*艘")
OFFICIAL_SHIPS_RE = re.compile(r"公務船\s*(\d+)\s*艘")
BALLOONS_RE = re.compile(r"氣球\s*(\d+)\s*(?:顆|枚|個|架)")
NONE_AIRCRAFT_RE = re.compile(r"未(?:偵獲|發現)[^。]*?共機")
NONE_VESSELS_RE = re.compile(r"未(?:偵獲|發現)[^。]*?共艦")
BODY_START_RE = re.compile(r"一\s*[、.．]\s*日期")
SECTION_RE = re.compile(r"(?:^|\n|。)\s*[一二三四五]\s*[、.．]")


@dataclass(slots=True)
class PlaReport:
    report_date: str  # end date of the 06:00–06:00 window, YYYY-MM-DD
    period_start: str | None
    period_end: str | None
    aircraft: int | None
    aircraft_crossed: int | None
    vessels: int | None
    official_ships: int | None
    balloons: int | None
    source_url: str
    raw_text: str


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return to_traditional(text).replace("台海", "臺海")


def _roc_to_date(match: re.Match) -> date | None:
    year, month, day = (int(g) for g in match.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError:
        return None


def _first_int(pattern: re.Pattern, text: str) -> int | None:
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def parse_report_text(text: str, source_url: str = "") -> PlaReport | None:
    """Counts from a report's text; None when it is not a PLA activity report."""
    norm = _normalize(text)
    start = BODY_START_RE.search(norm)
    body = norm[start.start():] if start else norm
    body = body[:3000]
    dates = [d for d in (_roc_to_date(m) for m in ROC_DATE_RE.finditer(body)) if d]
    if not dates:
        return None
    aircraft = _first_int(AIRCRAFT_RE, body)
    vessels = _first_int(VESSELS_RE, body)
    if aircraft is None and NONE_AIRCRAFT_RE.search(body):
        aircraft = 0
    if vessels is None and NONE_VESSELS_RE.search(body):
        vessels = 0
    if aircraft is None and vessels is None:
        return None
    match = CROSSED_RE.search(body)
    if match:
        crossed: int | None = int(match.group(1))
    elif aircraft == 0 or not re.search(r"逾越|進入", body):
        crossed = 0  # no sorties, or the report lists no crossings
    else:
        crossed = None  # crossings mentioned but not in a recognised form
    period_start = dates[0]
    period_end = dates[1] if len(dates) > 1 else dates[0]
    sections = list(SECTION_RE.finditer(body))
    end = body.find("。", sections[-1].end()) if sections else -1
    raw = body[: end + 1] if end > 0 else body[:600]
    return PlaReport(
        report_date=period_end.isoformat(),
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        aircraft=aircraft,
        aircraft_crossed=crossed,
        vessels=vessels,
        official_ships=_first_int(OFFICIAL_SHIPS_RE, body),
        balloons=_first_int(BALLOONS_RE, body),
        source_url=source_url,
        raw_text=raw.strip(),
    )


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return soup.get_text("\n", strip=True)


def report_links(html: str, base_url: str) -> list[str]:
    """Detail links whose text (or title) names the daily report, page order."""
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = _normalize(anchor.get_text(" ", strip=True) + " " + str(anchor.get("title") or ""))
        if not REPORT_TITLE_RE.search(label):
            continue
        href = urljoin(base_url, anchor["href"].strip())
        if href not in links and href.rstrip("/") != base_url.rstrip("/"):
            links.append(href)
    return links


# ── storage ───────────────────────────────────────────────────────────


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pla_activity_daily (
            report_date TEXT PRIMARY KEY,
            period_start TEXT,
            period_end TEXT,
            aircraft INTEGER,
            aircraft_crossed INTEGER,
            vessels INTEGER,
            official_ships INTEGER,
            balloons INTEGER,
            source_url TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            raw_sha256 TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def save_reports(conn: sqlite3.Connection, reports: list[PlaReport], *, now: datetime | None = None) -> int:
    """Insert new dates; update a date only if MND changed its text."""
    ensure_table(conn)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    before = conn.total_changes
    for r in reports:
        sha = hashlib.sha256(r.raw_text.encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO pla_activity_daily(report_date, period_start, period_end, aircraft, "
            "aircraft_crossed, vessels, official_ships, balloons, source_url, raw_text, raw_sha256, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(report_date) DO UPDATE SET period_start=excluded.period_start, "
            "period_end=excluded.period_end, aircraft=excluded.aircraft, "
            "aircraft_crossed=excluded.aircraft_crossed, vessels=excluded.vessels, "
            "official_ships=excluded.official_ships, balloons=excluded.balloons, "
            "source_url=excluded.source_url, raw_text=excluded.raw_text, "
            "raw_sha256=excluded.raw_sha256, fetched_at=excluded.fetched_at "
            "WHERE pla_activity_daily.raw_sha256 != excluded.raw_sha256",
            (r.report_date, r.period_start, r.period_end, r.aircraft, r.aircraft_crossed,
             r.vessels, r.official_ships, r.balloons, r.source_url, r.raw_text, sha, stamp),
        )
    conn.commit()
    return conn.total_changes - before


def latest_report_date(conn: sqlite3.Connection) -> str | None:
    ensure_table(conn)
    row = conn.execute("SELECT MAX(report_date) FROM pla_activity_daily").fetchone()
    return row[0] if row else None


def recent_reports(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    ensure_table(conn)
    cur = conn.execute(
        "SELECT report_date, aircraft, aircraft_crossed, vessels, official_ships, balloons, source_url "
        "FROM pla_activity_daily ORDER BY report_date DESC LIMIT ?",
        (max(1, days),),
    )
    keys = [c[0] for c in cur.description]
    return [dict(zip(keys, row)) for row in cur.fetchall()]


# ── fetching ──────────────────────────────────────────────────────────


def fetch_reports(
    list_url: str = DEFAULT_LIST_URL,
    *,
    client: httpx.Client | None = None,
    max_pages: int = MAX_DETAIL_PAGES,
    skip_dates_through: str | None = None,
) -> list[PlaReport]:
    """Newest reports from the list page (detail pages fetched up to max_pages)."""
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    try:
        listing = client.get(list_url)
        listing.raise_for_status()
        reports: list[PlaReport] = []
        # Some layouts print the newest report in full on the list page itself.
        inline = parse_report_text(page_text(listing.text), str(listing.url))
        links = report_links(listing.text, str(listing.url))
        if inline is not None and not links:
            return [inline]
        for href in links[:max_pages]:
            try:
                detail = client.get(href)
                detail.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("PLA activity detail fetch failed %s: %s", href, exc)
                continue
            report = parse_report_text(page_text(detail.text), href)
            if report is None:
                logger.warning("PLA activity page not parsed: %s", href)
                continue
            reports.append(report)
            if skip_dates_through and report.report_date <= skip_dates_through:
                break  # list is newest first; the rest are already stored
        return reports
    finally:
        if own:
            client.close()


NEXT_LABELS = frozenset({"下一頁", "下一页", "下頁", "次頁", "next", "›", "»", ">"})


def next_page_link(html: str, base_url: str) -> str | None:
    """The list's "next page" link (rel=next, a next-like label or class)."""
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        label = anchor.get_text(" ", strip=True).lower()
        rel = [r.lower() for r in (anchor.get("rel") or [])]
        classes = " ".join(anchor.get("class") or []).lower()
        if "next" in rel or label in NEXT_LABELS or "next" in classes:
            return urljoin(base_url, href)
    return None


def backfill(
    since: date,
    list_url: str = DEFAULT_LIST_URL,
    *,
    client: httpx.Client | None = None,
    max_list_pages: int = 200,
    delay_seconds: float = 1.0,
    sleep=None,
) -> tuple[list[PlaReport], str]:
    """Walk list pages newest to oldest until reports predate ``since``.

    Returns (reports, stop_reason).  One request per second by default, so a
    two-year backfill takes roughly 15 minutes.
    """
    import time

    pause = sleep or time.sleep
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    reports: list[PlaReport] = []
    seen_pages: set[str] = set()
    seen_links: set[str] = set()
    url: str | None = list_url
    reason = "max_list_pages"
    try:
        for _ in range(max_list_pages):
            if url is None:
                reason = "no_next_page"
                break
            if url in seen_pages:
                reason = "page_loop"
                break
            seen_pages.add(url)
            listing = client.get(url)
            listing.raise_for_status()
            links = [h for h in report_links(listing.text, str(listing.url)) if h not in seen_links]
            for href in links:
                seen_links.add(href)
                pause(delay_seconds)
                try:
                    detail = client.get(href)
                    detail.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("PLA backfill detail failed %s: %s", href, exc)
                    continue
                report = parse_report_text(page_text(detail.text), href)
                if report is None:
                    logger.warning("PLA backfill page not parsed: %s", href)
                    continue
                reports.append(report)
            if reports and min(r.report_date for r in reports) < since.isoformat():
                reason = "reached_since"
                break
            url = next_page_link(listing.text, str(listing.url))
            pause(delay_seconds)
        return [r for r in reports if r.report_date >= since.isoformat()], reason
    finally:
        if own:
            client.close()


def missing_dates(conn: sqlite3.Connection, since: date, until: date) -> list[str]:
    """Report dates in [since, until] with no stored row (documented gaps)."""
    from datetime import timedelta

    ensure_table(conn)
    have = {r[0] for r in conn.execute(
        "SELECT report_date FROM pla_activity_daily WHERE report_date BETWEEN ? AND ?",
        (since.isoformat(), until.isoformat()),
    )}
    out, day = [], since
    while day <= until:
        if day.isoformat() not in have:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def enabled() -> bool:
    return os.getenv("PLA_ACTIVITY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def collect_safe(db, *, now: datetime | None = None, fetcher=None) -> dict:
    """Regular-run hook: at most one list fetch per run, none once today is stored."""
    if db is None or not enabled():
        return {"status": "disabled"}
    try:
        today = (now or datetime.now(timezone.utc)).astimezone(TAIPEI).date().isoformat()
        latest = latest_report_date(db.conn)
        if latest is not None and latest >= today:
            return {"status": "up_to_date", "latest": latest}
        fetch = fetcher or fetch_reports
        reports = fetch(os.getenv("PLA_ACTIVITY_LIST_URL", DEFAULT_LIST_URL), skip_dates_through=latest)
        written = save_reports(db.conn, reports, now=now)
        logger.info("PLA activity: parsed=%d stored=%d", len(reports), written)
        return {"status": "ok", "parsed": len(reports), "stored": written}
    except Exception as exc:  # noqa: BLE001 - must never block news delivery
        logger.warning("PLA activity collection failed safely: %s", exc)
        return {"status": "error", "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.military.pla_activity",
                                     description="MND daily PLA activity reports")
    parser.add_argument("--check", action="store_true", help="fetch and print parsed reports; store nothing")
    parser.add_argument("--collect", action="store_true", help="fetch and store new reports")
    parser.add_argument("--show", type=int, metavar="DAYS", help="print the last DAYS stored reports")
    parser.add_argument("--backfill-days", type=int, metavar="DAYS",
                        help="walk list pages back DAYS days, store reports, then list missing dates")
    parser.add_argument("--url", default=os.getenv("PLA_ACTIVITY_LIST_URL", DEFAULT_LIST_URL))
    parser.add_argument("--pages", type=int, default=MAX_DETAIL_PAGES)
    parser.add_argument("--db", help="news DB path (default: the configured news DB)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.backfill_days:
        from datetime import timedelta

        from ..database import Database
        from ..settings import get_settings

        today = datetime.now(TAIPEI).date()
        since = today - timedelta(days=args.backfill_days)
        reports, reason = backfill(since, args.url)
        print(f"Backfill parsed {len(reports)} reports (stopped: {reason}).")
        with Database(args.db or get_settings().news_db_path) as db:
            print(f"Stored/updated: {save_reports(db.conn, reports)}")
            gaps = missing_dates(db.conn, since, today)
        print(f"Dates with no report since {since}: {len(gaps)}")
        for gap in gaps[:60]:
            print(f"  {gap}")
        return 0
    if args.check or args.collect:
        reports = fetch_reports(args.url, max_pages=args.pages)
        if not reports:
            print("No reports parsed. The page layout may differ from what the parser expects.")
            return 1
        for r in reports:
            print({k: v for k, v in asdict(r).items() if k != "raw_text"})
        if args.check:
            print(f"\nNewest report text:\n{reports[0].raw_text}")
            return 0
    from ..database import Database
    from ..settings import get_settings

    with Database(args.db or get_settings().news_db_path) as db:
        if args.collect:
            print(f"Stored/updated: {save_reports(db.conn, reports)}")
        for row in recent_reports(db.conn, args.show or 14):
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
