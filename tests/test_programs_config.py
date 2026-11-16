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
    # Live false positives from the first server run: chip/company stories are not P1.
    ("輝達黃仁勳：美國 中國 台灣 晶片需求強勁", None, "economy", set()),
    ("義大利 無人機 混合戰 國會議員 訪問團", None, "international", set()),
    ("白宮：美國 中國 台灣 晶片出口管制將調整", None, "international", {"P1", "P5"}),
    ("AUKUS核潛艦合作進度超前 美軍 澳洲 演習", None, "military", {"P4"}),  # military → P4 only, not P3
    # Second server run: U.S.-China chip and summit stories without Taiwan are not P1.
    ("川普政府放寬輝達H20晶片對中國出口 白宮 美國", None, "international", set()),
    ("美中峰會 川普 習近平 貿易協議", None, "international", set()),
    ("美中峰會 川普 談台灣問題", None, "international", {"P1"}),
    ("美軍與日本自衛隊演習 強化台海防衛", None, "international", {"P3"}),
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



def test_retagging_drops_stale_rule_tags_but_keeps_lead_tags(tmp_path):
    with Database(tmp_path / "n.db") as db:
        db.save_articles([Article(source_id="s", source_name="中央社", category="economy",
                                  title="輝達黃仁勳：美國 中國 台灣 晶片需求強勁", url="https://x/nv",
                                  published_at=datetime(2026, 11, 14, 8), fetched_at=datetime(2026, 11, 14, 8),
                                  position=1)])
        tagging.ensure_table(db.conn)
        db.conn.executemany("INSERT INTO program_tags VALUES (?, ?, ?, ?)", [
            ("https://x/nv", "P1", '["support:美國"]', "old"),       # stale rule tag from looser rules
            ("https://x/nv", "P5", '["lead:7"]', "old"),             # added by an approved lead
        ])
        db.conn.commit()
        tagging.tag_range(db.conn, datetime(2026, 11, 13, tzinfo=timezone.utc), NOW, now=NOW)
        assert tagging.programs_for(db.conn, "https://x/nv") == ["P5"]
