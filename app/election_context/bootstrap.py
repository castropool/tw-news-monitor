import argparse
import json
import os
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.election_context.repository import ElectionContextRepository
from app.election_context.audit import audit_database

EVENT_TYPES = frozenset([
    'candidate_announcement','candidate_withdrawal','primary_registration',
    'primary_result','party_nomination','endorsement','party_integration',
    'faction_conflict','alliance_proposal','alliance_agreement','poll_release',
    'policy_proposal','campaign_launch','fundraising','governance_event',
    'disaster_response','scandal_allegation','judicial_event',
    'campaign_attack','joint_campaign','candidate_status_change',
    'primary_procedure','primary_debate',
])
FACT_STATUSES = frozenset([
    'verified','multi_source_verified','candidate_claim','party_claim',
    'media_interpretation','poll_result','analytical_inference',
    'disputed','pending_verification','superseded',
])

def dry_run(seed_dir):
    seed = Path(seed_dir)
    errors = []
    expected = ['election.json','actors.yaml','sources.jsonl','events.jsonl','initial_snapshot.json']
    for fname in expected:
        p = seed / fname
        if not p.exists():
            errors.append(f'missing {fname}')
        else:
            print(f'  {fname}: found')
    evt_path = seed / 'events.jsonl'
    if evt_path.exists():
        count = 0
        with open(evt_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                count += 1
                et = e.get('event_type','')
                if et not in EVENT_TYPES:
                    errors.append(f'invalid event_type={et} in {e.get("event_id")}')
                fs = e.get('fact_status','')
                if fs not in FACT_STATUSES:
                    errors.append(f'invalid fact_status={fs} in {e.get("event_id")}')
                sc = e.get('significance_score',0)
                if not (0 <= sc <= 100):
                    errors.append(f'invalid significance_score={sc} in {e.get("event_id")}')
        print(f'  events: {count} validated')
    if errors:
        for e in errors:
            print(f'  ERROR: {e}')
        return False
    print('  All valid')
    return True

def run_bootstrap(seed_dir, db_path, reset=False):
    seed = Path(seed_dir)
    dbp = Path(db_path)
    if reset and dbp.exists():
        try:
            dbp.unlink(missing_ok=True)
            print(f'Reset: removed {dbp}')
        except PermissionError:
            print(f'Reset: could not remove {dbp} (in use), will overwrite')
    repo = ElectionContextRepository(str(dbp))
    repo.connect()
    repo.create_tables()
    stats = {}

    el_path = seed / 'election.json'
    if el_path.exists():
        with open(el_path, encoding='utf-8') as f:
            el = json.load(f)
        repo.save_election(el)
        stats['elections'] = 1
        print(f'  election: {el["election_id"]}')

    act_path = seed / 'actors.yaml'
    if act_path.exists():
        with open(act_path, encoding='utf-8') as f:
            act_data = yaml.safe_load(f)
        for a in act_data.get('actors',[]):
            repo.conn.execute(
                'INSERT OR IGNORE INTO actors (actor_id,canonical_name,actor_type,party,aliases_json) VALUES (?,?,?,?,?)',
                (a['actor_id'],a['canonical_name'],a['actor_type'],a.get('party',''),
                 json.dumps(a.get('aliases',[]), ensure_ascii=False))
            )
        repo.conn.commit()
        stats['actors'] = len(act_data.get('actors',[]))
        print(f'  actors: {stats["actors"]}')

    src_path = seed / 'sources.jsonl'
    if src_path.exists():
        count = 0
        with open(src_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                repo.save_source(json.loads(line))
                count += 1
        stats['sources'] = count
        print(f'  sources: {count}')

    evt_path = seed / 'events.jsonl'
    evt_count = 0
    if evt_path.exists():
        with open(evt_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                eid = repo.save_event(e)
                for src in e.get('sources',[]):
                    sid = repo.save_source(src)
                    repo.link_event_source(eid, sid, src.get('is_primary',False))
                evt_count += 1
        stats['events'] = evt_count
        print(f'  events: {evt_count}')

    def _raw_to_snapshot(raw):
        if 'state_json' in raw:
            return raw
        state_keys = ['coverage', 'candidate_status', 'structural_lean', 'competitiveness',
                      'dpp_integration', 'kmt_organization', 'kmt_tpp_cooperation',
                      'core_issues', 'key_risks', 'milestone_events',
                      'unresolved_questions', 'generated_at']
        return {
            'snapshot_id': raw.get('snapshot_id', ''),
            'election_id': raw.get('election_id', ''),
            'as_of': raw.get('as_of', ''),
            'state_json': {k: raw.get(k) for k in state_keys if k in raw},
            'supporting_event_ids': raw.get('supporting_event_ids', []),
            'created_at': raw.get('generated_at', ''),
            'snapshot_status': raw.get('snapshot_status', 'active'),
            'superseded_by': raw.get('superseded_by'),
            'superseded_at': raw.get('superseded_at'),
        }

    # Import history first (superseded snapshots)
    hist_path = seed / 'snapshot_history.jsonl'
    hist_count = 0
    if hist_path.exists():
        with open(hist_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                snap = _raw_to_snapshot(raw)
                repo.save_snapshot(snap)
                hist_count += 1
        print(f'  history snapshots: {hist_count}')

    # Import active snapshot second
    snap_path = seed / 'initial_snapshot.json'
    if snap_path.exists():
        with open(snap_path, encoding='utf-8') as f:
            raw = json.load(f)
        snap = _raw_to_snapshot(raw)
        snap['snapshot_status'] = 'active'
        repo.save_snapshot(snap)
        stats['snapshots'] = hist_count + 1
        print(f'  active snapshot: {snap["snapshot_id"]}')
    else:
        stats['snapshots'] = hist_count

    repo.conn.execute('DROP TABLE IF EXISTS election_events_fts')
    repo.conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS election_events_fts USING fts5(title, fact_summary, actors, issues, tokenize='unicode61')"
    )
    events = repo.conn.execute('SELECT rowid,title,fact_summary,actors_json,issues_json FROM election_events').fetchall()
    fts_count = 0
    for e in events:
        try:
            repo.conn.execute(
                'INSERT INTO election_events_fts(rowid,title,fact_summary,actors,issues) VALUES (?,?,?,?,?)',
                (e['rowid'],e['title'],e['fact_summary'],e['actors_json'],e['issues_json'])
            )
            fts_count += 1
        except Exception:
            pass
    repo.conn.commit()
    stats['fts'] = fts_count
    print(f'  FTS: {fts_count} rows')

    # Import polls (tables created by repository.create_tables)

    # Import poll sources (from poll_sources.jsonl if it exists)
    poll_src_path = seed / 'poll_sources.jsonl'
    if poll_src_path.exists():
        psc = 0
        with open(poll_src_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                ps = json.loads(line)
                repo.save_source(ps)
                psc += 1
        print(f'  poll sources: {psc}')

    # Import polls
    poll_jsonl = seed / 'polls.jsonl'
    if poll_jsonl.exists():
        cnt = 0
        with open(poll_jsonl, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                p = json.loads(line)
                pi = p.get('publication', {})
                repo.conn.execute("INSERT OR IGNORE INTO election_polls (poll_id,election_id,poll_type,fact_status,methodology_complete,verification_tier,recommended_disposition,canonical_origin,publication_json,fieldwork_json,methodology_json,population_json,limitations_json,usable_for_poll_trend,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (p['poll_id'],p.get('election_id','TW-2026-TNN-MAYOR'),p.get('poll_type',''),p.get('fact_status',''),1 if p.get('methodology_complete') else 0,p.get('verification_tier',''),p.get('recommended_disposition',''),pi.get('canonical_origin',''),json.dumps(pi,ensure_ascii=False),json.dumps(p.get('fieldwork',{}),ensure_ascii=False),json.dumps(p.get('methodology',{}),ensure_ascii=False),json.dumps(p.get('population',{}),ensure_ascii=False),json.dumps(p.get('limitations',[]),ensure_ascii=False),1 if p.get('usable_for_poll_trend') else 0,'',''))
                cnt += 1
        print(f'  polls: {cnt}')
        stats['polls'] = cnt

    comp_jsonl = seed / 'poll_question_comparability.jsonl'
    if comp_jsonl.exists():
        cnt = 0
        with open(comp_jsonl, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                q = json.loads(line)
                repo.conn.execute("INSERT OR IGNORE INTO poll_questions (poll_id,question_id,question_type,candidate_set_json,base_population,population_filter,trend_eligible,trend_scope,comparable_group_key,note) VALUES (?,?,?,?,?,?,?,?,?,?)", (q['poll_id'],q['question_id'],q.get('question_type',''),json.dumps(q.get('candidate_set',[]),ensure_ascii=False),q.get('base_population',''),q.get('population_filter',''),1 if q.get('trend_eligible') else 0,q.get('trend_scope',''),q.get('comparable_group_key',''),q.get('note','')))
                cnt += 1
        print(f'  questions: {cnt}')
        stats['questions'] = cnt

    links_jsonl = seed / 'poll_source_links.jsonl'
    if links_jsonl.exists():
        cnt = 0
        with open(links_jsonl, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                lk = json.loads(line)
                repo.conn.execute("INSERT OR IGNORE INTO poll_source_links (poll_id,source_id) VALUES (?,?)", (lk['poll_id'],lk['source_id']))
                cnt += 1
        print(f'  source links: {cnt}')
        stats['source_links'] = cnt

    # Import poll results (from polls.jsonl embedded results)
    result_count = 0
    poll_jsonl2 = seed / 'polls.jsonl'
    if poll_jsonl2.exists():
        with open(poll_jsonl2, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                p = json.loads(line)
                pid = p['poll_id']
                for i, res in enumerate(p.get('results', [])):
                    repo.conn.execute('INSERT OR IGNORE INTO poll_results (poll_id,question_id,option_id,option_name,option_type,reported_value,value,normalized_value,unit,base_population,is_derived,result_order) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)', (pid,res.get('question_id',''),res.get('option_id',''),res.get('option_name',''),res.get('option_type',''),res.get('reported_value',''),res.get('value'),res.get('normalized_value'),res.get('unit','percent'),res.get('base_population',''),i))
                    result_count += 1
    stats['results'] = result_count
    if result_count:
        print(f'  results: {result_count}')

    repo.conn.commit()

    audit = audit_database(repo)
    stats['audit_ok'] = audit['ok']
    stats['audit_errors'] = len(audit['errors'])
    if audit['errors']:
        print('  AUDIT ERRORS:')
        for ae in audit['errors']:
            print(f'    ERROR: {ae}')
    if audit['warnings']:
        for aw in audit['warnings']:
            print(f'    WARN: {aw}')
    print(f'  Audit: ok={audit["ok"]}, errors={len(audit["errors"])}, warnings={len(audit["warnings"])}')
    repo.close()
    return audit['ok'], stats

def main():
    parser = argparse.ArgumentParser(description='Election Context Bootstrap')
    parser.add_argument('--seed-dir', default='data/election_seed/tainan_2026')
    parser.add_argument('--db', default='data/election_context.db')
    parser.add_argument('--reset', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        ok = dry_run(args.seed_dir)
        print(f'Dry-run: ok={ok}')
        sys.exit(0 if ok else 1)
    ok, stats = run_bootstrap(args.seed_dir, args.db, args.reset)
    print(f'Bootstrap: ok={ok}')
    if not ok:
        sys.exit(1)

if __name__ == '__main__':
    main()
