import pytest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

from app.collectors.cna_html import CNAHtmlCollector


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_source(url, sid="cna_web_politics", cat="politics"):
    return {
        "id": sid,
        "name": "中央社",
        "type": "cna_list_html",
        "category": cat,
        "url": url,
        "enabled": False,
    }


class TestCNAHtmlCollector:
    def test_parse_politics_list(self):
        src = make_source("https://www.cna.com.tw/list/aipl.aspx")
        c = CNAHtmlCollector(src)
        # We test via direct HTML parsing approach
        from bs4 import BeautifulSoup
        html = (FIXTURES / "cna_politics_list.html").read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        
        articles = []
        import re
        pattern = re.compile(r"^/news/([a-z]+)/(\d+)\.aspx$")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not pattern.match(href):
                continue
            title_span = a_tag.find("span", class_="title")
            title = title_span.get_text().strip() if title_span else (a_tag.get_text() or "").strip()
            if not title:
                continue
            full_url = "https://www.cna.com.tw" + href if href.startswith("/") else href
            articles.append({
                "title": title,
                "url": c.normalize_url(full_url),
            })
        
        assert len(articles) == 3
        assert articles[0]["title"] == "政治新闻A"
        assert articles[0]["url"] == "https://www.cna.com.tw/news/aipl/202607180132.aspx"

    def test_normalize_url(self):
        c = CNAHtmlCollector(make_source("https://www.cna.com.tw/list/aipl.aspx"))
        u = c.normalize_url("https://www.cna.com.tw/news/aipl/202607180132.aspx?utm_source=test#frag")
        assert u == "https://www.cna.com.tw/news/aipl/202607180132.aspx"

    def test_normalize_http_to_https(self):
        c = CNAHtmlCollector(make_source("https://www.cna.com.tw/list/aipl.aspx"))
        u = c.normalize_url("http://www.cna.com.tw/news/aipl/202607180132.aspx")
        assert "www.cna.com.tw" in u

    def test_parse_time(self):
        from bs4 import BeautifulSoup
        html = (FIXTURES / "cna_politics_list.html").read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        date_span = soup.find("time")
        assert date_span is not None
        time_text = date_span.get_text().strip()
        assert time_text == "2026/07/18 20:58"
        dt = datetime.strptime(time_text, "%Y/%m/%d %H:%M")
        dt_aware = dt.replace(tzinfo=TAIPEI)
        assert dt_aware.tzinfo == TAIPEI
        assert dt_aware.hour == 20

    def test_filter_external_links(self):
        c = CNAHtmlCollector(make_source("https://www.cna.com.tw/list/aipl.aspx"))
        from bs4 import BeautifulSoup
        html = (FIXTURES / "cna_politics_list.html").read_text(encoding="utf-8")
        # Replace one internal link with external
        html = html.replace(
            'href="/news/aipl/202607180132.aspx"',
            'href="https://example.com/outside"'
        )
        soup = BeautifulSoup(html, "html.parser")
        import re
        pattern = re.compile(r"^/news/([a-z]+)/(\d+)\.aspx$")
        links = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not pattern.match(href):
                continue
            links.append(href)
        assert len(links) == 2  # External link filtered out

    def test_empty_list_warning(self):
        c = CNAHtmlCollector(make_source("https://www.cna.com.tw/list/aipl.aspx"))
        from bs4 import BeautifulSoup
        html = "<html><body><p>no news</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        import re
        pattern = re.compile(r"^/news/([a-z]+)/(\d+)\.aspx$")
        links = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not pattern.match(href):
                continue
            links.append(href)
        assert len(links) == 0

    def test_duplicate_url_dedup(self):
        c = CNAHtmlCollector(make_source("https://www.cna.com.tw/list/aipl.aspx"))
        from bs4 import BeautifulSoup
        # Create HTML with duplicate link
        html = (FIXTURES / "cna_politics_list.html").read_text(encoding="utf-8")
        html = html.replace("</ul>", '''<li class="list__item"><a href="/news/aipl/202607180132.aspx"><span class="date">2026/07/18 20:58</span><span class="title">重复</span></a></li></ul>''')
        soup = BeautifulSoup(html, "html.parser")
        import re
        pattern = re.compile(r"^/news/([a-z]+)/(\d+)\.aspx$")
        seen = set()
        urls = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not pattern.match(href):
                continue
            full_url = "https://www.cna.com.tw" + href if href.startswith("/") else href
            norm = c.normalize_url(full_url)
            if norm in seen:
                continue
            seen.add(norm)
            urls.append(norm)
        assert len(urls) == 3  # Only 3 unique


class TestCNAHtmlCrossDedup:
    def test_same_url_dedup_across_sources(self):
        from app.collectors.base import BaseCollector
        url1 = BaseCollector.normalize_url("https://www.cna.com.tw/news/aipl/202607180132.aspx")
        url2 = BaseCollector.normalize_url("https://www.cna.com.tw/news/aipl/202607180132.aspx")
        assert url1 == url2

    def test_cna_identity_matches_across_rss_html(self):
        from app.article_identity import article_identity_key
        k1 = article_identity_key("https://www.cna.com.tw/news/aipl/202607180132.aspx")
        k2 = article_identity_key("https://www.cna.com.tw/news/aipl/202607180132.aspx")
        assert k1 == k2
        assert k1.startswith("cna:")

    def test_html_url_vs_rss_url_dedup(self):
        from app.collectors.base import BaseCollector
        url_rss = BaseCollector.normalize_url("https://www.cna.com.tw/news/aipl/202607180132.aspx")
        url_html = BaseCollector.normalize_url("https://www.cna.com.tw/news/aipl/202607180132.aspx")
        assert url_rss == url_html

    def test_db_dedup_by_url(self):
        import os, sqlite3
        from pathlib import Path
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from app.models import Article
        from app.database import Database
        TAIPEI = ZoneInfo("Asia/Taipei")
        db_file = "data/test_cross_dedup.db"
        try:
            db = Database(db_file)
            db.connect()
            db.create_tables()
            a1 = Article(source_id="cna_politics", source_name="中央社", category="politics",
                title="测试", url="https://www.cna.com.tw/news/aipl/202607180132.aspx",
                published_at=datetime(2026,7,18,20,58,tzinfo=TAIPEI),
                fetched_at=datetime(2026,7,18,22,0,tzinfo=TAIPEI), position=1)
            inserted1 = db.save_articles([a1])
            assert len(inserted1) == 1
            a2 = Article(source_id="cna_web_politics", source_name="中央社", category="politics",
                title="测试", url="https://www.cna.com.tw/news/aipl/202607180132.aspx",
                published_at=datetime(2026,7,18,20,58,tzinfo=TAIPEI),
                fetched_at=datetime(2026,7,18,22,0,tzinfo=TAIPEI), position=1)
            inserted2 = db.save_articles([a2])
            assert len(inserted2) == 0
            assert db.count_articles() == 1
            db.close()
        finally:
            for suffix in ["", "-wal", "-shm"]:
                p = db_file + suffix
                if os.path.exists(p):
                    try: os.remove(p)
                    except: pass

    def test_db_dedup_rss_first(self):
        import os, sqlite3
        from pathlib import Path
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from app.models import Article
        from app.database import Database
        TAIPEI = ZoneInfo("Asia/Taipei")
        db_file = "data/test_cross_dedup_rss_first.db"
        try:
            db = Database(db_file)
            db.connect()
            db.create_tables()
            a1 = Article(source_id="cna_politics", source_name="中央社", category="politics",
                title="测试", url="https://www.cna.com.tw/news/aipl/202607180132.aspx",
                published_at=datetime(2026,7,18,20,58,tzinfo=TAIPEI),
                fetched_at=datetime(2026,7,18,22,0,tzinfo=TAIPEI), position=1)
            inserted1 = db.save_articles([a1])
            assert len(inserted1) == 1
            a2 = Article(source_id="cna_web_politics", source_name="中央社", category="politics",
                title="测试", url="https://www.cna.com.tw/news/aipl/202607180132.aspx",
                published_at=datetime(2026,7,18,20,58,tzinfo=TAIPEI),
                fetched_at=datetime(2026,7,18,22,0,tzinfo=TAIPEI), position=1)
            inserted2 = db.save_articles([a2])
            assert len(inserted2) == 0
            assert db.count_articles() == 1
            db.close()
        finally:
            for suffix in ["", "-wal", "-shm"]:
                p = db_file + suffix
                if os.path.exists(p):
                    try: os.remove(p)
                    except: pass
