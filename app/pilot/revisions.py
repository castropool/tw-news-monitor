"""Revision suggestions: what in the last approved assessment may need revising.

Each new weekly draft is compared with the most recent *approved* weekly
assessment before it.  Suggestions are raised on fixed, explainable rules:

1. ``hypothesis_shift``: a hypothesis's net indicator balance (支持 minus
   不支持) changed sign, or moved by 2 or more.
2. ``evidence_withdrawn``: a key judgment in the approved assessment cited a
   fact that has since been rejected by the analyst or found invalid.
3. ``activity_regime``: weekly aircraft activity moved across the
   above-normal (>= 1.3x baseline) / normal / below-normal (<= 0.8x) bands.

Every suggestion states its reason and starts ``open``; the analyst accepts
or dismisses it with a note.  The draft cannot be approved while any
suggestion is open.  No model is involved.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _net(tally: dict) -> int:
    return int(tally.get("支持", 0)) - int(tally.get("不支持", 0))


def regime(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    return "偏高" if ratio >= 1.3 else "偏低" if ratio <= 0.8 else "正常"


def previous_approved(root: Path, week_start: str) -> dict | None:
    """Meta of the latest approved assessment that ended before ``week_start``."""
    best = None
    for path in root.glob("*/meta.json") if root.exists() else []:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("status") == "approved" and meta.get("week_end", "") < week_start:
            if best is None or meta["week_end"] > best["week_end"]:
                best = meta
    return best


def claim_states(conn: sqlite3.Connection, ids: list[int]) -> dict[int, tuple[int, str]]:
    """claim id -> (valid, review_status); missing ids are absent."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    try:
        rows = conn.execute(f"SELECT id, valid, review_status FROM claims WHERE id IN ({marks})", ids)
    except sqlite3.OperationalError:
        return {}
    return {r[0]: (r[1], r[2]) for r in rows}


def suggest(conn: sqlite3.Connection, previous: dict | None, current: dict) -> list[dict]:
    """``current`` holds ``tally`` (hypothesis -> counts) and ``aircraft_ratio``."""
    if not previous:
        return []
    out: list[dict] = []
    since = f"截至 {previous['week_end']} 的核准週報"

    prev_tally = previous.get("tally") or {}
    for hyp, now_counts in sorted((current.get("tally") or {}).items()):
        if hyp not in prev_tally:
            continue
        before, after = _net(prev_tally[hyp]), _net(now_counts)
        flipped = (before > 0 > after) or (before < 0 < after) or (before != 0 and after == 0 and abs(before) >= 2)
        if flipped or abs(after - before) >= 2:
            out.append({
                "kind": "hypothesis_shift",
                "text": f"{hyp} 的指標淨支持由 {before:+d} 變為 {after:+d}（{since}）。建議重新檢視上次關於 {hyp} 的判斷是否仍成立。",
            })

    judgments = previous.get("judgments") or []
    claim_ids = previous.get("fact_claim_ids") or []
    states = claim_states(conn, [i for i in claim_ids if i is not None])
    for j_index, j in enumerate(judgments, 1):
        withdrawn = []
        for n in j.get("citations") or []:
            if 1 <= n <= len(claim_ids):
                state = states.get(claim_ids[n - 1])
                if state is None or not state[0] or state[1] == "rejected":
                    withdrawn.append(n)
        if withdrawn:
            out.append({
                "kind": "evidence_withdrawn",
                "text": f"{since}第 {j_index} 條要點所引事實 {withdrawn} 已被駁回或失效：「{j.get('text', '')[:60]}」。建議修訂或撤回該要點。",
            })

    before, after = regime(previous.get("aircraft_ratio")), regime(current.get("aircraft_ratio"))
    if before and after and before != after:
        out.append({
            "kind": "activity_regime",
            "text": (f"共機活動由「{before}」（{previous['aircraft_ratio']:.2f}× 基線）轉為「{after}」"
                     f"（{current['aircraft_ratio']:.2f}×）。建議檢查上次對活動水準的描述是否需要更新。"),
        })
    for i, s in enumerate(out, 1):
        s.update(id=i, status="open", decided_by=None, note=None)
    return out


def render_markdown(suggestions: list[dict], previous: dict | None) -> str:
    if not previous:
        return "（尚無已核准的週報可供比較。）"
    if not suggestions:
        return f"（與截至 {previous['week_end']} 的核准週報相比，沒有需要修訂的項目。）"
    return "\n".join(f"- [R{s['id']}] {s['text']}" for s in suggestions)
