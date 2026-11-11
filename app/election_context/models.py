import json
from datetime import datetime
from typing import Any
from . import validate_fact_status, validate_event_type, validate_actor_type, validate_significance

class Election:
    def __init__(self, election_id: str, election_name: str, election_date: str,
                 region: str, election_type: str, status: str = 'active'):
        self.election_id = election_id
        self.election_name = election_name
        self.election_date = election_date
        self.region = region
        self.election_type = election_type
        self.status = status

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

class Actor:
    def __init__(self, actor_id: str, canonical_name: str, actor_type: str,
                 party: str = '', aliases: list[str] | None = None):
        validate_actor_type(actor_type)
        self.actor_id = actor_id
        self.canonical_name = canonical_name
        self.actor_type = actor_type
        self.party = party
        self.aliases = aliases or []

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not k.startswith('_')}
        d['aliases_json'] = json.dumps(self.aliases, ensure_ascii=False)
        return d

class Source:
    def __init__(self, source_id: str, publisher: str, title: str, url: str,
                 published_at: str | None = None, fetched_at: str | None = None,
                 source_type: str = 'news', evidence_level: str = 'normal',
                 content_hash: str = '', raw_text: str = ''):
        self.source_id = source_id
        self.publisher = publisher
        self.title = title
        self.url = url
        self.published_at = published_at
        self.fetched_at = fetched_at or datetime.now().isoformat()
        self.source_type = source_type
        self.evidence_level = evidence_level
        self.content_hash = content_hash
        self.raw_text = raw_text

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

class ElectionEvent:
    def __init__(self, event_id: str, election_id: str, occurred_at: str,
                 event_type: str, title: str, fact_summary: str = '',
                 fact_status: str = 'pending_verification',
                 significance_score: int = 50,
                 actors: list[str] | None = None,
                 issues: list[str] | None = None,
                 affected_dimensions: list[str] | None = None,
                 analysis: str = ''):
        validate_event_type(event_type)
        validate_fact_status(fact_status)
        validate_significance(significance_score)
        self.event_id = event_id
        self.election_id = election_id
        self.occurred_at = occurred_at
        self.event_type = event_type
        self.title = title
        self.fact_summary = fact_summary
        self.fact_status = fact_status
        self.significance_score = significance_score
        self.actors = actors or []
        self.issues = issues or []
        self.affected_dimensions = affected_dimensions or []
        self.analysis = analysis
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            'event_id': self.event_id, 'election_id': self.election_id,
            'occurred_at': self.occurred_at, 'event_type': self.event_type,
            'title': self.title, 'fact_summary': self.fact_summary,
            'fact_status': self.fact_status,
            'significance_score': self.significance_score,
            'actors_json': json.dumps(self.actors, ensure_ascii=False),
            'issues_json': json.dumps(self.issues, ensure_ascii=False),
            'affected_dimensions_json': json.dumps(self.affected_dimensions, ensure_ascii=False),
            'analysis_json': json.dumps({'analysis': self.analysis}, ensure_ascii=False),
            'created_at': self.created_at, 'updated_at': self.updated_at,
        }

class ElectionStateSnapshot:
    def __init__(self, snapshot_id: str, election_id: str, as_of: str,
                 state_json: dict, supporting_event_ids: list[str]):
        self.snapshot_id = snapshot_id
        self.election_id = election_id
        self.as_of = as_of
        self.state_json = state_json
        self.supporting_event_ids = supporting_event_ids
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            'snapshot_id': self.snapshot_id, 'election_id': self.election_id,
            'as_of': self.as_of,
            'state_json': json.dumps(self.state_json, ensure_ascii=False),
            'supporting_event_ids_json': json.dumps(self.supporting_event_ids, ensure_ascii=False),
            'created_at': self.created_at,
        }
