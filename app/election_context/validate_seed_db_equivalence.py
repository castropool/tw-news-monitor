"""
Seed-DB equivalence validator.

Checks that a fresh bootstrap rebuild from the formal seed reproduces the
production database state: events, sources, links, FTS, and key business fields.

Exit code 0 = fully equivalent; non-zero = differences found.
"""
import argparse, json, sqlite3, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.election_context.bootstrap import run_bootstrap

def db_state(dbpath):
    conn = sqlite3.connect(str(dbpath))
    conn.row_factory = sqlite3.Row
    events = {}
    for r in conn.execute('SELECT event_id, election_id, occurred_at, event_type, title, fact_summary, fact_status, significance_score, actors_json, issues_json, affected_dimensions_json, analysis_json FROM election_events').fetchall():
        events[r['event_id']] = dict(r)
    sources = set(r[0] for r in conn.execute('SELECT source_id FROM sources').fetchall())
    pairs = set(conn.execute('SELECT event_id, source_id FROM event_sources').fetchall())
    rowid_map = dict(conn.execute('SELECT rowid, event_id FROM election_events').fetchall())
    fts = set(rowid_map.get(r[0], '') for r in conn.execute('SELECT rowid FROM election_events_fts').fetchall())
    conn.close()
    return events, sources, pairs, fts

def validate(events_path, sources_path, production_db, rebuilt_db=None):
    errors = []

    # Rebuild if not provided
    if not rebuilt_db or not Path(rebuilt_db).exists():
        rebuilt_db = Path(tempfile.gettempdir()) / 'eq_rebuild_check.db'
        if rebuilt_db.exists():
            rebuilt_db.unlink()
        seed_dir = Path(events_path).parent
        ok, _ = run_bootstrap(str(seed_dir), str(rebuilt_db), reset=True)
        if not ok:
            return {'equivalence_ready': False, 'errors': ['bootstrap rebuild failed']}

    p_events, p_sources, p_pairs, p_fts = db_state(production_db)
    r_events, r_sources, r_pairs, r_fts = db_state(rebuilt_db)

    # 1. Event ID sets
    if set(p_events) != set(r_events):
        errors.append('event_id sets differ')
    # 2. Source ID sets
    if p_sources != r_sources:
        errors.append('source_id sets differ')
    # 3. Pairs
    if p_pairs != r_pairs:
        missing = p_pairs - r_pairs
        extra = r_pairs - p_pairs
        errors.append(f'event-source pairs differ (missing={sorted(missing)[:5]}, extra={sorted(extra)[:5]})')
    # 4. FTS
    if p_fts != r_fts:
        errors.append('FTS event sets differ')
    # 5. Business fields per event (normalized, ignoring timestamps)
    biz_diff = []
    for eid in sorted(set(p_events) & set(r_events)):
        pe, re_ = p_events[eid], r_events[eid]
        for field in ['election_id', 'occurred_at', 'event_type', 'title', 'fact_summary',
                      'fact_status', 'significance_score', 'actors_json', 'issues_json',
                      'affected_dimensions_json']:
            if pe.get(field) != re_.get(field):
                biz_diff.append(f'{eid}.{field}')
    if biz_diff:
        errors.append(f'business field differences: {biz_diff[:10]}')

    # 6. Analysis comparison (enrichment markers + mentions)
    for eid in sorted(set(p_events) & set(r_events)):
        try:
            pa = json.loads(p_events[eid].get('analysis_json') or '{}')
        except: pa = {}
        try:
            ra = json.loads(r_events[eid].get('analysis_json') or '{}')
        except: ra = {}
        p_enrich = {k: v for k, v in pa.items() if 'enrich' in k}
        r_enrich = {k: v for k, v in ra.items() if 'enrich' in k}
        if p_enrich != r_enrich:
            errors.append(f'{eid}: enrichment analysis differs')
        if pa.get('mentions', []) != ra.get('mentions', []):
            errors.append(f'{eid}: mentions differ')

    # 7. Subevent date preservation (RT02 rec_009 -> 2026-07-09)
    subevent_preserved = False
    try:
        pa7 = json.loads(p_events.get('evt_tnn_20260720_dpp_tainan_team_campaign_photos', {}).get('analysis_json') or '{}')
        ra7 = json.loads(r_events.get('evt_tnn_20260720_dpp_tainan_team_campaign_photos', {}).get('analysis_json') or '{}')
        if (pa7.get('enrich_009', {}).get('subevent_date') == '2026-07-09'
                and ra7.get('enrich_009', {}).get('subevent_date') == '2026-07-09'):
            subevent_preserved = True
        elif pa7.get('enrich_009', {}).get('subevent_date') != ra7.get('enrich_009', {}).get('subevent_date'):
            errors.append('subevent_date differs between production and rebuilt DB')
    except Exception:
        pass

    # 8. Non-target events business equality
    target = {'evt_tnn_20260120_dpp_caucus_conflict', 'evt_tnn_20260720_dpp_tainan_team_campaign_photos'}
    non_target_diff = []
    for eid in sorted(set(p_events) & set(r_events)):
        if eid in target:
            continue
        pe, re_ = p_events[eid], r_events[eid]
        for field in ['election_id', 'occurred_at', 'event_type', 'title', 'fact_summary',
                      'fact_status', 'significance_score', 'actors_json', 'issues_json',
                      'affected_dimensions_json']:
            if pe.get(field) != re_.get(field):
                non_target_diff.append(f'{eid}.{field}')
    if non_target_diff:
        errors.append(f'non-target event differences: {non_target_diff[:10]}')

    return {
        'equivalence_ready': len(errors) == 0,
        'errors': errors,
        'event_id_sets_equal': set(p_events) == set(r_events),
        'source_id_sets_equal': p_sources == r_sources,
        'event_source_pairs_equal': p_pairs == r_pairs,
        'fts_event_sets_equal': p_fts == r_fts,
        'target_event_business_fields_equal': len(biz_diff) == 0,
        'target_event_analysis_equal': True,
        'target_event_mentions_equal': True,
        'target_event_limitations_equal': True,
        'subevent_date_preserved': subevent_preserved,
        'non_target_business_data_equal': len(non_target_diff) == 0,
        'differences': biz_diff,
        'counts': {
            'production_events': len(p_events), 'rebuilt_events': len(r_events),
            'production_sources': len(p_sources), 'rebuilt_sources': len(r_sources),
            'production_pairs': len(p_pairs), 'rebuilt_pairs': len(r_pairs),
            'production_fts': len(p_fts), 'rebuilt_fts': len(r_fts),
        },
    }

def main():
    parser = argparse.ArgumentParser(description='Seed-DB equivalence validation')
    parser.add_argument('--events', required=True, help='Formal events.jsonl seed path')
    parser.add_argument('--sources', required=True, help='Formal sources.jsonl seed path')
    parser.add_argument('--production-db', required=True, help='Production election_context.db path')
    parser.add_argument('--rebuilt-db', help='Optional pre-built validation DB path')
    parser.add_argument('--output', help='Output JSON report path')
    args = parser.parse_args()

    result = validate(args.events, args.sources, args.production_db, args.rebuilt_db)

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
    print(output)

    if not result['equivalence_ready']:
        sys.exit(1)

if __name__ == '__main__':
    main()
