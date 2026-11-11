"""Resolve assertion speakers from clauses and article match evidence."""

from __future__ import annotations

import re

from .candidate_models import NormalizedArticle


VERB_SUFFIX = re.compile(r"(直呼|表示|稱|說|強調|主張|認為|要求|呼籲|批評|指控|質疑)$")


def _extract_prefix_speaker(clause: str, verbs: list[str]) -> str:
    for verb in sorted(verbs, key=len, reverse=True):
        idx = clause.find(verb)
        if idx <= 0:
            continue
        prefix = clause[:idx]
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}?)$", prefix)
        if m:
            return m.group(1)
    return ""


def resolve_speaker(clause: str, article: NormalizedArticle, config) -> tuple[str, str]:
    cfg = config.get("assertion_classifier", {}) or {}
    known = set(article.match.matched_people) | set(article.match.matched_parties) | set(
        cfg.get("known_speaker_terms", []) or []
    )
    colon = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}?)[：:]", clause)
    if colon:
        speaker = VERB_SUFFIX.sub("", colon.group(1))
        return speaker, "colon"
    verbs = cfg.get("statement_verbs", []) or []
    for verb in verbs:
        if verb in clause:
            speaker = _extract_prefix_speaker(clause, verbs)
            if speaker in known:
                return speaker, "known_actor"
            return "", "unknown"
    return "", "unknown"
