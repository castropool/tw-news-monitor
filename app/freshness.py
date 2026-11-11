from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Article

TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass
class FreshnessResult:
    fresh_articles: list[Article] = field(default_factory=list)
    catch_up_articles: list[Article] = field(default_factory=list)
    date_only_today_articles: list[Article] = field(default_factory=list)
    stale_articles: list[Article] = field(default_factory=list)
    unknown_time_articles: list[Article] = field(default_factory=list)
    future_time_articles: list[Article] = field(default_factory=list)

    def count(self) -> int:
        return len(self.fresh_articles)


def filter_fresh_articles(
    articles: list[Article],
    run_started_at: datetime,
    freshness_minutes: int = 90,
    future_tolerance_minutes: int = 10,
    catch_up_enabled: bool = False,
    catch_up_max_minutes: int = 720,
) -> FreshnessResult:
    """Filter articles by publish time freshness.

    Articles are classified into four categories based on their
    ``published_at`` relative to ``run_started_at``.

    Returns a FreshnessResult with the classified articles.

    * `fresh` -- within freshness_minutes window (default 90 min)
    * `catch_up` -- between freshness window and catch_up_max_minutes (default 12h),
      only when `catch_up_enabled=True`
    * `stale` -- older than catch_up_max_minutes (or older than freshness_minutes
      when catch_up is disabled)
    * `unknown_time` -- missing or naive timestamp
    * `future_time` -- beyond future_tolerance_minutes (default 10 min)
    """
    result = FreshnessResult()

    if catch_up_enabled and catch_up_max_minutes <= freshness_minutes:
        raise ValueError(
            f"catch_up_max_minutes ({catch_up_max_minutes}) must be greater than "
            f"freshness_minutes ({freshness_minutes})"
        )

    if run_started_at.tzinfo is None:
        run_started_at = run_started_at.replace(tzinfo=TAIPEI)
    min_time = run_started_at - timedelta(minutes=freshness_minutes)
    max_time = run_started_at + timedelta(minutes=future_tolerance_minutes)
    catch_up_min_time = run_started_at - timedelta(minutes=catch_up_max_minutes) if catch_up_enabled else min_time

    for article in articles:
        pub = article.published_at
        precision = getattr(article, "published_at_precision", "exact") or "exact"
        if pub is None or precision == "unknown":
            result.unknown_time_articles.append(article)
            continue

        if precision == "date_only":
            # date-only articles carry no fake time.  If the date is today they
            # are delivery candidates; older dates are stale and never delivered.
            if pub.tzinfo is None:
                pub_taipei = pub.replace(tzinfo=TAIPEI)
            else:
                pub_taipei = pub.astimezone(TAIPEI)
            run_date = run_started_at.date()
            if pub_taipei.date() == run_date:
                result.date_only_today_articles.append(article)
            elif pub_taipei.date() < run_date:
                result.stale_articles.append(article)
            else:
                result.future_time_articles.append(article)
            continue

        # exact precision: unknown/naive exact timestamps remain unknown.
        if pub.tzinfo is None:
            result.unknown_time_articles.append(article)
            continue

        pub_taipei = pub.astimezone(TAIPEI)
        if pub_taipei > max_time:
            result.future_time_articles.append(article)
        elif pub_taipei < min_time:
            if catch_up_enabled and pub_taipei >= catch_up_min_time:
                result.catch_up_articles.append(article)
            else:
                result.stale_articles.append(article)
        else:
            result.fresh_articles.append(article)

    return result
