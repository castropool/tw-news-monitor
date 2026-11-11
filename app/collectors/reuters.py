"""Reuters collector via the official Google News sitemap (metadata only).

Reuters' official RSS endpoints were retired; the Google News sitemap
(news-sitemap-index -> news-sitemap pages, ~100 entries per page) is the
remaining machine-readable outlet. Entries carry title / canonical URL /
publication date only (no summary/body), so collected Articles are marked
``access_level="metadata_only"`` and ``summary=None`` — the missing body must
never be treated as a failure.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree

from ..models import Article
from ..time_utils import TAIPEI
from .base import BaseCollector

_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}

# Non-English Reuters path prefixes: skip these articles (English only).
_NON_EN_PATH_PREFIXES = (
    "/pt/", "/es/", "/fr/", "/de/", "/it/", "/jp/", "/latam/",
)

# Section derived from the URL path. Order matters: /world/china/ before /world/.
_SECTION_RULES = (
    ("/world/china/", "China"),
    ("/world/", "world"),
    ("/business/", "Business"),
    ("/markets/", "Markets"),
    ("/technology/", "Technology"),
    ("/sports/", "Sports"),
    ("/legal/", "Legal"),
    ("/commentary/", "Commentary"),
)

# Defensive cap on news-sitemap pages fetched per run (each page ~100 entries;
# MAX_ITEMS=20 means a single page normally suffices).
_MAX_PAGES = 5


def _parse_iso8601(value: str) -> datetime | None:
    """Parse ISO8601 into an Asia/Taipei aware datetime.

    Reuters emits ``...Z`` (UTC); naive values are assumed UTC so the result
    is always timezone-aware. Returns None when unparseable.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TAIPEI)


def _section_from_path(path: str) -> str | None:
    for prefix, section in _SECTION_RULES:
        if path.startswith(prefix):
            return section
    return None


class ReutersCollector(BaseCollector):
    """Collect English Reuters news from the official Google News sitemap.

    Flow: fetch the news-sitemap-index from ``source["url"]``, take its
    ``news-sitemap`` page URLs in order, and accumulate ``<url>`` entries
    until MAX_ITEMS (20). No keyword filtering, no body fetching, no breaking
    classification (collector rules).
    """

    def collect(self) -> list[Article]:
        resp = self.get_with_retry(self.url)
        resp.raise_for_status()
        try:
            index_root = ElementTree.fromstring(resp.content)
        except (ElementTree.ParseError, TypeError, ValueError) as exc:
            self.mark_outcome(
                http_status=resp.status_code,
                schema_valid=False,
                item_count=0,
                error_code="parse",
            )
            raise ValueError("Reuters sitemap index schema changed") from exc
        if not index_root.tag.endswith("sitemapindex"):
            self.mark_outcome(
                http_status=resp.status_code,
                schema_valid=False,
                item_count=0,
                error_code="parse",
            )
            raise ValueError("Reuters sitemap index schema changed")
        page_urls = [
            loc.strip()
            for el in index_root.findall("sm:sitemap", _NS)
            for loc in [el.findtext("sm:loc", default="", namespaces=_NS).strip()]
            if loc and "news-sitemap" in loc
        ]

        now = datetime.now(TAIPEI)
        articles: list[Article] = []
        seen_urls: set[str] = set()

        for page_url in page_urls[:_MAX_PAGES]:
            page_resp = self.get_with_retry(page_url)
            page_resp.raise_for_status()
            try:
                page_root = ElementTree.fromstring(page_resp.content)
            except (ElementTree.ParseError, TypeError, ValueError) as exc:
                self.mark_outcome(
                    http_status=page_resp.status_code,
                    schema_valid=False,
                    item_count=0,
                    error_code="parse",
                )
                raise ValueError("Reuters news sitemap schema changed") from exc
            if not page_root.tag.endswith("urlset"):
                self.mark_outcome(
                    http_status=page_resp.status_code,
                    schema_valid=False,
                    item_count=0,
                    error_code="parse",
                )
                raise ValueError("Reuters news sitemap schema changed")
            for url_el in page_root.findall("sm:url", _NS):
                parsed = self._parse_url_entry(url_el)
                if parsed is None:
                    continue
                title, url, published, section = parsed
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
                    summary=None,
                    section=section,
                    language="en",
                    access_level="metadata_only",
                ))
                if len(articles) >= self.MAX_ITEMS:
                    self.mark_outcome(http_status=page_resp.status_code, schema_valid=True, item_count=len(articles))
                    return articles
        self.mark_outcome(http_status=200, schema_valid=True, item_count=len(articles))
        return articles

    def _parse_url_entry(
        self, url_el
    ) -> tuple[str, str, datetime, str | None] | None:
        loc = url_el.findtext("sm:loc", default="", namespaces=_NS).strip()
        title = url_el.findtext(
            "news:news/news:title", default="", namespaces=_NS
        ).strip()
        if not loc or not title:
            return None
        path = urlparse(loc).path.lower()
        if path.startswith(_NON_EN_PATH_PREFIXES):
            return None
        # Placeholder entries: titles ending in "Summary" or containing "OFR".
        if title.endswith("Summary") or "OFR" in title:
            return None
        # publication_date required; fall back to <lastmod> when missing.
        date_str = url_el.findtext(
            "news:news/news:publication_date", default="", namespaces=_NS
        ).strip()
        if not date_str:
            date_str = url_el.findtext(
                "sm:lastmod", default="", namespaces=_NS
            ).strip()
        published = _parse_iso8601(date_str)
        if published is None:
            return None
        section = _section_from_path(path) or self.source.get("section") or None
        return title, self.normalize_url(loc), published, section
