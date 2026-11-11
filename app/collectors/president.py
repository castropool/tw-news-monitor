import json
from datetime import datetime, timedelta
import re

from ..models import Article
from .base import BaseCollector
from ..time_utils import TAIPEI

TW_AM_PM_PATTERN = re.compile(
    r"^(\d{4})/(\d{1,2})/(\d{1,2})\s+(上午|下午)\s*(\d{1,2}):(\d{2}):(\d{2})$"
)


def parse_president_time(text: str) -> datetime | None:
    """Parse President Office's time format like '2026/7/16 下午 03:36:00'."""
    if not text:
        return None
    m = TW_AM_PM_PATTERN.match(text.strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    ampm = m.group(4)
    hour = int(m.group(5))
    minute = int(m.group(6))
    second = int(m.group(7))
    if ampm == "下午":
        if hour != 12:
            hour += 12
    else:
        if hour == 12:
            hour = 0
    try:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=TAIPEI)
        return dt
    except ValueError:
        return None


class PresidentCollector(BaseCollector):
    """Collector for Taiwan Presidential Office press releases via JSON API."""

    def collect(self) -> list[Article]:
        resp = self.client.get(self.url)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            items = data.get("Data", data.get("data", data.get("items", [])))
        else:
            items = data
        articles: list[Article] = []
        now = datetime.now(TAIPEI)
        for i, item in enumerate(items[: self.MAX_ITEMS]):
            title = (item.get("Title") or "").strip()
            url = (item.get("URL") or "").strip()
            pub_text = (item.get("PublishDate") or "").strip()
            published_at = parse_president_time(pub_text)
            if not title or not url:
                continue
            articles.append(
                Article(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    category=self.category,
                    title=title,
                    url=self.normalize_url(url),
                    published_at=published_at,
                    fetched_at=now,
                    position=i + 1,
                )
            )
        return articles
