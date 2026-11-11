import json
from typing import Any
from .repository import ElectionContextRepository

def audit_database(repo: ElectionContextRepository) -> dict:
    errors = []
    warnings = []
    counts = {}

    counts['elections'] = repo.conn.execute('SELECT COUNT(*) FROM elections').fetchone()[0]
    counts['actors'] = repo.conn.execute('SELECT COUNT(*) FROM actors').fetchone()[0]
    counts['sources'] = repo.conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
    counts['events'] = repo.conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0]
    counts['event_sources'] = repo.conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0]
    counts['snapshots'] = repo.conn.execute('SELECT COUNT(*) FROM election_state_snapshots').fetchone()[0]
    try:
        counts['fts'] = repo.conn.execute('SELECT COUNT(*) FROM election_events_fts').fetchone()[0]
    except Exception:
        counts['fts'] = -1

    # 1. All event election_ids exist
    bad_el = repo.conn.execute('''
        SELECT DISTINCT e.election_id FROM election_events e
        LEFT JOIN elections el ON e.election_id=el.election_id
        WHERE el.election_id IS NULL
    ''').fetchall()
    for r in bad_el:
        errors.append(f'election_id {r[0]} in events but not in elections')

    # 2. All event_sources refs exist
    bad_es = repo.conn.execute('''
        SELECT es.event_id FROM event_sources es
        LEFT JOIN election_events e ON es.event_id=e.event_id
        WHERE e.event_id IS NULL
    ''').fetchall()
    for r in bad_es:
        errors.append(f'event_sources refs missing event: {r[0]}')

    bad_ss = repo.conn.execute('''
        SELECT es.source_id FROM event_sources es
        LEFT JOIN sources s ON es.source_id=s.source_id
        WHERE s.source_id IS NULL
    ''').fetchall()
    for r in bad_ss:
        errors.append(f'event_sources refs missing source: {r[0]}')

    # 3. All snapshot supporting_event_ids exist
    snap_rows = repo.conn.execute(
        'SELECT snapshot_id, supporting_event_ids_json FROM election_state_snapshots'
    ).fetchall()
    for row in snap_rows:
        try:
            eids = json.loads(row['supporting_event_ids_json']) if row['supporting_event_ids_json'] else []
        except Exception:
            errors.append(f'snapshot {row[0]}: supporting_event_ids_json invalid JSON')
            continue
        for eid in eids:
            evt = repo.conn.execute(
                'SELECT 1 FROM election_events WHERE event_id=?', (eid,)
            ).fetchone()
            if not evt:
                errors.append(f'snapshot {row[0]} supports missing event: {eid}')

    # 4. Non-analytical_inference events must have at least one source
    no_src = repo.conn.execute('''
        SELECT e.event_id FROM election_events e
        LEFT JOIN event_sources es ON e.event_id=es.event_id
        WHERE e.fact_status!='analytical_inference' AND es.event_id IS NULL
    ''').fetchall()
    for r in no_src:
        errors.append(f'event {r[0]} has no source but status != analytical_inference')

    # 5. verified/multi_source_verified must not rely solely on candidate_self_statement
    fake_src = repo.conn.execute('''
        SELECT es.event_id FROM event_sources es
        JOIN sources s ON es.source_id=s.source_id
        JOIN election_events e ON es.event_id=e.event_id
        WHERE e.fact_status IN ('verified','multi_source_verified')
        AND s.evidence_level='candidate_self_statement'
        AND NOT EXISTS (
            SELECT 1 FROM event_sources es2
            JOIN sources s2 ON es2.source_id=s2.source_id
            WHERE es2.event_id=e.event_id AND s2.evidence_level!='candidate_self_statement'
        )
    ''').fetchall()
    for r in fake_src:
        warnings.append(f'event {r[0]} verified but only has candidate_self_statement sources')

    # 6. party_nomination date must not be before primary_result for same candidate
    # Check each event individually
    nom_rows = repo.conn.execute(
        "SELECT event_id, occurred_at, actors_json FROM election_events WHERE event_type='party_nomination'"
    ).fetchall()
    for nr in nom_rows:
        try:
            actors = json.loads(nr['actors_json']) if nr['actors_json'] else []
        except Exception:
            actors = []
        for actor in actors:
            primaries = repo.conn.execute(
                "SELECT event_id, occurred_at FROM election_events WHERE event_type='primary_result' AND actors_json LIKE ?",
                (f'%{actor}%',)
            ).fetchall()
            for primary in primaries:
                if primary['occurred_at'] and nr['occurred_at'] and primary['occurred_at'] > nr['occurred_at']:
                    errors.append(f"party_nomination {nr['event_id']} before primary_result {primary['event_id']} for {actor}")

    # 7. superseded events must have reason in analysis_json
    sup = repo.conn.execute(
        "SELECT event_id, analysis_json FROM election_events WHERE fact_status='superseded'"
    ).fetchall()
    for s in sup:
        aj = s['analysis_json']
        reason = ''
        if aj:
            try:
                reason = json.loads(aj).get('superseded_by', '')
            except Exception:
                pass
        if not reason:
            warnings.append(f'superseded event {s[0]} missing superseded_by reason')

    # 8. No alias maps to multiple actor_ids
    alias_map = {}
    actor_rows = repo.conn.execute('SELECT actor_id, aliases_json FROM actors').fetchall()
    for ar in actor_rows:
        try:
            aliases = json.loads(ar['aliases_json']) if ar['aliases_json'] else []
        except Exception:
            aliases = []
        for alias in aliases + [ar[0]]:
            if alias in alias_map:
                if alias_map[alias] != ar[0]:
                    errors.append(f'alias {alias} maps to both {alias_map[alias]} and {ar[0]}')
            else:
                alias_map[alias] = ar[0]

    # 9. significance_score 0-100
    bad_score = repo.conn.execute(
        'SELECT event_id, significance_score FROM election_events WHERE significance_score<0 OR significance_score>100'
    ).fetchall()
    for r in bad_score:
        errors.append(f'event {r[0]} significance_score {r[1]} out of range')

    # 10. JSON fields parse
    json_fields = ['actors_json', 'issues_json', 'affected_dimensions_json', 'analysis_json']
    evt_rows = repo.conn.execute(
        f"SELECT event_id, {','.join(json_fields)} FROM election_events"
    ).fetchall()
    for er in evt_rows:
        for f in json_fields:
            val = er[f]
            if val:
                try:
                    json.loads(val)
                except Exception:
                    errors.append(f'event {er[0]} has invalid JSON in {f}')

    # 11. FTS consistency
    if counts['fts'] >= 0:
        fts_events = repo.conn.execute(
            'SELECT DISTINCT rowid FROM election_events_fts'
        ).fetchall()
        fts_ids = set(r[0] for r in fts_events)
        real_ids = set(r[0] for r in repo.conn.execute('SELECT rowid FROM election_events').fetchall())
        missing_in_fts = real_ids - fts_ids
        extra_in_fts = fts_ids - real_ids
        if missing_in_fts:
            warnings.append(f'FTS missing {len(missing_in_fts)} events')
        if extra_in_fts:
            warnings.append(f'FTS has {len(extra_in_fts)} extra rows')

    # 12. Snapshot as_of not before event occurred_at
    snap_rows2 = repo.conn.execute(
        'SELECT snapshot_id, as_of, supporting_event_ids_json FROM election_state_snapshots'
    ).fetchall()
    for sr in snap_rows2:
        try:
            eids = json.loads(sr['supporting_event_ids_json']) if sr['supporting_event_ids_json'] else []
        except Exception:
            continue
        for eid in eids:
            evt = repo.conn.execute(
                'SELECT occurred_at FROM election_events WHERE event_id=?', (eid,)
            ).fetchone()
            if evt and evt[0] and sr['as_of'] and evt[0] > sr['as_of']:
                errors.append(f'snapshot {sr[0]} as_of {sr['as_of']} before event {eid} occurred_at {evt[0]}')

    return {
        'ok': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'counts': counts,
    }
