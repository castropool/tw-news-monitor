"""AI call ledger and monthly spending ceiling.

Every model call goes through :class:`app.deepseek_analysis.DeepSeekClient`,
which asks :func:`check_budget` before sending and :func:`record_call` after.
The ledger is a small SQLite file of its own (``data/ai_usage.db``, override
with ``AI_USAGE_DB_PATH``) so any caller can log without a news DB handle.

Cost is an upper-bound estimate in US dollars:

- prices come from ``app/llm_router/pricing.py`` at the **peak** rate;
- a model with no known price is charged at the dearest known rate;
- when the endpoint reports no token usage (the local Command Code gateway
  always reports 0), tokens are estimated from text length: one per Han
  character plus one per three other characters.

Overestimating keeps real spend under the ceiling.  When this month's
estimated spend (calendar month, UTC) reaches ``AI_MONTHLY_BUDGET_USD``
(default 15), calls are refused and callers fall back exactly as they do
for any other model error: summaries fall back to teaser/meta text, and
news delivery continues.  A ledger failure never blocks a call.

Report: ``python -m app.ai_usage`` (this month) or ``--month 2026-10``.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .llm_router.pricing import MODEL_PRICING, normalize_model_id

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_USD = 15.0
_HAN_RE = re.compile("[㐀-䶿一-鿿豈-﫿]")
_DEFAREST = max(MODEL_PRICING.values(), key=lambda p: p["output_peak_per_m"])
_warned_models: set[str] = set()


class BudgetExceeded(RuntimeError):
    pass


def budget_usd() -> float:
    try:
        return max(0.0, float(os.getenv("AI_MONTHLY_BUDGET_USD", str(DEFAULT_BUDGET_USD))))
    except ValueError:
        return DEFAULT_BUDGET_USD


def ledger_path() -> Path:
    value = os.getenv("AI_USAGE_DB_PATH", "").strip()
    if value:
        return Path(value)
    return Path(__file__).resolve().parent.parent / "data" / "ai_usage.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            called_at TEXT NOT NULL,
            month TEXT NOT NULL,
            task TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            tokens_estimated INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            duration_ms INTEGER,
            error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_calls_month ON ai_calls(month)")
    return conn


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    han = len(_HAN_RE.findall(text))
    return han + math.ceil((len(text) - han) / 3)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(normalize_model_id(model or ""))
    if pricing is None:
        if model not in _warned_models:
            _warned_models.add(model)
            logger.warning("No price for model %r; charging the dearest known rate", model)
        pricing = _DEFAREST
    return (
        input_tokens / 1_000_000 * pricing["input_peak_per_m"]
        + output_tokens / 1_000_000 * pricing["output_peak_per_m"]
    )


def _month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m")


def month_spend(month: str | None = None, *, path: Path | None = None) -> float:
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM ai_calls WHERE month = ?", (month or _month(),)
        ).fetchone()
        return float(row[0])
    finally:
        conn.close()


def check_budget(*, now: datetime | None = None, path: Path | None = None) -> None:
    """Raise BudgetExceeded once this month's estimated spend reaches the ceiling.

    A ledger that cannot be read does not block calls (logged instead).
    """
    try:
        spent = month_spend(_month(now), path=path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI usage ledger unreadable (%s); budget not enforced for this call", exc)
        return
    ceiling = budget_usd()
    if spent >= ceiling:
        raise BudgetExceeded(
            f"monthly AI budget reached: estimated ${spent:.2f} of ${ceiling:.2f}"
        )


def record_call(
    *,
    task: str,
    provider: str,
    model: str,
    status: str,
    prompt_text: str = "",
    output_text: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int | None = None,
    error: str | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> float:
    """Log one call; returns its estimated cost. Never raises."""
    estimated = False
    if status == "success" and not (input_tokens or output_tokens):
        input_tokens, output_tokens = estimate_tokens(prompt_text), estimate_tokens(output_text)
        estimated = True
    # Failed or blocked calls are charged only for tokens the endpoint reported:
    # charging estimated prompts for an unreachable gateway would exhaust the
    # budget without any model work being done.
    cost = estimate_cost(model, input_tokens, output_tokens) if (input_tokens or output_tokens) else 0.0
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        conn = _connect(path)
        try:
            conn.execute(
                "INSERT INTO ai_calls(called_at, month, task, provider, model, status, input_tokens, "
                "output_tokens, tokens_estimated, cost_usd, duration_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    stamp.isoformat(), _month(stamp), task, provider, model, status,
                    input_tokens, output_tokens, int(estimated), cost, duration_ms,
                    (error or "")[:300] or None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI usage ledger write failed (%s)", exc)
    return cost


def month_report(month: str | None = None, *, path: Path | None = None) -> list[tuple]:
    conn = _connect(path)
    try:
        return conn.execute(
            "SELECT task, model, status, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) "
            "FROM ai_calls WHERE month = ? GROUP BY task, model, status ORDER BY SUM(cost_usd) DESC",
            (month or _month(),),
        ).fetchall()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ai_usage", description="AI spend this month")
    parser.add_argument("--month", help="YYYY-MM (default: current month, UTC)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    month = args.month or _month()
    rows = month_report(month)
    spent = sum(r[6] or 0 for r in rows)
    ceiling = budget_usd()
    print(f"AI usage {month}: estimated ${spent:.4f} of ${ceiling:.2f} ceiling "
          f"({'BLOCKED' if spent >= ceiling else f'${ceiling - spent:.4f} left'})")
    for task, model, status, calls, tin, tout, cost in rows:
        print(f"  {task:<14} {model:<30} {status:<8} calls={calls:<5} in={tin or 0:<9} out={tout or 0:<8} ${cost or 0:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
