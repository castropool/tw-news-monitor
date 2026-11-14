"""Daily strategic digest and cross-program alerts (design phase 4).

For one day (default: yesterday, Taipei) across all programs:

- ``pla_anomaly``: P4 aircraft or crossing counts flagged spike/lull against
  the 90-day baseline;
- ``ladder_formal``: facts that reached a formal decision or higher on the
  evidence ladder (decision, resources, implementation, effect);
- ``cross_program``: articles tagged to two or more programs (the design's
  shared-knowledge cases, e.g. an arms sale that is P1, P3 and P4 evidence);
- ``revision_open``: open revision suggestions on any program's latest draft.

Which classes count as alerts is set by ``DAILY_ALERT_CLASSES`` (default
``pla_anomaly,ladder_formal``); everything else is listed in the digest.
Output is a local Markdown file under ``ASSESSMENT_ROOT``/daily; nothing is
sent anywhere, because the audience and alert cadence are still open
decisions in the design doc.

CLI::

    python -m app.programs.daily [--date 2026-11-13]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import programs as prog
from ..military import pla_baseline as pb
from ..pilot import claims as cl
from ..pilot.weekly import assessment_dir
from . import tagging

FORMAL_STAGES = ("decision", "resources", "implementation", "effect")
ALL_CLASSES = ("pla_anomaly", "ladder_formal", "cross_program", "revision_open")


def alert_classes() -> set[str]:
    raw = os.getenv("DAILY_ALERT_CLASSES", "pla_anomaly,ladder_formal")
    return {c.strip() for c in raw.split(",") if c.strip() in ALL_CLASSES}


def collect(conn: sqlite3.Connection, day: date) -> list[dict]:
    items: list[dict] = []
    series = pb.load_series(conn)
    for metric in ("aircraft", "aircraft_crossed"):
        r = pb.read_day(series, metric, day)
        if r.flag:
            kind = "異常偏高" if r.flag == "spike" else "異常偏低"
            items.append({"class": "pla_anomaly", "programs": ["P4"],
                          "text": f"{pb.LABELS[metric]} {r.value}（z={r.z:+.1f}，前90日日均 {r.baseline.mean:.1f}），{kind}"})
    facts = cl.claims_between(conn, day, day, program=None)
    labels = prog.load()["ladder_labels"]
    for f in facts:
        if f["ladder"] in FORMAL_STAGES:
            items.append({"class": "ladder_formal", "programs": [f["program"]],
                          "text": f"［{labels[f['ladder']]}］{f['actor'] or ''} {f['action'] or ''} — 「{f['quote']}」",
                          "source": f"{f['source_name']} {f['url']}"})
    tagging.ensure_table(conn)
    rows = conn.execute(
        "SELECT a.url, a.title, a.source_name, GROUP_CONCAT(t.program) FROM program_tags t JOIN articles a ON a.url = t.url "
        "WHERE substr(COALESCE(a.published_at, a.fetched_at), 1, 10) = ? GROUP BY a.url HAVING COUNT(*) >= 2",
        (day.isoformat(),),
    ).fetchall()
    for url, title, source, progs in rows:
        items.append({"class": "cross_program", "programs": sorted(progs.split(",")),
                      "text": title, "source": f"{source} {url}"})
    for pid in prog.PROGRAM_IDS:
        folder = assessment_dir(pid)
        metas = sorted(folder.glob("*/meta.json")) if folder.exists() else []
        if not metas:
            continue
        meta = json.loads(metas[-1].read_text(encoding="utf-8"))
        open_r = [r for r in meta.get("revisions") or [] if r.get("status") == "open"]
        for r in open_r:
            items.append({"class": "revision_open", "programs": [pid],
                          "text": f"截至 {meta['week_end']} 週報待決修訂 R{r['id']}：{r['text']}"})
    alerts = alert_classes()
    for it in items:
        it["alert"] = it["class"] in alerts
    return items


SECTION_TITLES = {
    "pla_anomaly": "共軍活動異常",
    "ladder_formal": "達到正式決定以上的事實",
    "cross_program": "跨計畫事件",
    "revision_open": "待決修訂建議",
}


def render(day: date, items: list[dict], now: datetime) -> str:
    alerts = [i for i in items if i["alert"]]
    L = [f"# 每日戰略摘要 {day}", "",
         f"> 生成於 {now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC；只使用系統已存資料，未發送。", "",
         "## 警示", ""]
    L += [f"- ⚠ [{','.join(i['programs'])}] {i['text']}" for i in alerts] or ["- 無"]
    for cls, title in SECTION_TITLES.items():
        rows = [i for i in items if i["class"] == cls]
        L += ["", f"## {title}（{len(rows)}）", ""]
        for i in rows:
            src = f" — {i['source']}" if i.get("source") else ""
            L.append(f"- [{','.join(i['programs'])}] {i['text']}{src}")
        if not rows:
            L.append("- 無")
    return "\n".join(L).rstrip() + "\n"


def write(day: date, markdown: str, items: list[dict]) -> Path:
    root = Path(os.getenv("ASSESSMENT_ROOT", "").strip() or Path(__file__).resolve().parents[2] / "data" / "assessments")
    folder = root / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.md").write_text(markdown, encoding="utf-8")
    (folder / f"{day}.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder / f"{day}.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.programs.daily")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..database import Database
    from ..settings import get_settings
    from ..time_utils import TAIPEI

    day = args.date or (datetime.now(TAIPEI).date() - timedelta(days=1))
    with Database(args.db or get_settings().news_db_path) as db:
        items = collect(db.conn, day)
    path = write(day, render(day, items, datetime.now(timezone.utc)), items)
    print(f"Daily digest written: {path}  (items={len(items)}, alerts={sum(i['alert'] for i in items)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
