"""Event construction, representative reporting, and editorial event salience."""
import hashlib
import re

from ..source_registry import is_official_source
from .classifier import any_term, classify, folded, matches
from .clustering import article_time, cluster


def source_info(article, rules):
    name = folded(article.source_name)
    for canonical, info in rules["sources"].items():
        if name in {folded(alias) for alias in info["aliases"]}:
            return canonical, info["quality"], info["mainstream"]
    return article.source_name, 20 if is_official_source(article.source_id) else 5, False


def representative(members, rules):
    def key(f):
        _, quality, _ = source_info(f.article, rules)
        information = min(18, len(f.title) / 3) + min(12, len(f.weapons | f.equipment) * 4)
        directness = 10 if f.primary_action != "unknown" else 0
        completeness = min(10, len(f.article.summary or "") / 30)
        return (quality + information + directness + completeness,
                article_time(f.article), f.article.url)
    return max(members, key=key)


def _signal_present(f, signal, rules):
    # Modifiers must share a title clause with an actual military action/entity.
    for clause in re.split(r"[；;。！？!?]", f.text):
        if not any_term(clause, signal["terms"]):
            continue
        if not (matches(clause, rules["actions"]) & set(signal["actions"])):
            continue
        if not any(matches(clause, rules["entities"][g]) for g in ("weapons", "equipment", "organizations", "named_events")):
            continue
        # Conservative negation scope: do not award a negated modifier.
        if any_term(clause, rules["negative"]["status_negative"]):
            continue
        return True
    return False


def importance(members, rules):
    if members[0].category == "us_taiwan_military" or any(f.us_related for f in members):
        return None, "factual", ["美国相关报道：仅列事实与报道来源，不评分"]
    cfg = rules["importance"]
    category = members[0].category
    score = cfg["category_base"][category]
    reasons = [f"{rules['categories'][category]}事件基准（{score}）"]
    entities = set().union(*(f.weapons | f.equipment for f in members))
    if entities & set(cfg["key_equipment"]):
        score += cfg["equipment_bonus"]
        reasons.append(f"涉及关键战力装备（+{cfg['equipment_bonus']}）")
    for signal in cfg["signals"].values():
        if any(_signal_present(f, signal, rules) for f in members):
            score += signal["weight"]
            reasons.append(f"{signal['reason']}（+{signal['weight']}）")
    sources = {source_info(f.article, rules)[0] for f in members if source_info(f.article, rules)[2]}
    bonus = min(cfg["max_source_bonus"], max(0, len(sources) - 1) * cfg["source_bonus"])
    if bonus:
        score += bonus
        reasons.append(f"{len(sources)}家不同主流媒体报道（+{bonus}，不代表独立核实）")
    score = round(min(100, max(0, score)))
    level = "major" if score >= 80 else "important" if score >= 60 else "normal" if score >= 40 else "low"
    return score, level, reasons


def build_events(articles, rules, article_ids=None):
    article_ids = article_ids or {}
    # A repeated URL is one report, with deterministic metadata selection.
    unique = {}
    for a in sorted(articles, key=lambda a: (a.url, article_time(a), a.title, a.summary or "", a.source_name)):
        unique[a.url] = a
    features = [classify(a, rules) for a in unique.values()]
    events = []
    for members, explanations in cluster([f for f in features if f.is_military], rules):
        rep = representative(members, rules)
        score, level, reasons = importance(members, rules)
        urls = sorted(f.article.url for f in members)
        articles_out = [{"id": article_ids.get(f.article.url, f.article.url),
                         "url": f.article.url, "title": f.title,
                         "source": source_info(f.article, rules)[0],
                         "published_at": article_time(f.article).isoformat(),
                         "time_basis": "published_at" if f.article.published_at else "fetched_at"}
                        for f in sorted(members, key=lambda f: f.article.url)]
        events.append({
            "event_id": "mil_" + hashlib.sha256("\n".join(urls).encode()).hexdigest()[:16],
            "title": rep.title, "category": rep.category,
            "category_name": rules["categories"][rep.category], "importance": score,
            "level": level, "importance_reasons": reasons,
            "representative": next(a for a in articles_out if a["url"] == rep.article.url),
            "article_ids": [a["id"] for a in articles_out], "articles": articles_out,
            "sources": sorted({a["source"] for a in articles_out}),
            "first_seen": min(article_time(f.article) for f in members).isoformat(),
            "last_updated": max(article_time(f.article) for f in members).isoformat(),
            "core_entities": sorted(set().union(*(f.entities for f in members))),
            "article_count": len(members), "cluster_explanations": explanations,
        })
    # Unscored policy facts form a separate chronology, never a policy ranking.
    scored = [e for e in events if e["importance"] is not None]
    facts = [e for e in events if e["importance"] is None]
    scored.sort(key=lambda e: (-e["importance"], e["first_seen"], e["event_id"]))
    facts.sort(key=lambda e: (e["first_seen"], e["event_id"]))
    return scored + facts
