import argparse, json, sqlite3, sys
from pathlib import Path

def validate(polls_path, comp_path, links_path, hold_path, db_path):
    with open(polls_path, encoding='utf-8') as f:
        seed_polls = [json.loads(l) for l in f if l.strip()]
    with open(comp_path, encoding='utf-8') as f:
        seed_qs = [json.loads(l) for l in f if l.strip()]
    with open(links_path, encoding='utf-8') as f:
        seed_links = [json.loads(l) for l in f if l.strip()]
    with open(hold_path, encoding='utf-8') as f:
        hold = [json.loads(l) for l in f if l.strip()]

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    errors = []
    warnings = []
    checks = {}

    db_polls = {r['poll_id'] for r in db.execute('SELECT poll_id FROM election_polls').fetchall()}
    seed_ids = {p['poll_id'] for p in seed_polls}
    missing_p = seed_ids - db_polls
    extra_p = db_polls - seed_ids
    checks['polls_match'] = len(missing_p) == 0 and len(extra_p) == 0
    if missing_p: errors.append('missing polls: ' + str(missing_p))
    if extra_p: errors.append('extra polls: ' + str(extra_p))

    db_q = {(r['poll_id'],r['question_id']) for r in db.execute('SELECT poll_id,question_id FROM poll_questions').fetchall()}
    seed_q_set = {(q['poll_id'],q['question_id']) for q in seed_qs}
    q_miss = seed_q_set - db_q; q_extra = db_q - seed_q_set
    checks['questions_match'] = len(q_miss) == 0 and len(q_extra) == 0
    if q_miss: errors.append('missing q: ' + str(q_miss))
    if q_extra: errors.append('extra q: ' + str(q_extra))

    db_res = db.execute('SELECT COUNT(*) FROM poll_results').fetchone()[0]
    seed_res = sum(len(p.get('results',[])) for p in seed_polls)
    checks['results_match'] = db_res == seed_res
    if db_res != seed_res: errors.append('results: db=' + str(db_res) + ' seed=' + str(seed_res))

    db_links = {(r['poll_id'],r['source_id']) for r in db.execute('SELECT poll_id,source_id FROM poll_source_links').fetchall()}
    seed_lk = {(l['poll_id'],l['source_id']) for l in seed_links}
    l_miss = seed_lk - db_links; l_extra = db_links - seed_lk
    checks['links_match'] = len(l_miss) == 0 and len(l_extra) == 0
    if l_miss: errors.append('missing links: ' + str(l_miss))
    if l_extra: errors.append('extra links: ' + str(l_extra))

    hold_ids = {h['poll_id'] for h in hold}
    placeholders = ','.join('?' for _ in hold_ids)
    db_hold = db.execute('SELECT poll_id FROM election_polls WHERE poll_id IN (' + placeholders + ')', list(hold_ids)).fetchall()
    checks['hold_not_in_db'] = len(db_hold) == 0
    if db_hold: errors.append('hold in db: ' + str([r[0] for r in db_hold]))

    internal = ['poll_tnn_20251022_trend_internal','poll_tnn_20251225_dpp_internal_reported','poll_tnn_20260107_rainclear_internal']
    for pid in internal:
        c = db.execute('SELECT COUNT(*) FROM poll_questions WHERE poll_id=? AND trend_eligible=1', (pid,)).fetchone()[0]
        if c: errors.append('internal trend: ' + pid)
    checks['internal_not_trend'] = True

    d = db.execute("SELECT COUNT(*) FROM poll_questions WHERE poll_id='poll_tnn_20260114_dpp_primary_official' AND trend_eligible=1").fetchone()[0]
    if d: errors.append('dpp primary trend')
    checks['dpp_primary_not_trend'] = True

    tv = db.execute("SELECT comparable_group_key FROM poll_questions WHERE poll_id='poll_tnn_20260312_tvbs' AND question_id='q_chen_hsieh_likely'").fetchone()
    if tv and tv[0] != 'tnn_h2h_voting_intention_landline_chen_hsieh':
        errors.append('tvbs group: ' + str(tv[0]))
    checks['tvbs_group'] = True

    on = db.execute("SELECT comparable_group_key FROM poll_questions WHERE poll_id='poll_tnn_20260228_juwen_pearson_online'").fetchone()
    if on and on[0] != 'tnn_h2h_online_network_population_chen_hsieh':
        errors.append('online group: ' + str(on[0]))
    checks['online_group'] = True

    groups = db.execute("SELECT comparable_group_key, COUNT(*) FROM poll_questions WHERE comparable_group_key IS NOT NULL AND comparable_group_key NOT IN ('','not_assigned') GROUP BY comparable_group_key").fetchall()
    checks['six_groups'] = len(groups) == 6
    if len(groups) != 6: errors.append('groups: ' + str(len(groups)))
    checks['sixteen_trend'] = db.execute('SELECT COUNT(*) FROM poll_questions WHERE trend_eligible=1').fetchone()[0] == 16

    for lk in seed_links:
        r = db.execute('SELECT 1 FROM sources WHERE source_id=?', (lk['source_id'],)).fetchone()
        if not r: errors.append('missing source: ' + lk['source_id'])
    checks['source_ids_exist'] = True

    res_q = {(r[0],r[1]) for r in db.execute('SELECT DISTINCT poll_id, question_id FROM poll_results').fetchall()}
    valid_q = {(r['poll_id'],r['question_id']) for r in db.execute('SELECT poll_id,question_id FROM poll_questions').fetchall()}
    bad = res_q - valid_q
    if bad: errors.append('bad result refs: ' + str(bad))
    checks['result_questions_valid'] = len(bad) == 0

    evt = db.execute('SELECT COUNT(*) FROM election_events').fetchone()[0]
    checks['events_unchanged'] = evt == 15
    sp = db.execute('SELECT COUNT(*) FROM election_state_snapshots').fetchone()[0]
    checks['snapshots_unchanged'] = sp == 2
    ac = db.execute('SELECT COUNT(*) FROM actors').fetchone()[0]
    checks['actors_unchanged'] = ac == 6

    db.close()
    return {'release_ready': len(errors) == 0, 'errors': errors, 'warnings': warnings, 'checks': checks}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--polls', required=True); p.add_argument('--comparability', required=True)
    p.add_argument('--source-links', required=True); p.add_argument('--hold', required=True)
    p.add_argument('--db', required=True)
    args = p.parse_args()
    r = validate(args.polls, args.comparability, args.source_links, args.hold, args.db)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if not r['release_ready']: sys.exit(1)

if __name__ == '__main__': main()
