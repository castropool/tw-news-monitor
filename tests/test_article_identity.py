import pytest, json, tempfile, os
from datetime import datetime
from zoneinfo import ZoneInfo
TAIPEI = ZoneInfo("Asia/Taipei")
from app.article_identity import article_identity_key, deduplicate_articles_by_identity
from app.models import Article
from app.database import Database

def make(url, title="T", src="S"):
    now = datetime.now(TAIPEI)
    return Article(source_id="t", source_name=src, category="pol", title=title, url=url, published_at=now, fetched_at=now, position=1)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "udn_alias_duplicates_20260718.json")

class TestIdentityKey:
    def test_udn_alias_same_key(self):
        assert article_identity_key("https://udn.com/news/story/6656/9635664") == "udn:9635664"
        assert article_identity_key("https://udn.com/news/story/124948/9635664") == "udn:9635664"
    def test_udn_www_stripped(self):
        assert article_identity_key("https://www.udn.com/news/story/7238/9635667") == "udn:9635667"
    def test_cna_uses_url_fallback(self):
        k = article_identity_key("https://www.cna.com.tw/news/aopl/202607180018.aspx")
        assert k.startswith("cna:")
    def test_non_standard_udn_path_fallback(self):
        k = article_identity_key("https://udn.com/news/something/else/9635664")
        assert k.startswith("url:")

    def test_cna_identity_key(self):
        k = article_identity_key("https://www.cna.com.tw/news/aopl/202607180018.aspx")
        assert k == "cna:aopl:202607180018"

    def test_cna_cross_section_identity(self):
        k = article_identity_key("https://www.cna.com.tw/news/aie/202607180019.aspx")
        assert k == "cna:aie:202607180019"

    def test_ltn_identity_key(self):
        k = article_identity_key("https://news.ltn.com.tw/news/politics/breakingnews/1234567")
        assert k.startswith("ltn:")
        assert "politics" in k

    def test_ltn_economy_identity(self):
        k = article_identity_key("https://news.ltn.com.tw/news/business/breakingnews/7654321")
        assert k.startswith("ltn:")
        assert "business" in k

    def test_fixture_all_entries(self):
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            fix = json.load(f)
        assert len(fix) == 4
        for entry in fix:
            for url in entry["urls"]:
                assert article_identity_key(url) == entry["expected_key"]

class TestIdentityDedup:
    def test_same_run_alias(self):
        arts = [make("https://udn.com/news/story/6656/9635664", "First"), make("https://udn.com/news/story/124948/9635664", "Second")]
        unique, dups = deduplicate_articles_by_identity(arts)
        assert len(unique) == 1 and len(dups) == 1 and unique[0].title == "First"
    def test_different_ids_not_merged(self):
        arts = [make("https://udn.com/news/story/6656/9635664"), make("https://udn.com/news/story/6656/9635665")]
        unique, dups = deduplicate_articles_by_identity(arts)
        assert len(unique) == 2 and len(dups) == 0
    def test_different_domains_not_merged(self):
        arts = [make("https://udn.com/news/story/6656/9635664"), make("https://www.cna.com.tw/article/9635664")]
        unique, dups = deduplicate_articles_by_identity(arts)
        assert len(unique) == 2 and len(dups) == 0
    def test_title_change_still_duplicate(self):
        arts = [make("https://udn.com/news/story/6656/9635667", "Old"), make("https://udn.com/news/story/7238/9635667", "Updated")]
        unique, dups = deduplicate_articles_by_identity(arts)
        assert len(unique) == 1 and len(dups) == 1
    def test_three_way_alias(self):
        arts = [make("https://udn.com/news/story/6656/9635664","A"), make("https://udn.com/news/story/124948/9635664","B"), make("https://udn.com/news/story/7238/9635664","C")]
        unique, dups = deduplicate_articles_by_identity(arts)
        assert len(unique) == 1 and len(dups) == 2
    def test_exact_url_still_deduped(self):
        arts = [make("https://udn.com/news/story/6656/9635664"), make("https://udn.com/news/story/6656/9635664")]
        unique, dups = deduplicate_articles_by_identity(arts)
        assert len(unique) == 1 and len(dups) == 1

class TestIdentityHistorical:
    def test_cross_period_alias_not_reinserted(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tp = tmp.name
        try:
            db = Database(tp); db.connect(); db.create_tables()
            db.save_articles([make("https://udn.com/news/story/6656/9635746", "Original")])
            existing_urls = set(db.get_all_article_urls())
            existing_ids = {article_identity_key(u) for u in existing_urls}
            a2 = make("https://udn.com/news/story/124948/9635746", "Alias")
            assert article_identity_key(a2.url) in existing_ids
            assert a2.url not in existing_urls
            db.close()
        finally:
            os.unlink(tp)
    def test_get_all_article_urls_works(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tp = tmp.name
        try:
            db = Database(tp); db.connect(); db.create_tables()
            db.save_articles([make("https://udn.com/news/story/6656/9635000")])
            urls = db.get_all_article_urls()
            assert len(urls) == 1 and urls[0] == "https://udn.com/news/story/6656/9635000"
            db.close()
        finally:
            os.unlink(tp)
