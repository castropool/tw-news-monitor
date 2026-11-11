from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(slots=True)
class MatchInfo:
    city: str = "tainan"
    relevance: str = "low"
    matched_people: list[str] = field(default_factory=list)
    matched_parties: list[str] = field(default_factory=list)
    matched_issues: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
    matched_basis: list[str] = field(default_factory=list)
    match_rule_id: str = "article_matches"
    region_match: bool = False
    election_context_match: bool = False
    match_score: float = 0.0
    relevance_label: str = ""
    relevance_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedArticle:
    news_article_id: str
    raw_title: str
    normalized_title: str
    raw_url: str
    normalized_url: str
    source_name: str
    normalized_source_name: str
    normalized_domain: str
    category: str = ""
    summary: str = ""
    published_at: str = ""
    collected_at: str = ""
    match: MatchInfo = field(default_factory=MatchInfo)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass(slots=True)
class CandidateEvent:
    candidate_id: str
    election_id: str
    anchor_article_id: str
    cluster_fingerprint: str
    canonical_event_date: str
    event_date_precision: str
    event_date_confidence: str
    candidate_event_type: str
    candidate_title: str
    candidate_summary: str
    primary_actor: str
    secondary_actors_json: str
    locations_json: str
    themes_json: str
    keywords_json: str
    assertion_profile_json: str
    article_count: int
    source_count: int
    completeness_score: float
    cluster_confidence: float
    formal_duplicate_score: float
    formal_duplicate_status: str
    risk_level: str
    review_status: str
    status_reason_codes_json: str
    first_seen_at: str
    last_updated_at: str
    created_run_id: str
    updated_run_id: str
    candidate_schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateArticleLink:
    candidate_id: str
    news_article_id: str
    relationship_type: str
    is_anchor: int
    article_title: str
    article_url: str
    source_name: str
    published_at: str
    event_date_candidate: str
    event_date_basis: str
    match_score: float
    attached_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateAssertion:
    assertion_id: str
    candidate_id: str
    assertion_kind: str
    assertion_text: str
    subject: str
    predicate: str
    object_text: str
    speaker: str
    evidence_article_id: str
    evidence_field: str
    evidence_text: str
    confidence: float
    risk_flags_json: str
    created_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateSource:
    candidate_source_id: str
    normalized_source_name: str
    normalized_domain: str
    original_source_names_json: str
    formal_source_id: str
    formal_match_status: str
    formal_match_basis: str
    first_seen_at: str
    last_seen_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateEventSource:
    candidate_id: str
    candidate_source_id: str
    news_article_id: str
    relationship_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FormalDuplicateSuggestion:
    suggestion_id: str
    candidate_id: str
    formal_event_id: str
    similarity_score: float
    date_score: float
    actor_score: float
    event_type_score: float
    keyword_score: float
    source_overlap_score: float
    matching_reasons_json: str
    conflicting_reasons_json: str
    suggested_action: str
    created_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateValidationResult:
    candidate_id: str
    validation_ready: int
    errors_json: str
    warnings_json: str
    checked_at: str
    validator_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PipelineRun:
    run_id: str
    election_id: str
    started_at: str
    finished_at: str
    status: str
    scan_mode: str
    date_from: str
    date_to: str
    cursor_before: str
    cursor_after: str
    articles_examined: int
    articles_matched: int
    candidate_events_created: int
    candidate_events_updated: int
    articles_attached: int
    auto_reject_count: int
    duplicate_candidate_count: int
    review_required_count: int
    hold_count: int
    pipeline_version: str
    input_hash: str
    business_output_hash: str
    error_summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
