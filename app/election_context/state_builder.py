import json
from datetime import datetime, timezone, timedelta
from typing import Any
from .repository import ElectionContextRepository

TAIPEI = timezone(timedelta(hours=8))

def build_snapshot(repo: ElectionContextRepository, election_id: str) -> dict:
    events = repo.search_events(
        election_id=election_id, min_significance=50, limit=100
    )
    candidates = {}
    latest_polls = []
    key_issues = set()
    faction_dynamics = []

    for e in events:
        actors = json.loads(e.get('actors_json', '[]')) if isinstance(e.get('actors_json'), str) else e.get('actors', [])
        issues = json.loads(e.get('issues_json', '[]')) if isinstance(e.get('issues_json'), str) else e.get('issues', [])
        for a in actors:
            candidates.setdefault(a, {'events': [], 'issues': set()})
            candidates[a]['events'].append(e['event_id'])
            candidates[a]['issues'].update(issues)
        for i in issues:
            key_issues.add(i)
        if e.get('event_type') == 'poll_release':
            latest_polls.append(e)

    state = {
        'election_id': election_id,
        'as_of': datetime.now(TAIPEI).isoformat(),
        'candidates': {k: {'event_count': len(v['events']), 'issues': list(v['issues'])}
                       for k, v in candidates.items()},
        'key_issues': list(key_issues),
        'poll_count': len(latest_polls),
        'total_high_impact_events': len(events),
    }

    snapshot_id = f'snap_{election_id}_{datetime.now(TAIPEI).strftime("%Y%m%d_%H%M%S")}'
    supporting = [e['event_id'] for e in events if e.get('significance_score', 0) >= 60]

    snapshot = {
        'snapshot_id': snapshot_id,
        'election_id': election_id,
        'as_of': state['as_of'],
        'state_json': state,
        'supporting_event_ids': supporting,
        'created_at': datetime.now(TAIPEI).isoformat(),
    }
    repo.save_snapshot(snapshot)
    return snapshot
