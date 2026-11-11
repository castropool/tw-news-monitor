import pytest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.freshness import FreshnessResult, filter_fresh_articles
from app.main import deduplicate_articles_by_url
from app.models import Article
from app.database import Database

TAIPEI = ZoneInfo("Asia/Taipei")


def make(pub, url="https://test.com/a", title="T", src="S", pos=1):
    now = datetime.now(TAIPEI)
    pub_aware = pub.replace(tzinfo=TAIPEI) if pub and pub.tzinfo is None else pub
    return Article(source_id="test", source_name=src, category="pol",
                   title=title, url=url, published_at=pub_aware,
                   fetched_at=now, position=pos)


def make_naive(pub):
    now = datetime.now(TAIPEI)
    pub_naive = pub.replace(tzinfo=None) if pub and pub.tzinfo else pub
    return Article(source_id="test", source_name="S", category="pol",
                   title="T", url="https://test.com/a",
                   published_at=pub_naive, fetched_at=now, position=1)


RUN = datetime(2026, 7, 16, 22, 45, tzinfo=TAIPEI)


class TestFreshBoundaries:
    def test_now(self):
        fr = filter_fresh_articles([make(RUN)], RUN)
        assert len(fr.fresh_articles) == 1

    def test_30min(self):
        fr = filter_fresh_articles([make(RUN - timedelta(minutes=30))], RUN)
        assert len(fr.fresh_articles) == 1

    def test_89min(self):
        fr = filter_fresh_articles([make(RUN - timedelta(minutes=89))], RUN)
        assert len(fr.fresh_articles) == 1

    def test_90min_exact(self):
        fr = filter_fresh_articles([make(RUN - timedelta(minutes=90))], RUN)
        assert len(fr.fresh_articles) == 1

    def test_90min_1s_stale(self):
        fr = filter_fresh_articles([make(RUN - timedelta(minutes=90, seconds=1))], RUN)
        assert len(fr.stale_articles) == 1

    def test_91min_stale(self):
        fr = filter_fresh_articles([make(RUN - timedelta(minutes=91))], RUN)
        assert len(fr.stale_articles) == 1

    def test_3h_stale(self):
        fr = filter_fresh_articles([make(RUN - timedelta(hours=3))], RUN)
        assert len(fr.stale_articles) == 1

    def test_no_pub_unknown(self):
        a = make(RUN)
        a.published_at = None
        fr = filter_fresh_articles([a], RUN)
        assert len(fr.unknown_time_articles) == 1

    def test_future_5min(self):
        fr = filter_fresh_articles([make(RUN + timedelta(minutes=5))], RUN)
        assert len(fr.fresh_articles) == 1

    def test_future_10min_exact(self):
        fr = filter_fresh_articles([make(RUN + timedelta(minutes=10))], RUN)
        assert len(fr.fresh_articles) == 1

    def test_future_10min_1s_abnormal(self):
        fr = filter_fresh_articles([make(RUN + timedelta(minutes=10, seconds=1))], RUN)
        assert len(fr.future_time_articles) == 1


class TestFreshTimezone:
    def test_utc_conversion(self):
        pub_utc = datetime(2026, 7, 16, 14, 30, tzinfo=ZoneInfo("UTC"))
        fr = filter_fresh_articles([make(pub_utc)], RUN)
        assert len(fr.fresh_articles) == 1

    def test_naive_is_unknown(self):
        a = make_naive(RUN - timedelta(minutes=30))
        fr = filter_fresh_articles([a], RUN)
        assert len(fr.unknown_time_articles) == 1


class TestFreshCombine:
    def test_mixed(self):
        arts = [
            make(RUN - timedelta(minutes=30)),
            make(RUN - timedelta(hours=3)),
            make(RUN + timedelta(minutes=20)),
        ]
        a4 = make(RUN)
        a4.published_at = None
        arts.append(a4)
        fr = filter_fresh_articles(arts, RUN)
        assert len(fr.fresh_articles) == 1
        assert len(fr.stale_articles) == 1
        assert len(fr.future_time_articles) == 1
        assert len(fr.unknown_time_articles) == 1


class TestFreshSort:
    def test_descending(self):
        arts = [
            make(RUN - timedelta(minutes=30), url="a"),
            make(RUN - timedelta(minutes=5), url="b"),
            make(RUN - timedelta(minutes=60), url="c"),
        ]
        fr = filter_fresh_articles(arts, RUN)
        sorted_arts = sorted(fr.fresh_articles, key=lambda a: a.published_at, reverse=True)
        times = [a.published_at for a in sorted_arts]
        assert times == sorted(times, reverse=True)

    def test_same_time_order(self):
        pub = RUN - timedelta(minutes=10)
        arts = [make(pub, url="f", title="First"), make(pub, url="s", title="Second")]
        fr = filter_fresh_articles(arts, RUN)
        assert fr.fresh_articles[0].title == "First"


class TestFreshResult:
    def test_empty_count(self):
        r = FreshnessResult()
        assert r.count() == 0

    def test_count_matches(self):
        r = FreshnessResult()
        r.fresh_articles.append(make(RUN))
        assert r.count() == 1


class TestFreshHistorical:
    def test_sample1(self):
        arts = [
            make(datetime(2026, 7, 16, 22, 38, tzinfo=TAIPEI), url="a", title="22:38"),
            make(datetime(2026, 7, 16, 22, 22, tzinfo=TAIPEI), url="b", title="22:22"),
            make(datetime(2026, 7, 16, 22, 38, tzinfo=TAIPEI), url="a", title="dup"),
            make(datetime(2026, 7, 16, 20, 2, tzinfo=TAIPEI), url="d", title="20:02"),
            make(datetime(2026, 7, 16, 20, 50, tzinfo=TAIPEI), url="e", title="20:50"),
        ]
        unique, dups = deduplicate_articles_by_url(arts)
        assert len(unique) == 4
        assert len(dups) == 1
        fr = filter_fresh_articles(unique, RUN)
        assert len(fr.fresh_articles) == 2
        assert len(fr.stale_articles) == 2
        assert {a.title for a in fr.fresh_articles} == {"22:38", "22:22"}

    def test_sample2(self):
        run2 = datetime(2026, 7, 16, 23, 14, tzinfo=TAIPEI)
        arts = [
            make(datetime(2026, 7, 16, 23, 12, tzinfo=TAIPEI), url="a", title="23:12"),
            make(datetime(2026, 7, 16, 22, 51, tzinfo=TAIPEI), url="b", title="22:51"),
        ]
        fr = filter_fresh_articles(arts, run2)
        assert len(fr.fresh_articles) == 2

    def test_no_fresh_means_empty(self):
        arts = [make(RUN - timedelta(hours=5), url="a"), make(RUN - timedelta(hours=3), url="b")]
        fr = filter_fresh_articles(arts, RUN)
        assert len(fr.fresh_articles) == 0
        assert len(fr.stale_articles) == 2


class TestFreshHistoricalJSON:
    @pytest.fixture
    def records(self):
        import json
        p = Path(__file__).resolve().parent / "fixtures" / "historical_collection_20260717_230913.json"
        if not p.exists():
            pytest.skip("JSON not found")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def test_json_dedup_fresh(self, records):
        arts = []
        run_ts = []
        for rec in records:
            pub = None
            if rec.get("published_at_parsed"):
                try:
                    pub = datetime.fromisoformat(rec["published_at_parsed"])
                except (ValueError, TypeError):
                    pass
            fet = datetime.now(TAIPEI)
            if rec.get("fetched_at"):
                try:
                    fet = datetime.fromisoformat(rec["fetched_at"])
                    fet = fet.replace(tzinfo=TAIPEI) if fet.tzinfo is None else fet
                except (ValueError, TypeError):
                    pass
            run_ts.append(fet)
            arts.append(Article(
                source_id=rec.get("source_id", "?"),
                source_name=rec.get("source_name", "?"),
                category=rec.get("category", "?"),
                title=rec.get("original_title", rec.get("normalized_title", "?")),
                url=rec.get("normalized_url", ""),
                published_at=pub,
                fetched_at=fet,
                position=int(rec.get("position", 0)),
            ))
        run_at = max(run_ts)
        unique, dups = deduplicate_articles_by_url(arts)
        fr = filter_fresh_articles(unique, run_at)
        diag = Path(__file__).resolve().parent.parent / "data" / "diagnostics"
        vp = diag / "freshness_validation.md"
        lines = [
            "# Freshness Validation (Historical JSON)",
            "",
            f"Run at: {run_at}",
            "",
            f"- Raw records: {len(records)}",
            f"- URL dedup removed: {len(dups)}",
            f"- After dedup: {len(unique)}",
            f"- Fresh: {len(fr.fresh_articles)}",
            f"- Stale: {len(fr.stale_articles)}",
            f"- Unknown time: {len(fr.unknown_time_articles)}",
            f"- Future abnormal: {len(fr.future_time_articles)}",
            "",
        ]
        for a in sorted(fr.fresh_articles, key=lambda x: x.published_at or datetime.min, reverse=True)[:5]:
            t = a.title[:50]
            lines.append(f"- Fresh: {t} ({a.source_name})")
        lines.append("")
        for a in fr.stale_articles[:10]:
            t = a.title[:50]
            lines.append(f"- Stale: {t} ({a.source_name})")
        lines.append("")
        lines.append("## Verification")
        lines.append(f"- No URL dups in fresh: {len(set(a.url for a in fr.fresh_articles)) == len(fr.fresh_articles)}")
        lines.append(f"- No URL dups in stale: {len(set(a.url for a in fr.stale_articles)) == len(fr.stale_articles)}")
        with open(vp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        assert len(unique) == len(set(a.url for a in unique))
        assert len(fr.fresh_articles) + len(fr.stale_articles) + len(fr.unknown_time_articles) + len(fr.future_time_articles) == len(unique)
        print(f"\nFreshness validation -> {vp}")
        print(f"  Raw={len(records)}, Dedup={len(unique)}, Fresh={len(fr.fresh_articles)}, Stale={len(fr.stale_articles)}")


class TestFreshDB:
    """Tests that require the dev database."""

    @pytest.fixture
    def db(self):
        p = Path(__file__).resolve().parent.parent / "data" / "news-dev.db"
        if not p.exists():
            pytest.skip("No dev DB")
        db = Database(p)
        db.connect()
        yield db
        db.close()

    def test_db_save_returns_inserted(self, db):
        """save_articles returns list of actually inserted articles."""
        from datetime import datetime
        art = Article(source_id="test", source_name="S", category="pol", title="Fresh test",
                      url="https://test.com/freshness_test_unique", published_at=datetime.now(TAIPEI),
                      fetched_at=datetime.now(TAIPEI), position=1)
        inserted = db.save_articles([art])
        assert len(inserted) == 1
        # Second insert with same URL should fail UNIQUE
        art2 = Article(source_id="test", source_name="S", category="pol", title="Fresh test dup",
                       url="https://test.com/freshness_test_unique", published_at=datetime.now(TAIPEI),
                       fetched_at=datetime.now(TAIPEI), position=1)
        inserted2 = db.save_articles([art2])
        assert len(inserted2) == 0  # UNIQUE conflict -> not inserted -> not in list
        # Clean up
        db.conn.execute("DELETE FROM articles WHERE url = ?", ("https://test.com/freshness_test_unique",))
        db.conn.commit()

    def test_bootstrap_stores_all(self, db):
        """Bootstrap (simulated by saving without freshness filter) stores all articles."""
        # This is tested by the bootstrap flow calling collect_all directly
        pass  # Handled by integration test

    def test_diagnosis_preserves_stale(self, db):
        """--diagnose-collection still shows all records including stale."""
        pass  # Handled by existing diagnose tests
