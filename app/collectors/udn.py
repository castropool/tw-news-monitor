from datetime import datetime

from bs4 import BeautifulSoup

from ..models import Article
from .base import BaseCollector
from ..time_utils import TAIPEI


class UDNCollector(BaseCollector):
    """Collector for UDN (聯合新聞網) category pages."""

    BASE_URL = "https://udn.com"

    def collect(self) -> list[Article]:
        resp = self.client.get(self.url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        articles: list[Article] = []
        now = datetime.now()
        items = soup.find_all(class_="story-list__news")[:self.MAX_ITEMS]

        for i, item in enumerate(items):
            text_div = item.find(class_="story-list__text")
            if not text_div:
                continue
            h = text_div.find(["h2", "h3", "h4"])
            if not h:
                continue
            a = h.find("a")
            if not a:
                continue
            title = a.text.strip()
            href = a.get("href", "").strip()
            if not title or not href:
                continue
            if href.startswith("/"):
                href = self.BASE_URL + href

            published_at = None
            info = item.find(class_="story-list__info")
            if info:
                time_el = info.find("time")
                if time_el:
                    time_text = time_el.text.strip()
                    try:
                        published_at = datetime.fromisoformat(time_text).replace(tzinfo=TAIPEI)
                    except ValueError:
                        pass

            articles.append(Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=title,
                url=self.normalize_url(href),
                published_at=published_at,
                fetched_at=now,
                position=i + 1,
            ))
        return articles
