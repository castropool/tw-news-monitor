from abc import ABC, abstractmethod
import email.utils
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from ..models import Article
from ..source_health import SourceOutcome


TRACKING_PARAMS = frozenset({
    "from",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "fbclid",
    "gclid",
})


class BaseCollector(ABC):
    """Base class for news collectors."""

    MAX_ITEMS = 20
    CONNECT_TIMEOUT = 5.0
    READ_TIMEOUT = 15.0
    WRITE_TIMEOUT = 15.0
    POOL_TIMEOUT = 5.0
    # Compatibility alias for older collectors/tests that override TIMEOUT.
    TIMEOUT = READ_TIMEOUT
    MAX_RETRIES = 2
    BACKOFF_SECONDS = 0.25
    MAX_RETRY_AFTER = 5.0
    MAX_RESPONSE_BYTES = 4 * 1024 * 1024
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, source: dict):
        self.source = source
        self._client: httpx.Client | None = None
        self.last_outcome: SourceOutcome | None = None
        self.http_calls = 0

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(
                    connect=self.CONNECT_TIMEOUT,
                    read=self.READ_TIMEOUT,
                    write=self.WRITE_TIMEOUT,
                    pool=self.POOL_TIMEOUT,
                ),
                headers={"User-Agent": self.USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    def get_with_retry(self, url: str) -> httpx.Response:
        """GET with finite retry, bounded Retry-After and response size.

        Collectors intentionally call this helper instead of opening an
        article page.  Tests can inject a tiny fake client exposing ``get``.
        """
        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            self.http_calls += 1
            try:
                response = self.client.get(url)
                self._check_response_size(response)
                status = int(getattr(response, "status_code", 0) or 0)
                if status in {408, 425, 429, 500, 502, 503, 504} and attempt < self.MAX_RETRIES:
                    self._sleep_before_retry(response, attempt)
                    continue
                self.last_outcome = SourceOutcome(
                    status,
                    False,
                    0,
                    "http" if not 200 <= status < 300 else None,
                )
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, OSError, ValueError) as exc:
                last_error = exc
                if attempt >= self.MAX_RETRIES:
                    break
                self._sleep_before_retry(None, attempt)
        if last_error is not None:
            error_code = "timeout" if isinstance(last_error, httpx.TimeoutException) else "http"
            self.last_outcome = SourceOutcome(0, False, 0, error_code)
            raise last_error
        raise RuntimeError("collector request failed without response")

    def _check_response_size(self, response: httpx.Response) -> None:
        header = response.headers.get("content-length") if hasattr(response, "headers") else None
        try:
            if header is not None and int(header) > self.MAX_RESPONSE_BYTES:
                raise ValueError("response exceeds maximum size")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc) == "response exceeds maximum size":
                raise
            # Invalid Content-Length is a malformed response, not permission
            # to disable the size guard; actual content is checked below.
        content = getattr(response, "content", b"")
        if isinstance(content, str):
            content = content.encode()
        if len(content) > self.MAX_RESPONSE_BYTES:
            raise ValueError("response exceeds maximum size")

    def _sleep_before_retry(self, response: httpx.Response | None, attempt: int) -> None:
        delay = min(self.BACKOFF_SECONDS * (2 ** attempt), self.MAX_RETRY_AFTER)
        if response is not None:
            retry_after = response.headers.get("retry-after")
            parsed = _parse_retry_after(retry_after)
            if parsed is not None:
                delay = min(parsed, self.MAX_RETRY_AFTER)
        if delay > 0:
            time.sleep(delay)

    def mark_outcome(
        self,
        *,
        http_status: int,
        schema_valid: bool,
        item_count: int,
        error_code: str | None = None,
    ) -> None:
        self.last_outcome = SourceOutcome(
            http_status, schema_valid, item_count, error_code
        )

    @abstractmethod
    def collect(self) -> list[Article]:
        """Collect articles from the source. Returns at most MAX_ITEMS."""

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize URL for deduplication.

        Strips tracking parameters (from, utm_*, fbclid, gclid),
        removes fragments, lowercases scheme and host, preserves the
        case-sensitive path, removes
        trailing slashes, and sorts remaining query parameters
        for stable display URLs.  Cross-URL identity aliases are handled
        separately by ``article_identity_key``.
        """
        parsed = urlparse(url.strip())
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
        )
        path = normalized.path.rstrip("/")
        normalized = normalized._replace(path=path)
        # Strip tracking parameters from query string
        if normalized.query:
            params = parse_qs(normalized.query, keep_blank_values=True)
            filtered = {
                k: v
                for k, v in params.items()
                if k.lower() not in TRACKING_PARAMS
            }
            if filtered:
                sorted_query = urlencode(sorted(filtered.items()), doseq=True)
                normalized = normalized._replace(query=sorted_query)
            else:
                normalized = normalized._replace(query="")
        return urlunparse(normalized)

    @property
    def source_id(self) -> str:
        return self.source["id"]

    @property
    def source_name(self) -> str:
        return self.source["name"]

    @property
    def category(self) -> str:
        return (
            self.source.get("category")
            or self.source.get("default_category")
            or "politics"
        )

    @property
    def url(self) -> str:
        return self.source["url"]


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
        return max(0.0, seconds)
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None
