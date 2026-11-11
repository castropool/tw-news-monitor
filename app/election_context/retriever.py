from datetime import datetime, timedelta
from typing import Any
from .repository import ElectionContextRepository

def build_election_context(
    repo: ElectionContextRepository,
    election_id: str,
    query_text: str = '',
    actors: list[str] | None = None,
    issues: list[str] | None = None,
    as_of: str | None = None,
    recent_days: int = 90,
    max_events: int = 20,
) -> dict:
    cutoff = ''
    if as_of:
        cutoff = as_of
    else:
        cutoff = (datetime.now() - timedelta(days=recent_days)).isoformat()

    snapshot = repo.get_latest_snapshot(election_id)
    recent = repo.search_events(
        election_id=election_id, date_from=cutoff,
        min_significance=30, limit=max_events,
    )
    historical = repo.search_events(
        election_id=election_id, keyword=query_text,
        actors=actors, issues=issues, limit=max_events,
    )
    milestones = repo.get_milestone_events(election_id, limit=max_events)

    for e in historical:
        if e['event_id'] in {r['event_id'] for r in recent}:
            continue
        if len(recent) < max_events:
            recent.append(e)

    return {
        'snapshot': snapshot,
        'recent_events': recent[:max_events],
        'milestones': milestones,
        'context_query': {
            'election_id': election_id,
            'query_text': query_text,
            'actors': actors,
            'issues': issues,
            'as_of': as_of,
            'recent_days': recent_days,
        },
    }
