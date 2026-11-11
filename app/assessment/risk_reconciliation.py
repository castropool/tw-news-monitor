"""风险语义分类（newly emerged / carried forward / reframed ...）。"""

from __future__ import annotations

import re

from .gap_reconciliation import normalize_text, THEME_KEYWORDS


def _slug(text: str) -> str:
    return normalize_text(text)[:60]


def _overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    chars_a = set(a)
    chars_b = set(b)
    if not chars_a or not chars_b:
        return 0.0
    return len(chars_a & chars_b) / min(len(chars_a), len(chars_b))


def _match_previous_risk(risk: dict, previous_risks: list[dict]) -> dict | None:
    text = normalize_text(risk.get("risk") or "")
    rtype = risk.get("risk_type")
    best = None
    best_score = 0.0
    for prev in previous_risks:
        prev_text = normalize_text(prev.get("risk") or "")
        score = _overlap(text, prev_text)
        if rtype and prev.get("risk_type") == rtype:
            score += 0.15
        if score > best_score:
            best_score = score
            best = prev
    if best and best_score >= 0.35:
        return best
    return None


def _match_previous_limitation(risk_text: str, previous_limitations: list[str]) -> bool:
    text = normalize_text(risk_text)
    for lim in previous_limitations:
        if _overlap(text, normalize_text(lim)) >= 0.25:
            return True
    return False


def _gap_ids_for_risk(risk_text: str) -> list[str]:
    out = []
    for gap_id, keywords in THEME_KEYWORDS:
        if any(kw in risk_text for kw in keywords):
            out.append(gap_id)
    return out


def classify_risks(
    *,
    current_risks: list[dict],
    previous_risks: list[dict],
    previous_limitations: list[str],
    previous_supporting_event_ids: set[str],
) -> dict:
    changes = []
    newly_emerged = []
    carried_forward = []
    for risk in current_risks:
        text = risk.get("risk") or ""
        risk_id = risk.get("risk_id") or f"risk_{_slug(text)}"
        matched = _match_previous_risk(risk, previous_risks)
        previously_present = matched is not None
        supporting_events = [e for e in (risk.get("supporting_event_ids") or []) if e]
        new_evidence = any(e not in previous_supporting_event_ids for e in supporting_events)

        if matched is not None:
            same_text = normalize_text(matched.get("risk") or "") == normalize_text(text)
            if same_text:
                change_type = "existing_risk_reaffirmed"
            elif matched.get("risk_type") == risk.get("risk_type"):
                change_type = "risk_reframed"
            else:
                change_type = "risk_reframed"
        elif _match_previous_limitation(text, previous_limitations):
            change_type = "existing_limitation_carried_forward"
        elif new_evidence:
            change_type = "newly_emerged_risk"
            newly_emerged.append(risk_id)
        else:
            change_type = "risk_reframed"

        if change_type == "existing_limitation_carried_forward":
            carried_forward.append(risk_id)

        changes.append(
            {
                "risk_id": risk_id,
                "risk_text": text,
                "previously_present": previously_present,
                "current_status": "active",
                "change_type": change_type,
                "material_for_report": change_type == "newly_emerged_risk" and bool(new_evidence),
                "supporting_event_ids": supporting_events,
                "supporting_gap_ids": _gap_ids_for_risk(text),
            }
        )

    return {
        "risk_change_count": len(changes),
        "newly_emerged_risk_count": len(newly_emerged),
        "risk_changes": changes,
        "newly_emerged_risks": newly_emerged,
        "carried_forward_risks": carried_forward,
        "reconciliation_ready": True,
    }
