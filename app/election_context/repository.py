import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from . import validate_fact_status, validate_significance


def _json_dumps(v):
    if v is None:
        return ""
    if isinstance(v, str):
        try:
            json.loads(v)
            return v
        except json.JSONDecodeError:
            return json.dumps(v, ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)

DB_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'election_context.db'

CREATE_ELECTIONS = '''
CREATE TABLE IF NOT EXISTS elections (
    election_id TEXT PRIMARY KEY,
    election_name TEXT NOT NULL,
    election_date TEXT,
    region TEXT,
    election_type TEXT,
    status TEXT DEFAULT 'active'
)'''

CREATE_ACTORS = '''
CREATE TABLE IF NOT EXISTS actors (
    actor_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    party TEXT,
    aliases_json TEXT
)'''

CREATE_SOURCES = '''
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    publisher TEXT,
    title TEXT,
    url TEXT UNIQUE NOT NULL,
    published_at TEXT,
    fetched_at TEXT,
    source_type TEXT DEFAULT 'news',
    evidence_level TEXT DEFAULT 'normal',
    content_hash TEXT,
    raw_text TEXT,
    updated_at TEXT
)'''

CREATE_EVENTS = '''
CREATE TABLE IF NOT EXISTS election_events (
    event_id TEXT PRIMARY KEY,
    election_id TEXT NOT NULL,
    occurred_at TEXT,
    event_type TEXT NOT NULL,
    title TEXT,
    fact_summary TEXT,
    fact_status TEXT DEFAULT 'pending_verification',
    significance_score INTEGER DEFAULT 50,
    actors_json TEXT,
    issues_json TEXT,
    affected_dimensions_json TEXT,
    analysis_json TEXT,
    created_at TEXT,
    updated_at TEXT
)'''

CREATE_EVENT_SOURCES = '''
CREATE TABLE IF NOT EXISTS event_sources (
    event_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    is_primary INTEGER DEFAULT 0,
    PRIMARY KEY (event_id, source_id)
)'''

CREATE_SNAPSHOTS = '''
CREATE TABLE IF NOT EXISTS election_state_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    election_id TEXT NOT NULL,
    as_of TEXT,
    state_json TEXT,
    supporting_event_ids_json TEXT,
    created_at TEXT,
    snapshot_status TEXT DEFAULT 'active',
    superseded_by TEXT,
    superseded_at TEXT
)'''

CREATE_FTS = '''
CREATE VIRTUAL TABLE IF NOT EXISTS election_events_fts USING fts5(
    title, fact_summary, actors, issues, tokenize='unicode61'
)'''

CREATE_POLLS = '''
CREATE TABLE IF NOT EXISTS election_polls (
    poll_id TEXT PRIMARY KEY,
    election_id TEXT NOT NULL,
    poll_type TEXT,
    fact_status TEXT,
    methodology_complete INTEGER DEFAULT 0,
    verification_tier TEXT,
    recommended_disposition TEXT,
    canonical_origin TEXT,
    publication_json TEXT,
    fieldwork_json TEXT,
    methodology_json TEXT,
    population_json TEXT,
    limitations_json TEXT,
    usable_for_poll_trend INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
)'''

CREATE_POLL_QUESTIONS = '''
CREATE TABLE IF NOT EXISTS poll_questions (
    poll_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question_type TEXT,
    candidate_set_json TEXT,
    base_population TEXT,
    population_filter TEXT,
    trend_eligible INTEGER DEFAULT 0,
    trend_scope TEXT,
    comparable_group_key TEXT,
    note TEXT,
    question_order INTEGER DEFAULT 0,
    PRIMARY KEY (poll_id, question_id)
)'''

CREATE_POLL_RESULTS = '''
CREATE TABLE IF NOT EXISTS poll_results (
    poll_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    option_id TEXT NOT NULL,
    option_name TEXT,
    option_type TEXT,
    reported_value TEXT,
    value REAL,
    normalized_value REAL,
    unit TEXT DEFAULT 'percent',
    base_population TEXT,
    is_derived INTEGER DEFAULT 0,
    result_order INTEGER DEFAULT 0,
    PRIMARY KEY (poll_id, question_id, option_id)
)'''

CREATE_POLL_LINKS = '''
CREATE TABLE IF NOT EXISTS poll_source_links (
    poll_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (poll_id, source_id)
)'''

POLLS_EID_IDX = 'CREATE INDEX IF NOT EXISTS idx_polls_election ON election_polls(election_id)'
POLLS_ORIGIN_IDX = 'CREATE INDEX IF NOT EXISTS idx_polls_origin ON election_polls(canonical_origin)'
PQ_POLL_IDX = 'CREATE INDEX IF NOT EXISTS idx_pq_poll ON poll_questions(poll_id)'
PQ_GROUP_IDX = 'CREATE INDEX IF NOT EXISTS idx_pq_group ON poll_questions(comparable_group_key)'
PQ_TREND_IDX = 'CREATE INDEX IF NOT EXISTS idx_pq_trend ON poll_questions(trend_eligible)'
PR_PQ_IDX = 'CREATE INDEX IF NOT EXISTS idx_pr_poll_q ON poll_results(poll_id, question_id)'
PS_SRC_IDX = 'CREATE INDEX IF NOT EXISTS idx_ps_source ON poll_source_links(source_id)'

FTS_TRIGGERS = []

class ElectionContextRepository:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.row_factory = sqlite3.Row

    def create_tables(self):
        for ddl in [CREATE_ELECTIONS, CREATE_ACTORS, CREATE_SOURCES,
                     CREATE_EVENTS, CREATE_EVENT_SOURCES, CREATE_SNAPSHOTS]:
            self.conn.execute(ddl)
        # Migrate: add columns for existing databases
        existing_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(election_state_snapshots)").fetchall()}
        if 'snapshot_status' not in existing_cols:
            self.conn.execute("ALTER TABLE election_state_snapshots ADD COLUMN snapshot_status TEXT DEFAULT 'active'")
        if 'superseded_by' not in existing_cols:
            self.conn.execute("ALTER TABLE election_state_snapshots ADD COLUMN superseded_by TEXT")
        if 'superseded_at' not in existing_cols:
            self.conn.execute("ALTER TABLE election_state_snapshots ADD COLUMN superseded_at TEXT")
        # Create poll tables
        for ddl_poll in [CREATE_POLLS, CREATE_POLL_QUESTIONS, CREATE_POLL_RESULTS, CREATE_POLL_LINKS]:
            try: self.conn.execute(ddl_poll)
            except: pass
        for idx in [POLLS_EID_IDX, POLLS_ORIGIN_IDX, PQ_POLL_IDX, PQ_GROUP_IDX, PQ_TREND_IDX, PR_PQ_IDX, PS_SRC_IDX]:
            try: self.conn.execute(idx)
            except: pass
        try:
            self.conn.execute('DROP TABLE IF EXISTS election_events_fts')
        except Exception:
            pass
        self.conn.execute(CREATE_FTS)
        self.conn.commit()
        events = self.conn.execute('SELECT rowid, title, fact_summary, actors_json, issues_json FROM election_events').fetchall()
        for e in events:
            try:
                self.conn.execute(
                    'INSERT INTO election_events_fts(rowid, title, fact_summary, actors, issues) VALUES (?,?,?,?,?)',
                    (e['rowid'], e['title'], e['fact_summary'], e['actors_json'], e['issues_json'])
                )
            except Exception:
                pass
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def save_election(self, election: dict):
        self.conn.execute('''INSERT OR REPLACE INTO elections
            (election_id, election_name, election_date, region, election_type, status)
            VALUES (?,?,?,?,?,?)''',
            (election['election_id'], election['election_name'], election['election_date'],
             election['region'], election['election_type'], election.get('status', 'active')))
        self.conn.commit()

    def save_actor(self, actor: dict):
        self.conn.execute('''INSERT OR REPLACE INTO actors
            (actor_id, canonical_name, actor_type, party, aliases_json)
            VALUES (?,?,?,?,?)''',
            (actor['actor_id'], actor['canonical_name'], actor['actor_type'],
             actor.get('party', ''), actor.get('aliases_json', '[]')))
        self.conn.commit()

    def save_source(self, source: dict) -> str:
        sid = source.get('source_id', '')
        existing = self.conn.execute(
            'SELECT source_id FROM sources WHERE url=?', (source['url'],)
        ).fetchone()
        if existing:
            sid = existing['source_id']
            self.conn.execute('''UPDATE sources SET updated_at=? WHERE source_id=?''',
                (datetime.now().isoformat(), sid))
            self.conn.commit()
            return sid
        if not sid:
            import hashlib
            sid = 'src_' + hashlib.sha256(source['url'].encode()).hexdigest()[:16]
        self.conn.execute('''INSERT OR IGNORE INTO sources
            (source_id, publisher, title, url, published_at, fetched_at, source_type, evidence_level, content_hash, raw_text)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (sid, source.get('publisher', ''), source.get('title', ''), source['url'],
             source.get('published_at'), source.get('fetched_at'),
             source.get('source_type', 'news'), source.get('evidence_level', 'normal'),
             source.get('content_hash', ''), source.get('raw_text', '')))
        self.conn.commit()
        return sid

    def save_event(self, event: dict) -> str:
        eid = event.get('event_id', '')
        existing = self.conn.execute(
            'SELECT event_id FROM election_events WHERE event_id=?', (eid,)
        ).fetchone()
        if existing:
            self.conn.execute('''UPDATE election_events SET updated_at=? WHERE event_id=?''',
                (datetime.now().isoformat(), eid))
            self.conn.commit()
            return eid
        validate_fact_status(event.get('fact_status', 'pending_verification'))
        validate_significance(int(event.get('significance_score', 50)))
        raw_actors = event.get('actors_json')
        if raw_actors is None:
            raw_actors = event.get('actors', [])
        actors_json = raw_actors if isinstance(raw_actors, str) else json.dumps(raw_actors, ensure_ascii=False)
        raw_issues = event.get('issues_json')
        if raw_issues is None:
            raw_issues = event.get('issues', [])
        issues_json = raw_issues if isinstance(raw_issues, str) else json.dumps(raw_issues, ensure_ascii=False)
        raw_dims = event.get('affected_dimensions_json')
        if raw_dims is None:
            raw_dims = event.get('affected_dimensions', [])
        dims_json = raw_dims if isinstance(raw_dims, str) else json.dumps(raw_dims, ensure_ascii=False)
        raw_analysis = event.get('analysis_json')
        if raw_analysis is None:
            raw_analysis = json.dumps({'analysis': event.get('analysis', '')}, ensure_ascii=False)
        analysis_json = raw_analysis if isinstance(raw_analysis, str) else json.dumps(raw_analysis, ensure_ascii=False)
        now = datetime.now().isoformat()
        self.conn.execute('''INSERT OR IGNORE INTO election_events
            (event_id, election_id, occurred_at, event_type, title, fact_summary,
             fact_status, significance_score, actors_json, issues_json,
             affected_dimensions_json, analysis_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (eid, event['election_id'], event.get('occurred_at', ''),
             event['event_type'], event.get('title', ''), event.get('fact_summary', ''),
             event.get('fact_status', 'pending_verification'),
             int(event.get('significance_score', 50)),
             actors_json, issues_json, dims_json, analysis_json, now, now))
        self.conn.commit()
        return eid

    def save_poll(self, poll: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO election_polls
               (poll_id, election_id, poll_type, fact_status, methodology_complete,
                verification_tier, recommended_disposition, canonical_origin,
                publication_json, fieldwork_json, methodology_json, population_json,
                limitations_json, usable_for_poll_trend, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                poll["poll_id"],
                poll.get("election_id", ""),
                poll.get("poll_type", ""),
                poll.get("fact_status", "poll_result"),
                int(bool(poll.get("methodology_complete", 0))),
                poll.get("verification_tier", ""),
                poll.get("recommended_disposition", ""),
                poll.get("canonical_origin", ""),
                _json_dumps(poll.get("publication_json")),
                _json_dumps(poll.get("fieldwork_json")),
                _json_dumps(poll.get("methodology_json")),
                _json_dumps(poll.get("population_json")),
                _json_dumps(poll.get("limitations_json")),
                int(bool(poll.get("usable_for_poll_trend", 0))),
                poll.get("created_at", ""),
                poll.get("updated_at", ""),
            ),
        )
        self.conn.commit()

    def rebuild_fts(self):
        self.conn.execute("DROP TABLE IF EXISTS election_events_fts")
        self.conn.execute(CREATE_FTS)
        events = self.conn.execute(
            "SELECT rowid, title, fact_summary, actors_json, issues_json FROM election_events"
        ).fetchall()
        for e in events:
            try:
                self.conn.execute(
                    "INSERT INTO election_events_fts(rowid, title, fact_summary, actors, issues) "
                    "VALUES (?,?,?,?,?)",
                    (e["rowid"], e["title"], e["fact_summary"], e["actors_json"], e["issues_json"]),
                )
            except Exception:
                pass
        self.conn.commit()

    def save_poll_question(self, q: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO poll_questions
               (poll_id, question_id, question_type, candidate_set_json, base_population,
                population_filter, trend_eligible, trend_scope, comparable_group_key,
                note, question_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                q["poll_id"],
                q["question_id"],
                q.get("question_type", ""),
                _json_dumps(q.get("candidate_set_json")),
                q.get("base_population", ""),
                q.get("population_filter", ""),
                int(bool(q.get("trend_eligible", 0))),
                q.get("trend_scope", ""),
                q.get("comparable_group_key", ""),
                q.get("note", ""),
                int(q.get("question_order", 0)),
            ),
        )
        self.conn.commit()

    def save_poll_result(self, r: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO poll_results
               (poll_id, question_id, option_id, option_name, option_type,
                reported_value, value, normalized_value, unit, base_population,
                is_derived, result_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["poll_id"],
                r["question_id"],
                r["option_id"],
                r.get("option_name", ""),
                r.get("option_type", ""),
                r.get("reported_value", ""),
                r.get("value"),
                r.get("normalized_value"),
                r.get("unit", "percent"),
                r.get("base_population", ""),
                int(bool(r.get("is_derived", 0))),
                int(r.get("result_order", 0)),
            ),
        )
        self.conn.commit()

    def link_poll_source(self, poll_id: str, source_id: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO poll_source_links (poll_id, source_id) VALUES (?,?)",
            (poll_id, source_id),
        )
        self.conn.commit()

    def link_event_source(self, event_id: str, source_id: str, is_primary: bool = False):
        self.conn.execute('''INSERT OR IGNORE INTO event_sources
            (event_id, source_id, is_primary) VALUES (?,?,?)''',
            (event_id, source_id, 1 if is_primary else 0))
        self.conn.commit()

    def get_event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            'SELECT * FROM election_events WHERE event_id=?', (event_id,)
        ).fetchone()
        if not row:
            return None
        return self._annotate_event(self._row_to_event(row))

    def _row_to_event(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in ['actors_json', 'issues_json', 'affected_dimensions_json']:
            if d.get(k):
                d[k.replace('_json', '')] = json.loads(d[k])
        if d.get('analysis_json'):
            d['analysis'] = json.loads(d['analysis_json']).get('analysis', '')
        if 'event_id' in d:
            cnt = self.conn.execute(
                'SELECT COUNT(*) FROM event_sources WHERE event_id=?', (d['event_id'],)
            ).fetchone()[0]
            d['source_count'] = cnt
        return d

    def _tokenize_keywords(self, raw: str) -> list[str]:
        import re
        tokens = re.split(r'[\s,，、。；;：:（）()！!？?/\\\'\"-]+', raw)
        seen = set()
        result = []
        for t in tokens:
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def _calc_relevance(self, event: dict, tokens: list[str]) -> tuple:
        import json as _json
        SCORE_TITLE_EXACT_QUERY = 100
        SCORE_TITLE_ALL_KEYWORDS = 80
        SCORE_ACTOR_EXACT_MATCH = 35
        SCORE_EVENT_TYPE_MATCH = 25
        SCORE_FACT_SUMMARY_HIT = 15
        SCORE_ISSUES_HIT = 12
        SCORE_ACTORS_JSON_HIT = 12
        SCORE_ANALYSIS_HIT = 3
        SCORE_MIN_THRESHOLD = 10

        score = 0
        ranking_reasons = []
        matched_fields = {}
        matched_kw_count = 0

        title = event.get('title', '') or ''
        fact_summary = event.get('fact_summary', '') or ''
        actors_raw = event.get('actors_json', '[]') or '[]'
        issues_raw = event.get('issues_json', '[]') or '[]'
        analysis_raw = event.get('analysis_json', '{}') or '{}'
        dims_raw = event.get('affected_dimensions_json', '[]') or '[]'

        def _pj(v):
            if isinstance(v, str):
                try: return _json.loads(v)
                except: return []
            return v if isinstance(v, list) else []

        actors_list = _pj(actors_raw)
        issues_list = _pj(issues_raw)
        actors_text = ' '.join(actors_list)
        issues_text = ' '.join(issues_list)

        for token in tokens:
            token_match_fields = []
            if token in title:
                token_match_fields.append('title')
            if token in fact_summary:
                token_match_fields.append('fact_summary')
            if token in actors_text:
                token_match_fields.append('actors_json')
            if token in issues_text:
                token_match_fields.append('issues_json')
            if token in dims_raw:
                token_match_fields.append('affected_dimensions_json')
            if token in analysis_raw:
                token_match_fields.append('analysis_json')
            if token_match_fields:
                matched_kw_count += 1
                matched_fields[token] = token_match_fields

        all_matched = matched_kw_count >= len(tokens)
        half_matched = matched_kw_count >= max(1, len(tokens) // 2 + 1)

        if all_matched:
            match_mode = 'strict'
        elif half_matched:
            person_in_query = [t for t in tokens if t in actors_text or t in title]
            matched_actors = any(t in actors_text for t in person_in_query) if person_in_query else True
            if matched_actors:
                match_mode = 'relaxed'
            else:
                return 0, 'irrelevant', [], matched_fields
        else:
            return 0, 'irrelevant', [], matched_fields

        query_text = ' '.join(tokens)
        if query_text in title:
            score += SCORE_TITLE_EXACT_QUERY
            ranking_reasons.append('标题精确匹配')
        elif all(t in title for t in tokens):
            score += SCORE_TITLE_ALL_KEYWORDS
            ranking_reasons.append('标题包含全部关键词')

        for t in tokens:
            if t in actors_text:
                score += SCORE_ACTOR_EXACT_MATCH
                ranking_reasons.append('人物匹配')
                # Primary: actor is in actors_list AND title starts with that specific actor AND token matches
                primary_actor = None
                for a in actors_list:
                    if title.startswith(a) and t in a:
                        primary_actor = a
                        break
                if primary_actor:
                    score += 35
                    ranking_reasons.append('主要人物事件')
                else:
                    score += 20
                    ranking_reasons.append('关联人物事件')
                break

        evt_type = event.get('event_type', '')
        type_keywords = {
            'primary_procedure': ['初选', '程序'],
            'primary_debate': ['初选', '辩论', '政见'],
            'primary_result': ['初选', '胜出'],
            'party_nomination': ['提名', '征召'],
            'campaign_attack': ['攻击'],
            'faction_conflict': ['整合', '派系'],
            'alliance_proposal': ['合作', '联盟'],
        }
        matching_kw = type_keywords.get(evt_type, [])
        if any(t in query_text for t in matching_kw):
            score += SCORE_EVENT_TYPE_MATCH
            ranking_reasons.append('事件类型匹配')

        for token, field_hits in matched_fields.items():
            for f in field_hits:
                if f == 'fact_summary':
                    score += SCORE_FACT_SUMMARY_HIT
                elif f == 'actors_json':
                    score += SCORE_ACTORS_JSON_HIT
                elif f == 'issues_json':
                    score += SCORE_ISSUES_HIT
                elif f == 'analysis_json':
                    score += SCORE_ANALYSIS_HIT

        seen = set()
        ranking_reasons = [r for r in ranking_reasons if not (r in seen or seen.add(r))]
        return score, match_mode, ranking_reasons, matched_fields

    def search_events(self, keyword: str = '', election_id: str = '',
                      actors: list[str] | None = None, issues: list[str] | None = None,
                      event_type: str = '', date_from: str = '', date_to: str = '',
                      fact_status: str = '', min_significance: int = 0,
                      limit: int = 20) -> list[dict]:
        base_query = 'SELECT * FROM election_events WHERE 1=1'
        base_params = []
        if election_id:
            base_query += ' AND election_id=?'
            base_params.append(election_id)
        if event_type:
            base_query += ' AND event_type=?'
            base_params.append(event_type)
        if fact_status:
            base_query += ' AND fact_status=?'
            base_params.append(fact_status)
        if min_significance:
            base_query += ' AND significance_score>=?'
            base_params.append(min_significance)
        if date_from:
            base_query += ' AND occurred_at>=?'
            base_params.append(date_from)
        if date_to:
            base_query += ' AND occurred_at<=?'
            base_params.append(date_to)

        rows = self.conn.execute(base_query, base_params).fetchall()
        candidates = [self._row_to_event(r) for r in rows]

        tokens = self._tokenize_keywords(keyword) if keyword else []
        scored = []

        for evt in candidates:
            if actors:
                eat = str(evt.get('actors_json', ''))
                if not any(a in eat for a in actors):
                    continue
            if issues:
                eit = str(evt.get('issues_json', ''))
                if not any(i in eit for i in issues):
                    continue

            if tokens:
                score, mode, reasons, mf = self._calc_relevance(evt, tokens)
                if mode == 'irrelevant':
                    continue
            else:
                score, mode, reasons, mf = 50, 'strict', [], {}

            scored.append((score, mode, evt, reasons, mf))

        mode_order = {'strict': 0, 'relaxed': 1}
        scored.sort(key=lambda x: (-x[0], mode_order.get(x[1], 9), -x[2].get('significance_score', 0)))

        result = []
        for score, mode, evt, reasons, mf in scored[:limit]:
            ann = self._annotate_event(evt)
            ann['relevance_score'] = score
            ann['match_mode'] = mode
            ann['matched_keywords'] = list(mf.keys())
            ann['matched_fields'] = mf
            ann['ranking_reasons'] = reasons
            result.append(ann)

        return result

    def _annotate_event(self, evt: dict) -> dict:
        STATUS_REQUIRES_ATTRIBUTION = frozenset({
            'candidate_claim', 'party_claim', 'media_interpretation',
            'analytical_inference', 'disputed',
        })
        if evt.get('fact_status') in STATUS_REQUIRES_ATTRIBUTION:
            evt['requires_attribution'] = True
            if evt['fact_status'] == 'media_interpretation':
                evt['attribution_rule'] = '必须表述为媒体或政治观察的判断，不得作为已核实事实。'
        else:
            evt['requires_attribution'] = False
        return evt
    def _annotate_event(self, evt: dict) -> dict:
        STATUS_REQUIRES_ATTRIBUTION = frozenset({
            'candidate_claim', 'party_claim', 'media_interpretation',
            'analytical_inference', 'disputed',
        })
        if evt.get('fact_status') in STATUS_REQUIRES_ATTRIBUTION:
            evt['requires_attribution'] = True
            if evt['fact_status'] == 'media_interpretation':
                evt['attribution_rule'] = '必须表述为媒体或政治观察的判断，不得作为已核实事实。'
        else:
            evt['requires_attribution'] = False
        return evt

    def get_latest_snapshot(self, election_id: str) -> dict | None:
        # Only return active snapshots; superseded/archived/preview excluded
        rows = self.conn.execute(
            "SELECT * FROM election_state_snapshots WHERE election_id=? AND snapshot_status='active' ORDER BY as_of DESC LIMIT 1",
            (election_id,)
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError(f"Multiple active snapshots for {election_id}")
        if not rows:
            return None
        d = dict(rows[0])
        if d.get('state_json'):
            d['state'] = json.loads(d['state_json'])
        if d.get('supporting_event_ids_json'):
            d['supporting_event_ids'] = json.loads(d['supporting_event_ids_json'])
        return d

    def get_snapshot_history(self, election_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM election_state_snapshots WHERE election_id=? AND snapshot_status!='active' ORDER BY as_of DESC",
            (election_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get('state_json'):
                d['state'] = json.loads(d['state_json'])
            if d.get('supporting_event_ids_json'):
                d['supporting_event_ids'] = json.loads(d['supporting_event_ids_json'])
            result.append(d)
        return result

    def save_snapshot(self, snapshot: dict):
        status = snapshot.get('snapshot_status', 'active')
        self.conn.execute('''INSERT OR REPLACE INTO election_state_snapshots
            (snapshot_id, election_id, as_of, state_json, supporting_event_ids_json,
             created_at, snapshot_status, superseded_by, superseded_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (snapshot['snapshot_id'], snapshot['election_id'], snapshot['as_of'],
             json.dumps(snapshot.get('state_json', snapshot), ensure_ascii=False),
             json.dumps(snapshot.get('supporting_event_ids', []), ensure_ascii=False),
             snapshot.get('created_at', datetime.now().isoformat()),
             status,
             snapshot.get('superseded_by'),
             snapshot.get('superseded_at')))
        self.conn.commit()

    def mark_event_superseded(self, event_id: str):
        self.conn.execute(
            "UPDATE election_events SET fact_status='superseded', updated_at=? WHERE event_id=?",
            (datetime.now().isoformat(), event_id))
        self.conn.commit()

    def get_milestone_events(self, election_id: str, limit: int = 10) -> list[dict]:
        types = ('party_nomination', 'primary_result', 'candidate_announcement',
                 'candidate_withdrawal', 'primary_registration')
        rows = self.conn.execute(
            'SELECT * FROM election_events WHERE election_id=? AND event_type IN '
            f'({",".join("?" for _ in types)}) AND fact_status!="superseded" '
            'ORDER BY significance_score DESC, occurred_at DESC LIMIT ?',
            (election_id, *types, limit)
        ).fetchall()
        return [self._row_to_event(r) for r in rows]
