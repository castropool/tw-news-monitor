from __future__ import annotations

from .conftest import make_config
from .test_candidate_router import _candidate, _profile, _scores
from app.election_candidates.candidate_router import route_candidate


def test_direct_event_review(tmp_path):
    config = make_config(tmp_path)
    status, _ = route_candidate(
        _candidate(relevance_label="direct_event", event_date_basis="explicit_in_title"),
        _scores(),
        _profile(),
        config,
    )
    assert status == "review_required"


def test_direct_statement_review(tmp_path):
    config = make_config(tmp_path)
    profile = _profile(has_observed_fact=False, has_actor_statement=True)
    status, _ = route_candidate(
        _candidate(relevance_label="direct_statement", event_date_basis="inferred_from_publication", risk_level="medium"),
        _scores(),
        profile,
        config,
    )
    assert status == "review_required"


def test_contextual_with_statement_context_only(tmp_path):
    config = make_config(tmp_path)
    profile = _profile(has_observed_fact=False, has_actor_statement=True)
    status, _ = route_candidate(
        _candidate(relevance_label="contextual"),
        _scores(),
        profile,
        config,
    )
    assert status == "context_only"


def test_contextual_media_only_hold(tmp_path):
    config = make_config(tmp_path)
    profile = _profile(has_observed_fact=False, has_actor_statement=False, has_media_interpretation=True)
    status, _ = route_candidate(
        _candidate(relevance_label="contextual"),
        _scores(),
        profile,
        config,
    )
    assert status == "hold"


def test_irrelevant_reject(tmp_path):
    config = make_config(tmp_path)
    status, _ = route_candidate(
        _candidate(relevance_label="irrelevant"),
        _scores(),
        _profile(),
        config,
    )
    assert status == "auto_reject"


def test_known_duplicate_routed(tmp_path):
    config = make_config(tmp_path)
    status, _ = route_candidate(
        _candidate(relevance_label="direct_event"),
        _scores(formal_duplicate_score=0.95),
        _profile(),
        config,
    )
    assert status == "duplicate_candidate"


def test_publication_inferred_not_hold(tmp_path):
    config = make_config(tmp_path)
    status, _ = route_candidate(
        _candidate(relevance_label="direct_event", event_date_basis="inferred_from_publication"),
        _scores(),
        _profile(),
        config,
    )
    assert status == "review_required"


def test_status_reasons_complete(tmp_path):
    config = make_config(tmp_path)
    _, reasons = route_candidate(
        _candidate(relevance_label="irrelevant"),
        _scores(),
        _profile(),
        config,
    )
    assert reasons
