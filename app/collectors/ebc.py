from datetime import datetime

from bs4 import BeautifulSoup

from ..models import Article
from .base import BaseCollector
from ..time_utils import TAIPEI


class EBCCollector(BaseCollector):
    """Collector for EBC (東森新聞) category pages."""

    BASE_URL = "https://news.ebc.net.tw"

    def collect(self) -> list[Article]:
        resp = self.client.get(self.url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        articles: list[Article] = []
        now = datetime.now()
        items = soup.select("a.item.row_box")[:self.MAX_ITEMS]

        for i, item in enumerate(items):
            title_el = item.find("h3")
            if not title_el:
                continue
            title = title_el.text.strip()
            href = item.get("href", "").strip()
            if not title or not href:
                continue
            if href.startswith("/"):
                href = self.BASE_URL + href

            published_at = None
            time_div = item.find(class_="item_time")
            if time_div:
                time_el = time_div.find("time")
                if time_el and time_el.get("datetime"):
                    try:
                        dt_str = time_el["datetime"]
                        # Handle +08:00 timezone
                        if dt_str.endswith("+08:00"):
                            dt_str = dt_str.replace("+08:00", "")
                        dt = datetime.fromisoformat(dt_str)
                        if dt.tzinfo is not None:
                            published_at = dt.astimezone(TAIPEI)
                        else:
                            published_at = dt.replace(tzinfo=TAIPEI)
                    except (ValueError, IndexError):
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
