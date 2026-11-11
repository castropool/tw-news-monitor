from datetime import datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

from ..models import Article
from ..time_utils import TAIPEI
from .base import BaseCollector


class ZaobaoCollector(BaseCollector):
    """Collector for Lianhe Zaobao (联合早报) via the official Google News sitemap.

    The official site has no public RSS feed, and its section list pages do
    not expose publish times. ``googlenews.xml`` provides recent stories
    with exact publication timestamps in a single request; ``path_prefix``
    in the source config narrows results to one section (e.g. /news/world/).
    """

    NS = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
    }

    def collect(self) -> list[Article]:
        resp = self.client.get(self.url)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        now = datetime.now()
        articles: list[Article] = []
        seen_urls: set[str] = set()
        path_prefix = (self.source.get("path_prefix") or "").strip()

        for url_el in root.findall("sm:url", self.NS):
            loc = url_el.findtext("sm:loc", default="", namespaces=self.NS).strip()
            title = url_el.findtext(
                "news:news/news:title", default="", namespaces=self.NS
            ).strip()
            date_str = url_el.findtext(
                "news:news/news:publication_date", default="", namespaces=self.NS
            ).strip()
            if not loc or not title or not date_str:
                continue
            path = urlparse(loc).path
            if path_prefix and not path.startswith(path_prefix):
                continue
            try:
                published = datetime.fromisoformat(date_str)
                if published.tzinfo is not None:
                    published = published.astimezone(TAIPEI)
            except ValueError:
                continue
            url = self.normalize_url(loc)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            articles.append(Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=title,
                url=url,
                published_at=published,
                fetched_at=now,
                position=len(articles) + 1,
            ))
            if len(articles) >= self.MAX_ITEMS:
                break
        return articles
