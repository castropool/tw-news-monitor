"""Real-network smoke test for the five military publishers.

This command never creates a notifier.  It additionally requires the normal
Feishu kill switch so an operator cannot mistake it for a delivery command.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import yaml
from docx import Document

from app.content_filter import load_content_filter
from app.database import Database
from app.main import collect_all
from app.military import load_military_config
from app.time_utils import TAIPEI
from app.word_digest import build_word_digest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE_IDS = (
    "ltn_defense",
    "udn_military",
    "nownews_military",
    "ydn_defense_focus",
    "ydn_weapons_tour",
    "ydn_military_world",
    "mna_military",
)
PUBLISHERS = {
    "自由军武": ("ltn_defense",),
    "联合军事": ("udn_military",),
    "NOWnews军武": ("nownews_military",),
    "青年日报": (
        "ydn_defense_focus",
        "ydn_weapons_tour",
        "ydn_military_world",
    ),
    "军事新闻通讯社": ("mna_military",),
}


def _disabled_feishu() -> bool:
    return os.getenv("DISABLE_FEISHU_SEND", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "data" / "military_smoke",
    )
    args = parser.parse_args()

    if not _disabled_feishu():
        raise RuntimeError("Set DISABLE_FEISHU_SEND=1 before military smoke")

    payload = yaml.safe_load(
        (PROJECT_ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")
    )
    configured = {source["id"]: source for source in payload.get("sources", [])}
    missing = [source_id for source_id in EXPECTED_SOURCE_IDS if source_id not in configured]
    if missing:
        raise RuntimeError(f"Missing military source configuration: {missing}")

    stamp = datetime.now(TAIPEI).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_root / stamp
    output_dir.mkdir(parents=True, exist_ok=False)
    db = Database(output_dir / "military_smoke.db")
    db.connect()
    db.create_tables()
    content_config = load_content_filter(PROJECT_ROOT / "config" / "content_filter.yaml")
    military_config = load_military_config(PROJECT_ROOT / "config" / "military.yaml")

    source_results = {}
    try:
        for source_id in EXPECTED_SOURCE_IDS:
            inserted, total, duplicates, failed, _, _, filtered = collect_all(
                [configured[source_id]],
                db,
                content_config,
                military_config,
            )
            topic_count = len(
                db.get_topic_urls([article.url for article in inserted], "military")
            )
            source_results[source_id] = {
                "fetched": total,
                "inserted": len(inserted),
                "duplicates": duplicates,
                "filtered": filtered,
                "military_topics": topic_count,
                "failed": source_id in failed,
            }

        articles = db.get_articles_since(datetime(2000, 1, 1))
        military_urls = db.get_topic_urls(
            [article.url for article in articles], "military"
        )
        word_path = build_word_digest(
            articles,
            output_dir,
            generated_at=datetime.now(TAIPEI),
            military_topic_urls=military_urls,
        )
        doc = Document(word_path)
        level_one = [
            paragraph.text
            for paragraph in doc.paragraphs
            if paragraph.style.name == "Heading 1"
        ]

        publisher_results = {}
        for publisher, source_ids in PUBLISHERS.items():
            members = [source_results[source_id] for source_id in source_ids]
            publisher_results[publisher] = {
                "fetched": sum(member["fetched"] for member in members),
                "inserted": sum(member["inserted"] for member in members),
                "military_topics": sum(member["military_topics"] for member in members),
                "passed": all(not member["failed"] for member in members)
                and sum(member["fetched"] for member in members) > 0,
            }

        passed = (
            all(result["passed"] for result in publisher_results.values())
            and len(military_urls) > 0
            and any("军武动态" in heading for heading in level_one)
            and word_path.exists()
        )
        report = {
            "generated_at": datetime.now(TAIPEI).isoformat(),
            "disable_feishu_send": True,
            "source_results": source_results,
            "publisher_results": publisher_results,
            "publishers_passed": sum(
                result["passed"] for result in publisher_results.values()
            ),
            "publishers_total": len(publisher_results),
            "database": str(db._db_path),
            "military_topic_count": len(military_urls),
            "word": str(word_path),
            "word_level_one_headings": level_one,
            "passed": passed,
        }
        report_path = output_dir / "smoke_result.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
