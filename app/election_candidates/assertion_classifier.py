"""Clause-level deterministic assertion classification.

Articles are split into clauses (quotes preserved); each clause is classified
independently so that an observed action and a statement in the same sentence
become separate assertions.  Statements without a resolvable speaker are not
emitted as actor_statement.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .candidate_models import NormalizedArticle
from .sentence_splitter import split_clauses
from .speaker_resolver import resolve_speaker


ALLOWED_KINDS = {
    "observed_fact",
    "actor_statement",
    "allegation",
    "media_interpretation",
    "planned_action",
    "uncertain_report",
    "unknown",
}

CONNECTORS = ("並表示", "並稱", "同时指出", "同時指出", "但強調", "但强调", "隨後宣布", "随后宣布", "接著", "接着")


def _contains_any(text: str, markers: list[str]) -> bool:
    return any(m and m in text for m in markers)


def _classify_clause(
    clause: str,
    cfg: dict[str, Any],
    article: NormalizedArticle,
    config,
    last_speaker: str,
) -> tuple[str, str, str, list[str], list[str], str]:
    statement_verbs = cfg.get("statement_verbs", []) or []
    allegation_verbs = cfg.get("allegation_verbs", []) or []
    uncertain_markers = cfg.get("uncertain_markers", []) or []
    media_markers = cfg.get("media_interpretation_markers", []) or []
    planned_markers = cfg.get("planned_action_markers", []) or []
    future_markers = cfg.get("future_tense_markers", []) or []
    observed_markers = cfg.get("observed_fact_markers", []) or []

    reasons: list[str] = []
    risk: list[str] = []
    predicate = ""
    speaker = ""

    if _contains_any(clause, uncertain_markers):
        reasons.append("uncertain_marker")
        risk.append("unverified")
        return "uncertain_report", "傳出/據悉", speaker, risk, reasons, clause

    if _contains_any(clause, allegation_verbs):
        is_denial = "指控不實" in clause or "駁斥" in clause or "否認" in clause or "澄清" in clause
        colon = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}?)[：:]", clause)
        if is_denial and colon:
            reasons.append("denial_with_colon_speaker")
            return "actor_statement", "駁斥/否認", re.sub(
                r"(直呼|表示|稱|說|強調|主張|認為|要求|呼籲|批評|指控|質疑)$", "", colon.group(1)
            ), risk, reasons, clause
        if is_denial:
            reasons.append("denial_statement")
            for verb in ("駁斥", "否認", "澄清"):
                idx = clause.find(verb)
                if idx > 0:
                    prefix = clause[:idx]
                    m = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}?)$", prefix)
                    if m:
                        return "actor_statement", verb, m.group(1), risk, reasons, clause
            return "actor_statement", "否認", "", risk, reasons, clause
        verb = next(v for v in allegation_verbs if v in clause)
        reasons.append(f"allegation_verb:{verb}")
        risk.append("unverified")
        return "allegation", verb, speaker, risk, reasons, clause

    if "：" in clause or ":" in clause:
        colon = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}?)[：:]", clause)
        if colon:
            sp = re.sub(
                r"(直呼|表示|稱|說|強調|主張|認為|要求|呼籲|批評|指控|質疑)$", "", colon.group(1)
            )
            reasons.append("colon_quotation")
            return "actor_statement", "表示", sp, risk, reasons, clause

    for verb in statement_verbs:
        if verb in clause:
            idx = clause.find(verb)
            sp, basis = resolve_speaker(clause, article, config)
            if not sp and last_speaker and clause.startswith(CONNECTORS):
                sp = last_speaker
                reasons.append(f"connector_speaker_carry:{verb}")
            elif sp:
                reasons.append(f"statement_verb:{verb}({basis})")
            else:
                continue
            obj = clause[idx + len(verb):].split("，")[0][:80]
            return "actor_statement", verb, sp, risk, reasons, f"{obj or clause[:80]}"

    if _contains_any(clause, media_markers):
        reasons.append("media_marker")
        risk.append("interpretation")
        return "media_interpretation", "媒體分析", speaker, risk, reasons, clause

    if _contains_any(clause, planned_markers) or _contains_any(clause, future_markers):
        reasons.append("planned_or_future_marker")
        risk.append("future")
        return "planned_action", "計劃/將", speaker, risk, reasons, clause

    for marker in observed_markers:
        if marker in clause:
            reasons.append(f"observed_marker:{marker}")
            return "observed_fact", marker, speaker, risk, reasons, clause

    return "", "", speaker, risk, reasons, clause


def classify_article_assertions(
    article: NormalizedArticle,
    candidate_id: str,
    run_id: str,
    config,
) -> list[dict[str, Any]]:
    cfg = config.get("assertion_classifier", {}) or {}
    known_speakers = set(article.match.matched_people) | set(article.match.matched_parties) | set(
        cfg.get("known_speaker_terms", []) or []
    )
    title = article.raw_title or article.normalized_title
    summary = article.summary or ""
    sentences = [title] + ([summary] if summary else [])
    clauses = [c for s in sentences for c in split_clauses(s)]

    subject = ",".join(article.match.matched_people) or ",".join(article.match.matched_parties) or ""
    outputs: list[dict[str, Any]] = []
    last_speaker = ""

    for clause in clauses:
        kind, predicate, speaker, risk, reasons, source_clause = _classify_clause(
            clause, cfg, article, config, last_speaker
        )
        if not kind:
            continue
        if kind == "actor_statement" and speaker:
            last_speaker = speaker
        elif speaker:
            last_speaker = speaker
        elif kind in ("observed_fact", "unknown", "planned_action", "media_interpretation"):
            for name in known_speakers:
                if name and name in clause:
                    last_speaker = name
                    break
        if kind == "actor_statement" and not speaker:
            continue
        if kind == "actor_statement" and speaker and speaker not in known_speakers and not clause.startswith(CONNECTORS):
            # Only accept non-known speakers when they come from a colon quote.
            if "：" not in clause and ":" not in clause:
                continue

        confidence = {
            "observed_fact": 0.8,
            "actor_statement": 0.7,
            "allegation": 0.5,
            "media_interpretation": 0.4,
            "planned_action": 0.6,
            "uncertain_report": 0.3,
            "unknown": 0.2,
        }[kind]
        if _contains_any(clause, ["可能", "疑似", "恐怕"]):
            risk.append("hedged")
        assertion_text = f"{subject}{predicate}{source_clause[:80]}" if (subject or predicate) else source_clause[:150]
        assertion_id = "asrt_" + hashlib.sha256(
            f"{candidate_id}|{article.news_article_id}|{kind}|{assertion_text}".encode("utf-8")
        ).hexdigest()[:12]
        outputs.append(
            {
                "assertion_id": assertion_id,
                "candidate_id": candidate_id,
                "assertion_kind": kind,
                "assertion_text": assertion_text[:200],
                "subject": subject,
                "predicate": predicate,
                "object_text": source_clause[:80],
                "speaker": speaker,
                "evidence_article_id": article.news_article_id,
                "evidence_field": "title" if clause in title else "summary",
                "evidence_text": clause[:200],
                "source_clause": source_clause,
                "classification_reasons_json": json.dumps(reasons, ensure_ascii=False),
                "confidence": confidence,
                "risk_flags_json": json.dumps(risk, ensure_ascii=False),
                "created_run_id": run_id,
            }
        )

    if not outputs:
        outputs.append(
            {
                "assertion_id": "asrt_" + hashlib.sha256(
                    f"{candidate_id}|{article.news_article_id}|unknown".encode("utf-8")
                ).hexdigest()[:12],
                "candidate_id": candidate_id,
                "assertion_kind": "unknown",
                "assertion_text": title[:150],
                "subject": subject,
                "predicate": "",
                "object_text": title[:80],
                "speaker": "",
                "evidence_article_id": article.news_article_id,
                "evidence_field": "title",
                "evidence_text": title[:200],
                "source_clause": title[:200],
                "classification_reasons_json": "[]",
                "confidence": 0.2,
                "risk_flags_json": '["unknown_kind"]',
                "created_run_id": run_id,
            }
        )

    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for out in outputs:
        key = (out["assertion_kind"], out["assertion_text"])
        if key in seen:
            continue
        seen.add(key)
        result.append(out)
    return result


def build_assertion_profile(assertions: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {k: 0 for k in sorted(ALLOWED_KINDS)}
    for a in assertions:
        counts[a["assertion_kind"]] = counts.get(a["assertion_kind"], 0) + 1
    risk_flags: list[str] = []
    for a in assertions:
        for flag in json.loads(a.get("risk_flags_json", "[]")):
            if flag not in risk_flags:
                risk_flags.append(flag)
    return {
        "counts": counts,
        "has_observed_fact": counts["observed_fact"] > 0,
        "has_actor_statement": counts["actor_statement"] > 0,
        "has_allegation": counts["allegation"] > 0,
        "has_media_interpretation": counts["media_interpretation"] > 0,
        "has_planned_action": counts["planned_action"] > 0,
        "has_uncertain_report": counts["uncertain_report"] > 0,
        "has_unknown": counts["unknown"] > 0,
        "risk_flags": risk_flags,
    }
