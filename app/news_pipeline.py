"""Shared delivery-stage orchestration for production and dry-run.

The collectors and persistence layer are intentionally separate from this
module. This module owns the common, side-effect-light path that turns newly
inserted articles into the article set used by Word/notifications. The
caller supplies the summary callback so production and dry-run can use their
own database dependencies without duplicating the business sequence.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from .freshness import filter_fresh_articles
from .importance import classify_articles, finalize_importance, importance_summary
from .pipeline_models import DeliveryResult


def run_delivery_core(
    inserted_articles: Iterable,
    source_baselines: dict[str, int],
    run_started_at: datetime,
    *,
    international_config: dict | None,
    importance_rules_config: dict,
    prepare_international_delivery: Callable,
    enrich_summaries: Callable[[list], None],
    excluded_delivery_urls: Iterable[str] = (),
    source_continuity: dict[str, bool] | None = None,
    catch_up_enabled: bool = False,
    catch_up_max_minutes: int = 720,
) -> DeliveryResult:
    """Run the common freshness/enrichment/importance delivery path.

    ``source_continuity`` distinguishes a continuously running source from a
    source that just recovered after a long outage.  Date-only articles are
    only delivery candidates for continuous sources; exact articles continue
    to use the normal freshness/catch-up rules regardless.
    """
    continuity = source_continuity or {}

    excluded_urls = set(excluded_delivery_urls)
    freshness = filter_fresh_articles(
        list(inserted_articles),
        run_started_at,
        catch_up_enabled=catch_up_enabled,
        catch_up_max_minutes=catch_up_max_minutes,
    )

    fresh_articles = [
        article for article in freshness.fresh_articles
        if article.url not in excluded_urls
    ]
    catch_up_articles = [
        article
        for article in freshness.catch_up_articles
        if source_baselines.get(article.source_id, 0) > 0
        and article.url not in excluded_urls
    ]
    baseline_excluded = [
        article
        for article in freshness.catch_up_articles
        if source_baselines.get(article.source_id, 0) == 0
    ]
    date_only_today_articles = [
        article for article in freshness.date_only_today_articles
        if article.url not in excluded_urls
    ]
    date_only_eligible = [
        article for article in date_only_today_articles
        if source_baselines.get(article.source_id, 0) > 0
        and bool(continuity.get(article.source_id, False))
    ]
    date_only_baseline_excluded = [
        article for article in date_only_today_articles
        if source_baselines.get(article.source_id, 0) == 0
        or not bool(continuity.get(article.source_id, False))
    ]
    stale_articles = (
        list(freshness.stale_articles)
        + baseline_excluded
        + date_only_baseline_excluded
    )
    catch_up_urls = {article.url for article in catch_up_articles}
    delivery_articles = fresh_articles + catch_up_articles + date_only_eligible

    digest_articles, international_coverage = prepare_international_delivery(
        delivery_articles, international_config
    )
    enrich_summaries(digest_articles)
    importance_results = classify_articles(
        digest_articles,
        importance_rules_config,
        international_config=international_config,
    )
    pre_cap_importance_summary = importance_summary(importance_results)
    importance_results = finalize_importance(
        importance_results, importance_rules_config
    )

    return DeliveryResult(
        delivery_articles=delivery_articles,
        digest_articles=digest_articles,
        fresh_articles=fresh_articles,
        catch_up_articles=catch_up_articles,
        date_only_today_articles=date_only_today_articles,
        date_only_baseline_excluded=date_only_baseline_excluded,
        stale_articles=stale_articles,
        unknown_time_articles=list(freshness.unknown_time_articles),
        future_articles=list(freshness.future_time_articles),
        baseline_excluded=baseline_excluded,
        catch_up_urls=catch_up_urls,
        international_coverage=international_coverage,
        importance_results=importance_results,
        pre_cap_importance_summary=pre_cap_importance_summary,
    )
