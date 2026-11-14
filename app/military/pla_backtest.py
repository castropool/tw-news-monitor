"""Backtest of the PLA spike flags against labelled incidents (pilot gate).

Labels come from ``config/p4_incident_labels.yaml``: dates of named
exercises or incidents an analyst confirms.  For a period (ideally one held
out from tuning) the backtest reports:

- recall: share of labelled incidents with a spike flag within ±``window``
  days (default 1);
- precision: share of spike days within ±``window`` of a labelled incident;
- the misses and the unexplained spikes, for the analyst to review.

Flags use the same rules as the weekly draft (``pla_baseline.read_day``),
each day judged only on the 90 days before it, so the result is what the
system would have said at the time.

CLI::

    python -m app.military.pla_backtest --since 2025-01-01 --until 2025-12-31
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import pla_baseline as pb

LABELS_PATH = Path(__file__).resolve().parents[2] / "config" / "p4_incident_labels.yaml"


def load_labels(path: Path = LABELS_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for row in data.get("incidents") or []:
        start = date.fromisoformat(str(row["start"]))
        end = date.fromisoformat(str(row.get("end") or row["start"]))
        out.append({"name": row["name"], "start": start, "end": end, "confirmed": bool(row.get("confirmed"))})
    return out


def spike_days(series, since: date, until: date, metric: str = "aircraft") -> list[date]:
    days, d = [], since
    while d <= until:
        if pb.read_day(series, metric, d).flag == "spike":
            days.append(d)
        d += timedelta(days=1)
    return days


def backtest(series, labels: list[dict], since: date, until: date, *, window: int = 1,
             metric: str = "aircraft", confirmed_only: bool = False) -> dict:
    labels = [l for l in labels if l["end"] >= since and l["start"] <= until and (l["confirmed"] or not confirmed_only)]
    spikes = spike_days(series, since, until, metric)

    def near(day: date, label: dict) -> bool:
        return label["start"] - timedelta(days=window) <= day <= label["end"] + timedelta(days=window)

    caught = [l for l in labels if any(near(s, l) for s in spikes)]
    explained = [s for s in spikes if any(near(s, l) for l in labels)]
    return {
        "period": [since.isoformat(), until.isoformat()],
        "labels": len(labels),
        "spike_days": len(spikes),
        "recall": round(len(caught) / len(labels), 3) if labels else None,
        "precision": round(len(explained) / len(spikes), 3) if spikes else None,
        "missed": [f"{l['start']} {l['name']}" for l in labels if l not in caught],
        "unexplained_spikes": [s.isoformat() for s in spikes if s not in explained],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.military.pla_backtest")
    parser.add_argument("--since", type=date.fromisoformat, required=True)
    parser.add_argument("--until", type=date.fromisoformat, required=True)
    parser.add_argument("--window", type=int, default=1)
    parser.add_argument("--metric", default="aircraft", choices=pb.METRICS)
    parser.add_argument("--confirmed-only", action="store_true")
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..database import Database
    from ..settings import get_settings

    with Database(args.db or get_settings().news_db_path) as db:
        series = pb.load_series(db.conn)
    report = backtest(series, load_labels(), args.since, args.until, window=args.window, metric=args.metric,
                      confirmed_only=args.confirmed_only)
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
