import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.database import Database
from app.models import Article
from app.main import _classify_delivery_articles, _get_source_baselines

TAIPEI = ZoneInfo("Asia/Taipei")
RUN = datetime(2026, 7, 19, 9, 44, tzinfo=TAIPEI)

def make(pub=None, url="https://t.com/a", title="T", sid="test", src="Test", mins=None):
    if mins is not None:
        pub = RUN - timedelta(minutes=mins)
    pub_aware = pub.replace(tzinfo=TAIPEI) if pub and pub.tzinfo is None else pub
    return Article(source_id=sid, source_name=src, category="politics", title=title, url=url, published_at=pub_aware, fetched_at=RUN, position=1)

# Test 1: Only catch-up must generate delivery articles
def test_only_catch_up_delivers():
    arts = [make(mins=120, url="a"), make(mins=180, url="b")]
    bl = {"test": 5}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["fresh_articles"]) == 0
    assert len(result["catch_up_eligible"]) == 2
    assert len(result["catch_up_urls"]) == 2
    assert len(result["stale_articles"]) == 0

# Test 2: Catch-up disabled keeps old behavior
def test_catch_up_disabled():
    arts = [make(mins=120, url="a"), make(mins=180, url="b")]
    bl = {"test": 5}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=False)
    assert len(result["fresh_articles"]) == 0
    assert len(result["catch_up_eligible"]) == 0
    assert len(result["catch_up_urls"]) == 0
    assert len(result["stale_articles"]) == 2

# Test 3: New source baseline protection
def test_new_source_baseline():
    arts = [make(mins=120, url="a", sid="new_source")]
    bl = {"new_source": 0}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["catch_up_eligible"]) == 0
    assert len(result["catch_up_urls"]) == 0
    assert len(result["stale_articles"]) == 1
    assert len(result["baseline_excluded"]) == 1


def test_source_baseline_snapshot_is_taken_before_insert(tmp_path):
    db = Database(tmp_path / "baseline.db")
    db.connect()
    db.create_tables()
    sources = [{"id": "new_source"}]

    before_insert = _get_source_baselines(db, sources)
    db.save_article(make(mins=120, sid="new_source", url="https://t.com/new"))
    after_insert = _get_source_baselines(db, sources)

    assert before_insert == {"new_source": 0}
    assert after_insert == {"new_source": 1}
    assert before_insert["new_source"] == 0
    db.close()

# Test 4: New source fresh still pushes
def test_new_source_fresh():
    arts = [
        make(mins=30, url="a", sid="new_source"),
        make(mins=120, url="b", sid="new_source"),
    ]
    bl = {"new_source": 0}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["fresh_articles"]) == 1
    assert len(result["catch_up_eligible"]) == 0
    assert len(result["stale_articles"]) == 1

# Test 5: Fresh and catch-up mixed
def test_mixed_fresh_catchup():
    arts = [
        make(mins=30, url="a"),
        make(mins=120, url="b"),
        make(mins=180, url="c"),
    ]
    bl = {"test": 5}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["fresh_articles"]) == 1
    assert len(result["catch_up_eligible"]) == 2
    assert len(result["stale_articles"]) == 0
    # Total delivery = fresh + catch_up
    assert len(result["fresh_articles"]) + len(result["catch_up_eligible"]) == 3

# Test 6: Historical articles not re-delivered (not in inserted set)
def test_historical_not_delivered():
    existing_arts = [make(mins=120, url="existing1"), make(mins=180, url="existing2")]
    bl = {"test": 5}
    # These articles are NOT in the inserted set, so they can't be classified
    result = _classify_delivery_articles([], bl, RUN, catch_up_enabled=True)
    assert len(result["fresh_articles"]) == 0
    assert len(result["catch_up_eligible"]) == 0

# Test 7: Time-unknown articles not catch-up
def test_unknown_time_not_catchup():
    a = make(url="a")
    a.published_at = None
    arts = [a]
    bl = {"test": 5}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["unknown_articles"]) == 1
    assert len(result["catch_up_eligible"]) == 0

# Test 8: Future articles not catch-up
def test_future_not_catchup():
    arts = [make(mins=-20, url="a")]
    bl = {"test": 5}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["future_articles"]) == 1
    assert len(result["catch_up_eligible"]) == 0

# Test 9: Fresh + catch_up delivery sum
def test_delivery_sum():
    arts = [
        make(mins=30, url="a"),
        make(mins=120, url="b"),
        make(mins=600, url="c"),
        make(mins=800, url="d"),
    ]
    bl = {"test": 5}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["fresh_articles"]) == 1
    assert len(result["catch_up_eligible"]) == 2  # b(120min) and c(600min)
    assert len(result["stale_articles"]) == 1  # d(800min, over 720)
    assert len(result["fresh_articles"]) + len(result["catch_up_eligible"]) == 3

# Test 10: Baseline excluded counts separately
def test_baseline_excluded_count():
    arts = [make(mins=120, url="a", sid="old_src")]
    bl = {"old_src": 0, "new_src": 0}
    result = _classify_delivery_articles(arts, bl, RUN, catch_up_enabled=True)
    assert len(result["catch_up_eligible"]) == 0
    assert len(result["baseline_excluded"]) == 1
    assert len(result["stale_articles"]) == 1

# Test 11: Normal fresh without catch_up config
def test_fresh_no_config():
    arts = [make(mins=30, url="a")]
    bl = {"test": 5}
    result = _classify_delivery_articles(arts, bl, RUN)
    assert len(result["fresh_articles"]) == 1
    assert len(result["catch_up_eligible"]) == 0
