"""Program registry config and multi-program tagging."""

from datetime import datetime, timezone

import pytest

from app import programs
from app.database import Database
from app.models import Article
from app.programs import tagging

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def test_config_is_valid_and_complete():
    cfg = programs.load()
    assert set(cfg["programs"]) == {"P1", "P2", "P3", "P4", "P5", "P6"}
    for pid, p in cfg["programs"].items():
        assert len(p["hypotheses"]) >= 3, pid  # no single favoured hypothesis
        assert all(q["indicators"] for q in p["questions"])


def test_validation_rejects_single_hypothesis_program():
    import copy

    cfg = copy.deepcopy(programs.load())
    cfg["programs"]["P1"]["hypotheses"] = {"H1": "only one"}
    with pytest.raises(programs.ProgramConfigError, match="three rival"):
        programs.validate(cfg)


@pytest.mark.parametrize("title, summary, category, expected", [
    ("國防部：共機25架次擾台", None, "military", {"P4"}),
    ("美國宣布對台軍售 DSCA 通知國會", None, "international", {"P1", "P3"}),
    ("台積電赴美 投審會核准", None, "economy", {"P5"}),
    ("立法院三讀 國民黨 民眾黨 共同提案", None, "politics", {"P6"}),
    ("外交部長出訪友邦 簽署合作備忘錄", None, "politics", {"P2"}),
    ("台北市長視察市場", "市府推動攤商補助", "politics", set()),
    ("美国宣布对台军售", None, "international", {"P1", "P3"}),  # Simplified folds
])
def test_routing(title, summary, category, expected):
    assert set(tagging.match(title, summary, category)) == expected


def test_reasons_name_the_matched_terms():
    reasons = tagging.match("台積電 半導體 出口管制 新規", None, "economy")["P5"]
    assert any(r.startswith("support:") for r in reasons)


def test_tag_range_stores_multi_program_tags(tmp_path):
    with Database(tmp_path / "n.db") as db:
        db.save_articles([Article(source_id="s", source_name="中央社", category="international",
                                  title="美國宣布對台軍售 DSCA 通知國會", url="https://x/1",
                                  published_at=datetime(2026, 11, 14, 8), fetched_at=datetime(2026, 11, 14, 8),
                                  position=1)])
        counts = tagging.tag_range(db.conn, datetime(2026, 11, 13, tzinfo=timezone.utc), NOW, now=NOW)
        assert counts == {"P1": 1, "P3": 1}
        assert tagging.programs_for(db.conn, "https://x/1") == ["P1", "P3"]
        assert tagging.urls_for(db.conn, "P3") == {"https://x/1"}
        assert tagging.tag_range(db.conn, datetime(2026, 11, 13, tzinfo=timezone.utc), NOW, now=NOW) == counts
        assert db.count_topics() == 0  # existing topic table untouched


def test_run_hook_off_by_default(tmp_path):
    with Database(tmp_path / "n.db") as db:
        assert tagging.run_safe(db) == {"status": "disabled"}
