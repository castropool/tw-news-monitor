import csv
import logging
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from .time_utils import TAIPEI
from pathlib import Path

from .collectors import (
    BloombergNewsletterCollector,
    CNAHtmlCollector,
    EBCCollector,
    FTAlphavilleCollector,
    LtnMilitaryCollector,
    LtnRSSCollector,
    MNAMilitaryCollector,
    NownewsMilitaryCollector,
    PresidentCollector,
    RSSCollector,
    ReutersCollector,
    UDNCollector,
    WSJNewsletterCollector,
    WSJRSSCollector,
    ZaobaoCollector,
)

logger = logging.getLogger(__name__)

COLLECTOR_MAP = {
    "rss": RSSCollector,
    "udn": UDNCollector,
    "ebc": EBCCollector,
    "cna_list_html": CNAHtmlCollector,
    "zaobao": ZaobaoCollector,
    "ltn_rss": LtnRSSCollector,
    "newtalk_rss": RSSCollector,
    "president_json": PresidentCollector,
    "reuters": ReutersCollector,
    "ft_alphaville": FTAlphavilleCollector,
    "wsj_rss": WSJRSSCollector,
    "wsj_newsletter": WSJNewsletterCollector,
    "bloomberg_newsletter": BloombergNewsletterCollector,
    "ltn_military": LtnMilitaryCollector,
    "nownews_military": NownewsMilitaryCollector,
    "mna_military": MNAMilitaryCollector,
}

CSV_FIELDS = [
    "run_started_at", "collector_name", "source_id", "source_name",
    "category", "position", "original_title", "normalized_title",
    "original_url", "normalized_url", "published_at_raw",
    "published_at_parsed", "published_timezone", "published_at_is_aware", "time_parse_method", "assumed_timezone", "fetched_at",
    "url_duplicate_count_in_run", "title_duplicate_count_in_run",
    "duplicate_group_id", "duplicate_type", "already_in_database",
    "is_published_time_valid", "article_age_minutes",
    "suspected_stale", "diagnostic_reason",
]


def normalize_title(title: str) -> str:
    """Lightweight title normalization for diagnosis."""
    t = unicodedata.normalize("NFKC", title)
    t = t.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def run_diagnosis(sources, db, output_dir, run_started_at=None):
    """Collect news and produce diagnosis CSV and MD files. READ-ONLY."""
    if run_started_at is None:
        run_started_at = datetime.now(TAIPEI)

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = run_started_at.strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"collection_{ts}.csv"
    md_path = output_dir / f"diagnosis_{ts}.md"
    latest_csv = output_dir / "latest_collection.csv"
    latest_md = output_dir / "latest_diagnosis.md"

    records = []
    seen_urls = {}
    seen_titles = {}
    seen_across_cats = {}

    for source in sources:
        source_type = source.get("type") or source.get("collector")
        cls = COLLECTOR_MAP.get(source_type)
        if source.get("enabled") is False:
            logger.info("Diagnosis: skipping disabled source %s", source.get("id"))
            continue
        if not cls:
            logger.warning(
                "Diagnosis: unsupported collector type %r for source %s; skipping",
                source_type,
                source.get("id"),
            )
            continue
        collector = None
        try:
            collector = cls(source)
            articles = collector.collect()
        except Exception as e:
            logger.error("Failed %s: %s", source["id"], e)
            continue
        finally:
            if collector is not None:
                try:
                    collector.close()
                except Exception:
                    logger.warning("Diagnosis: collector close failed for %s", source.get("id"))

        for article in articles:
            nurl = article.url
            ntitle = normalize_title(article.title)
            cat = article.category

            if nurl not in seen_urls:
                seen_urls[nurl] = []
            seen_urls[nurl].append(len(records))

            tk = (article.source_name, ntitle)
            if tk not in seen_titles:
                seen_titles[tk] = []
            seen_titles[tk].append(len(records))

            if nurl not in seen_across_cats:
                seen_across_cats[nurl] = set()
            seen_across_cats[nurl].add(cat)

            pub_parsed = ""
            pub_tz = ""
            if article.published_at:
                pub_parsed = article.published_at.isoformat()
                pub_tz = "with_tz" if article.published_at.tzinfo else "naive"

            age_mins = ""
            is_stale = False
            if article.published_at:
                age = run_started_at - article.published_at
                age_mins = int(age.total_seconds() / 60)
                is_stale = age_mins > 90

            already_db = db.article_exists(nurl) if db else False

            records.append({
                "run_started_at": run_started_at.strftime("%Y-%m-%d %H:%M:%S"),
                "collector_name": source_type,
                "source_id": source["id"],
                "source_name": source["name"],
                "category": cat,
                "position": article.position,
                "original_title": article.title,
                "normalized_title": ntitle,
                "original_url": "",
                "normalized_url": nurl,
                "published_at_raw": str(article.published_at) if article.published_at else "",
                "published_at_parsed": pub_parsed,
                "published_timezone": pub_tz,
                "published_at_is_aware": "true" if (article.published_at and article.published_at.tzinfo is not None) else "false",
                "time_parse_method": (
                    "rss_explicit_offset" if source_type in {"rss", "newtalk_rss"}
                    else "html_full_datetime" if source_type in {"ebc", "udn", "cna_list_html"}
                    else "html_local_taipei"
                ) if article.published_at else "parse_failed",
                "assumed_timezone": str(article.published_at.tzinfo) if article.published_at and article.published_at.tzinfo else "",
                "fetched_at": article.fetched_at.isoformat(),
                "url_duplicate_count_in_run": 0,
                "title_duplicate_count_in_run": 0,
                "duplicate_group_id": "",
                "duplicate_type": "none",
                "already_in_database": "true" if already_db else "false",
                "is_published_time_valid": "true",
                "article_age_minutes": str(age_mins) if age_mins != "" else "",
                "suspected_stale": "true" if is_stale else "false",
                "diagnostic_reason": "",
            })

    # Second pass: classify duplicates
    for i, rec in enumerate(records):
        nurl = rec["normalized_url"]
        ntitle = rec["normalized_title"]
        sn = rec["source_name"]
        reasons = []

        u_idx = seen_urls.get(nurl, [])
        rec["url_duplicate_count_in_run"] = len([x for x in u_idx if x != i])

        t_idx = seen_titles.get((sn, ntitle), [])
        rec["title_duplicate_count_in_run"] = len([x for x in t_idx if x != i])

        if rec["already_in_database"] == "true":
            reasons.append("already_in_database")
        if rec["url_duplicate_count_in_run"] > 0:
            reasons.append("same_normalized_url")
            rec["duplicate_group_id"] = f"url_dup_{min(u_idx)}"
        if rec["title_duplicate_count_in_run"] > 0 and rec["url_duplicate_count_in_run"] == 0:
            reasons.append("same_source_same_normalized_title")
            if not rec["duplicate_group_id"]:
                rec["duplicate_group_id"] = f"title_dup_{min(t_idx)}"
        cats = seen_across_cats.get(nurl, set())
        if len(cats) > 1:
            reasons.append("same_article_across_categories")
        if rec["suspected_stale"] == "true":
            reasons.append("suspected_stale")

        rec["duplicate_type"] = "multiple_reasons" if len(reasons) > 1 else (
            reasons[0] if reasons else "none")
        rec["diagnostic_reason"] = "; ".join(reasons) if reasons else "normal"

    total = len(records)
    summary = {
        "total": total,
        "unique_urls": len(set(r["normalized_url"] for r in records)),
        "url_duplicates": sum(1 for r in records if r["url_duplicate_count_in_run"] > 0),
        "title_duplicates": sum(1 for r in records if r["title_duplicate_count_in_run"] > 0),
        "cross_category": sum(1 for r in records if "same_article_across_categories" in r.get("diagnostic_reason", "")),
        "already_in_db": sum(1 for r in records if r["already_in_database"] == "true"),
        "stale": sum(1 for r in records if r["suspected_stale"] == "true"),
        "no_publish_time": sum(1 for r in records if not r["published_at_parsed"]),
    }

    _write_csv(records, csv_path)
    shutil.copy2(csv_path, latest_csv)
    _write_md(records, summary, md_path, run_started_at)
    shutil.copy2(md_path, latest_md)
    json_path = output_dir / f"collection_{ts}.json"
    _save_json(records, json_path)
    shutil.copy2(json_path, output_dir / "latest_collection.json")
    logger.info("Diagnosis complete: CSV=%s, MD=%s", csv_path, md_path)


def _write_csv(records, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(records)


def _write_md(records, summary, path, run_started_at):
    L = [
        "# Diagnosis Report",
        "",
        f"Run at: {run_started_at}",
        "",
        "## Summary",
        "",
        f"- Total raw items: {summary['total']}",
        f"- Unique normalized URLs: {summary['unique_urls']}",
        f"- Same-run URL duplicates: {summary['url_duplicates']}",
        f"- Same-source same-title duplicates: {summary['title_duplicates']}",
        f"- Cross-category duplicates: {summary['cross_category']}",
        f"- Already in database: {summary['already_in_db']}",
        f"- Parse time failed: {summary['no_publish_time']}",
        f"- Stale (>90 min): {summary['stale']}",
        "",
        "## Collection by source",
        "",
    ]
    for sid, cnt in sorted(Counter(r["source_id"] for r in records).items()):
        L.append(f"- {sid}: {cnt}")
    L.append("")

    L.append("## Time analysis by source")
    L.append("")
    ct = defaultdict(lambda: {"t": 0, "null": 0, "stale": 0, "naive": 0, "tz": 0, "ages": []})
    for r in records:
        s = r["source_id"]; ct[s]["t"] += 1
        if not r["published_at_parsed"]: ct[s]["null"] += 1
        if r["published_timezone"] == "naive": ct[s]["naive"] += 1
        if r["published_timezone"] == "with_tz": ct[s]["tz"] += 1
        if r["suspected_stale"] == "true": ct[s]["stale"] += 1
        if r["article_age_minutes"]: ct[s]["ages"].append(int(r["article_age_minutes"]))
    for sid in sorted(ct.keys()):
        s = ct[sid]
        ma = max(s["ages"]) if s["ages"] else "-"
        mi = min(s["ages"]) if s["ages"] else "-"
        L.append(f"- {sid}: total={s['t']} empty_time={s['null']} tz={s['tz']} naive={s['naive']} stale={s['stale']} oldest={ma}min newest={mi}min")
    L.append("")

    dups = [r for r in records if r["duplicate_type"] != "none"]
    if dups:
        L.append(f"## Duplicates ({len(dups)} items)")
        L.append("")
        for r in dups[:30]:
            t = r["original_title"][:40]
            L.append(f"- [{r['duplicate_type']}] {r['source_id']}/{r['category']}: {t}")
        L.append("")

    stales = [r for r in records if r["suspected_stale"] == "true"]
    if stales:
        L.append(f"## Stale articles ({len(stales)} items)")
        L.append("")
        for r in stales[:15]:
            L.append(f"- {r['source_id']}/{r['category']}: {r['original_title'][:50]} ({r['article_age_minutes']}m old)")
        L.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def _save_json(records, path):
    """Save records as JSON for offline replay."""
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def run_diagnosis_from_file(json_path, db, output_dir):
    """Replay diagnosis from saved JSON. No network access."""
    import json as jm
    with open(json_path, encoding="utf-8") as f:
        records = jm.load(f)

    output_dir.mkdir(parents=True, exist_ok=True)
    run_started_at = datetime.now()
    ts = run_started_at.strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"collection_{ts}.csv"
    md_path = output_dir / f"diagnosis_{ts}.md"
    latest_csv = output_dir / "latest_collection.csv"
    latest_md = output_dir / "latest_diagnosis.md"

    if db:
        for rec in records:
            nu = rec.get("normalized_url", "")
            rec["already_in_database"] = "true" if db.article_exists(nu) else "false"
        records, summary = _reclassify(records)
    else:
        records, summary = _reclassify(records)

    _write_csv(records, csv_path)
    shutil.copy2(csv_path, latest_csv)
    _write_md(records, summary, md_path, run_started_at)
    shutil.copy2(md_path, latest_md)
    logger.info("Replay: CSV=%s, MD=%s", csv_path, md_path)


def _reclassify(records):
    """Re-classify duplicates and return (records, summary)."""
    seen_urls = {}
    seen_titles = {}
    for i, rec in enumerate(records):
        nu = rec.get("normalized_url", "")
        nt = rec.get("normalized_title", "")
        sn = rec.get("source_name", "")
        seen_urls.setdefault(nu, []).append(i)
        seen_titles.setdefault((sn, nt), []).append(i)

    for i, rec in enumerate(records):
        nu = rec.get("normalized_url", "")
        nt = rec.get("normalized_title", "")
        sn = rec.get("source_name", "")
        reasons = []
        ui = seen_urls.get(nu, [])
        rec["url_duplicate_count_in_run"] = len([x for x in ui if x != i])
        ti = seen_titles.get((sn, nt), [])
        rec["title_duplicate_count_in_run"] = len([x for x in ti if x != i])
        if rec.get("already_in_database") == "true":
            reasons.append("already_in_database")
        if rec["url_duplicate_count_in_run"] > 0:
            reasons.append("same_normalized_url")
            rec["duplicate_group_id"] = f"url_dup_{min(ui)}"
        if rec["title_duplicate_count_in_run"] > 0 and rec["url_duplicate_count_in_run"] == 0:
            reasons.append("same_source_same_normalized_title")
            if not rec.get("duplicate_group_id"):
                rec["duplicate_group_id"] = f"title_dup_{min(ti)}"
        if rec.get("suspected_stale") == "true":
            reasons.append("suspected_stale")
        rec["duplicate_type"] = "multiple_reasons" if len(reasons) > 1 else (
            reasons[0] if reasons else "none")
        rec["diagnostic_reason"] = "; ".join(reasons) if reasons else "normal"

    total = len(records)
    summary = {
        "total": total,
        "unique_urls": len(set(r.get("normalized_url", "") for r in records)),
        "url_duplicates": sum(1 for r in records if int(r.get("url_duplicate_count_in_run", 0)) > 0),
        "title_duplicates": sum(1 for r in records if int(r.get("title_duplicate_count_in_run", 0)) > 0),
        "cross_category": sum(1 for r in records if "same_article_across_categories" in r.get("diagnostic_reason", "")),
        "already_in_db": sum(1 for r in records if r.get("already_in_database") == "true"),
        "stale": sum(1 for r in records if r.get("suspected_stale") == "true"),
        "no_publish_time": sum(1 for r in records if not r.get("published_at_parsed", "")),
    }
    return records, summary
