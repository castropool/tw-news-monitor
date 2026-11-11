from datetime import datetime
import re
from bs4 import BeautifulSoup
from ..models import Article
from .base import BaseCollector
from ..time_utils import TAIPEI

CNA_NEWS_PATTERN = re.compile(r"^/news/([a-z]+)/(\d+)\.aspx$")

CATEGORY_SECTIONS = {
    "politics": frozenset({"aipl"}),
    "economy": frozenset({"afe"}),
    "international": frozenset({"aopl"}),
}


class CNAHtmlCollector(BaseCollector):
    "Collector for CNA HTML list pages, scoped to ul.mainList."
    BASE_URL = "https://www.cna.com.tw"

    def _get_allowed_sections(self):
        return CATEGORY_SECTIONS.get(self.category, frozenset())

    def collect(self):
        resp = self.client.get(self.url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        main_list = soup.find("ul", class_="mainList")
        if main_list is None:
            return []
        articles = []
        now = datetime.now()
        allowed = self._get_allowed_sections()
        seen_urls = set()
        for li in main_list.find_all("li", recursive=False):
            if len(articles) >= self.MAX_ITEMS:
                break
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"].strip()
            m = CNA_NEWS_PATTERN.match(href)
            if not m:
                continue
            section = m.group(1)
            if allowed and section not in allowed:
                continue
            full_url = self.BASE_URL + href if href.startswith("/") else href
            norm = self.normalize_url(full_url)
            if norm in seen_urls:
                continue
            seen_urls.add(norm)
            info_div = li.find("div", class_="listInfo")
            if info_div:
                h2 = info_div.find("h2")
                if h2:
                    sp = h2.find("span")
                    title = (sp.get_text() if sp else h2.get_text()).strip()
                else:
                    title = info_div.get_text().strip()
            else:
                title = a_tag.get_text().strip()
            if not title:
                continue
            pub = None
            te = li.find("time", class_="date")
            if te:
                dt_str = te.get("datetime", "") or te.get_text().strip()
                if dt_str:
                    try:
                        dt = datetime.fromisoformat(dt_str)
                        if dt.tzinfo is not None:
                            pub = dt.astimezone(TAIPEI)
                        else:
                            pub = dt.replace(tzinfo=TAIPEI)
                    except:
                        pass
            articles.append(Article(
                source_id=self.source_id, source_name=self.source_name,
                category=self.category, title=title, url=norm,
                published_at=pub, fetched_at=now, position=len(articles) + 1,
            ))
        return articles