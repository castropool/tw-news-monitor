"""Shared result objects for collection and delivery stages."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CollectionResult:
    fetched_articles: list = field(default_factory=list)
    inserted_articles: list = field(default_factory=list)
    promoted_articles: list = field(default_factory=list)
    promoted_delivery_articles: list = field(default_factory=list)
    filtered_before_save: list = field(default_factory=list)
    filtered_from_delivery: list = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    run_url_duplicates: int = 0
    run_identity_duplicates: int = 0
    historical_url_duplicates: int = 0
    historical_identity_duplicates: int = 0
    election_annotations: dict = field(default_factory=dict)

    @property
    def total_fetched(self) -> int:
        return len(self.fetched_articles)

    @property
    def run_removed(self) -> int:
        return self.run_url_duplicates + self.run_identity_duplicates

    @property
    def filtered_count(self) -> int:
        return len(self.filtered_before_save) + len(self.filtered_from_delivery)

    @property
    def duplicate_count(self) -> int:
        return (
            self.run_removed
            + self.historical_url_duplicates
            + self.historical_identity_duplicates
        )

    def __iter__(self):
        """Compatibility view for older callers expecting the legacy tuple."""
        return iter(self._legacy_values())

    def _legacy_values(self) -> tuple:
        return (
            self.inserted_articles,
            self.total_fetched,
            self.duplicate_count,
            self.failed_sources,
            self.run_removed,
            self.historical_identity_duplicates,
            self.filtered_count,
        )

    def __getitem__(self, index):
        return self._legacy_values()[index]

    def __len__(self) -> int:
        return 7


@dataclass(slots=True)
class DeliveryResult:
    delivery_articles: list = field(default_factory=list)
    digest_articles: list = field(default_factory=list)
    fresh_articles: list = field(default_factory=list)
    catch_up_articles: list = field(default_factory=list)
    date_only_today_articles: list = field(default_factory=list)
    date_only_baseline_excluded: list = field(default_factory=list)
    stale_articles: list = field(default_factory=list)
    unknown_time_articles: list = field(default_factory=list)
    future_articles: list = field(default_factory=list)
    baseline_excluded: list = field(default_factory=list)
    catch_up_urls: set[str] = field(default_factory=set)
    international_coverage: dict = field(default_factory=dict)
    importance_results: list = field(default_factory=list)
    pre_cap_importance_summary: str = ""
