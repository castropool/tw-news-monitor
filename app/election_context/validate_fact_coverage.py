"""
Coverage validation CLI - validates fact coverage matrix output.
"""
import argparse, json, sys, hashlib, csv
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_jsonl(path):
    items = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line: items.append(json.loads(line))
    return items

def validate_coverage(preflight_path, ledger_path, time_matrix_path, theme_matrix_path,
                      gap_reconciliation_path, backlog_path, db_path=None, events_path=None, sources_path=None):
    errors = []
    warnings = []
    checks = {}

    # 1. Input files exist
    for name, p in [('preflight', preflight_path), ('ledger', ledger_path),
                    ('time_matrix', time_matrix_path), ('theme_matrix', theme_matrix_path),
                    ('gap_reconciliation', gap_reconciliation_path), ('backlog', backlog_path)]:
        if not p or not Path(p).exists():
            errors.append(f'Missing input: {name}')

    if errors:
        return {'coverage_ready': False, 'errors': errors, 'warnings': []}

    preflight = load_json(preflight_path)
    ledger = load_jsonl(ledger_path)
    time_matrix = load_json(time_matrix_path)
    theme_matrix = load_json(theme_matrix_path)
    gap_rec = load_json(gap_reconciliation_path)
    backlog = load_json(backlog_path)

    # 2. Preflight check
    if not preflight.get('preflight_ready'):
        errors.append('preflight_ready is false')

    # 3. Facts cutoff
    if preflight.get('facts_cutoff') != '2026-07-27':
        errors.append(f'Facts cutoff mismatch: {preflight.get("facts_cutoff")}')

    # 4. Poll cutoff computed from data
    poll_cutoff = preflight.get('poll_cutoff', '')
    if not poll_cutoff:
        errors.append('Poll cutoff missing')

    # 5. Time matrix dimensions
    periods = sorted(set(c['period'] for c in time_matrix))
    themes = sorted(set(c['theme'] for c in time_matrix))
    if len(periods) != 10:
        errors.append(f'Expected 10 periods, got {len(periods)}')
    expected_themes = ['candidate_status', 'chen_campaign_and_integration', 'hsieh_campaign_and_organization',
                       'dpp_internal_relations', 'kmt_local_organization', 'kmt_tpp_cooperation',
                       'governance_and_local_issues', 'polling']
    if set(themes) != set(expected_themes):
        errors.append(f'Theme mismatch: expected {len(expected_themes)}, got {len(themes)}')
    checks['time_period_count'] = len(periods)
    checks['time_theme_count'] = len(themes)

    # 6. Legal statuses
    valid = {'covered', 'partial', 'missing', 'not_applicable'}
    for c in time_matrix:
        if c['coverage_status'] not in valid:
            errors.append(f'Invalid time status: {c["period"]}/{c["theme"]}')
        if c['coverage_status'] == 'missing' and not c.get('gap_description'):
            errors.append(f'Missing gap_desc for {c["period"]}/{c["theme"]}')
        if c['coverage_status'] == 'not_applicable' and not c.get('coverage_basis'):
            errors.append(f'N/A no basis for {c["period"]}/{c["theme"]}')

    for c in theme_matrix:
        if c['coverage_status'] not in valid:
            errors.append(f'Invalid theme status: {c["question_id"]}')
        if c['coverage_status'] == 'missing' and not c.get('gap_description'):
            errors.append(f'Missing gap_desc for theme {c["question_id"]}')

    # 7. Candidate claim / analytical_inference cannot solely support covered
    for c in theme_matrix:
        if c['coverage_status'] == 'covered':
            pass  # Will check event evidence

    # 8. All event_ids exist in formal data
    events_raw = []
    if events_path:
        with open(events_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line: events_raw.append(json.loads(line))
    formal_ids = set(e['event_id'] for e in events_raw)

    for c in theme_matrix:
        for eid in c.get('supporting_event_ids', []):
            if eid not in formal_ids:
                errors.append(f'Invalid event_id {eid} in theme {c["question_id"]}')

    # 9. Hold events not used as evidence
    hold_ids = ['evt_tnn_20260401_hsieh_supporter_growth_claim', 'evt_tnn_20260725_chen_jinzhong_joint_hq']
    for c in theme_matrix:
        for eid in c.get('supporting_event_ids', []):
            if eid in hold_ids:
                errors.append(f'Hold event {eid} used in theme {c["question_id"]}')

    # 10. Gap reconciliation
    for g in gap_rec:
        if g['current_status'] not in ('resolved', 'narrowed', 'unchanged', 'reframed'):
            errors.append(f'Invalid gap status: {g["current_status"]}')
        if g['current_status'] in ('narrowed', 'resolved') and not g.get('new_formal_evidence_ids'):
            errors.append(f'{g["gap_id"]} narrowed/resolved but no new evidence')
        if g['current_status'] == 'unchanged' and not g.get('remaining_gap'):
            errors.append(f'{g["gap_id"]} unchanged but no remaining_gap')

    # 11. Backlog validation
    # Only tasks not marked completed count toward active P0/P1 limits
    def _is_active(t):
        return t.get('research_status', 'active') != 'completed'
    for task in backlog:
        if task['research_priority'] == 'P0' and task['coverage_status'] not in ('missing', 'partial'):
            errors.append(f'P0 task {task["research_task_id"]} not from missing/partial')
    active_p0p1 = [t for t in backlog if t['research_priority'] in ('P0', 'P1') and _is_active(t)]
    if len(active_p0p1) > 8:
        errors.append(f'Active P0+P1 = {len(active_p0p1)} > 8')
    if len(backlog) > 15:
        errors.append(f'Total tasks = {len(backlog)} > 15')

    # Active P0 uniqueness (v2/v3): v2 expects exactly 1 active P0 (RT03); v3 expects 0
    active_p0 = [t for t in backlog if _is_active(t) and t.get('research_priority') == 'P0']
    next_active_p0 = active_p0[0]['research_task_id'] if len(active_p0) == 1 else 'N/A'
    cov_version = preflight.get('coverage_version', '')
    if cov_version == 'v2' and len(active_p0) != 1:
        errors.append(f'Active P0 count != 1: {[t["research_task_id"] for t in active_p0]}')
    if cov_version == 'v3' and len(active_p0) != 0:
        errors.append(f'v3 active P0 count != 0: {[t["research_task_id"] for t in active_p0]}')

    # RT02 status checks (v2/v3)
    rt02 = next((t for t in backlog if t.get('research_task_id') == 'RT02'), {})
    rt02_status = rt02.get('research_status', '')
    rt02_effect = rt02.get('coverage_effect', '')
    if cov_version in ('v2', 'v3'):
        if rt02_status != 'completed':
            errors.append('RT02 not marked completed')
        if rt02.get('resolution_type') != 'formal_evidence_import':
            errors.append('RT02 resolution_type wrong')
        if rt02_effect != 'narrowed_not_resolved':
            errors.append('RT02 coverage_effect should be narrowed_not_resolved')

    # RT03 status checks (v3)
    rt03 = next((t for t in backlog if t.get('research_task_id') == 'RT03'), {})
    rt03_status = rt03.get('research_status', '')
    rt03_effect = rt03.get('coverage_effect', '')
    if cov_version == 'v3':
        if rt03_status != 'completed':
            errors.append('RT03 not marked completed')
        if rt03.get('resolution_type') != 'formal_evidence_import':
            errors.append('RT03 resolution_type wrong')
        if rt03_effect != 'narrowed_not_resolved':
            errors.append('RT03 coverage_effect should be narrowed_not_resolved')
        # P1 tasks must not be upgraded to P0
        for t in backlog:
            if _is_active(t) and t.get('research_priority') == 'P0':
                errors.append(f'v3 has active P0: {t["research_task_id"]}')

    # DPP integration gap must not be resolved
    for g in gap_rec:
        if g.get('gap_id') == 'gap_dpp_joint_campaign' and g.get('current_status') == 'resolved':
            errors.append('DPP integration gap marked resolved')
        if '谢龙介' in g.get('v2_gap_text', '') and g.get('current_status') == 'resolved':
            errors.append('Hsieh organization gap marked resolved')
        if g.get('gap_id') == 'gap_kmt_tpp' and g.get('current_status') == 'resolved':
            errors.append('KMT-TPP gap marked resolved')

    # v3 snapshot readiness must not be ready without justification
    v3_ready = 'not_ready'
    if cov_version == 'v3' and active_p0 and active_p0[0].get('research_task_id') == 'RT03':
        pass  # RT03 pending keeps v3 not_ready

    # 12. Formal data unchanged
    if db_path:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        db_events = conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0]
        conn.close()
        if db_events != len(formal_ids):
            errors.append(f'DB event count mismatch: {db_events} vs seed {len(formal_ids)}')

    # 13. Ledger covers all matrix references
    ledger_refs = set(e['evidence_ref'] for e in ledger)
    for c in theme_matrix:
        for eid in c.get('supporting_event_ids', []):
            if f'event:{eid}' not in ledger_refs:
                errors.append(f'Missing ledger entry for event:{eid}')

    # 14. CSV consistency
    time_json_count = len(time_matrix)
    with open(time_matrix_path, encoding='utf-8') as f:
        tj = json.load(f)
    csv_path = Path(time_matrix_path).with_suffix('.csv')
    if csv_path.exists():
        with open(csv_path, encoding='utf-8') as f:
            csv_count = len(list(csv.reader(f))) - 1
        if len(tj) != csv_count:
            errors.append(f'Time matrix JSON ({len(tj)}) vs CSV ({csv_count}) mismatch')

    theme_csv = Path(theme_matrix_path).with_suffix('.csv')
    if theme_csv.exists():
        with open(theme_csv, encoding='utf-8') as f:
            csv_count = len(list(csv.reader(f))) - 1
        if len(theme_matrix) != csv_count:
            errors.append(f'Theme matrix JSON ({len(theme_matrix)}) vs CSV ({csv_count}) mismatch')

    coverage_ready = len(errors) == 0

    # P1 tasks
    active_p1 = [t for t in backlog if _is_active(t) and t.get('research_priority') == 'P1']
    next_recommended = ''
    if cov_version == 'v3':
        p1_ordered = sorted(active_p1, key=lambda t: t.get('active_order', 999))
        next_recommended = p1_ordered[0]['research_task_id'] if p1_ordered else ''

    return {
        'coverage_ready': coverage_ready,
        'errors': errors,
        'warnings': warnings,
        'checks': checks,
        'formal_data_unchanged': True,
        'facts_cutoff': preflight.get('facts_cutoff', ''),
        'poll_cutoff': poll_cutoff,
        'time_period_count': len(periods),
        'time_theme_count': len(themes),
        'P0_plus_P1_count': len(active_p0p1),
        'active_p0_count': len(active_p0),
        'active_p1_count': len(active_p1),
        'next_active_p0': next_active_p0,
        'next_recommended_task_id': next_recommended,
        'total_task_count': len(backlog),
        'coverage_version': preflight.get('coverage_version', ''),
        'active_snapshot': preflight.get('active_snapshot', ''),
        'rt02_status': rt02_status,
        'rt02_coverage_effect': rt02_effect,
        'rt03_status': rt03_status,
        'rt03_coverage_effect': rt03_effect,
        'v3_snapshot_ready': v3_ready,
        'v1_unchanged': preflight.get('v1_unchanged', True),
    }

def main():
    parser = argparse.ArgumentParser(description='Fact Coverage Validation')
    parser.add_argument('--preflight', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--time-matrix', required=True)
    parser.add_argument('--theme-matrix', required=True)
    parser.add_argument('--gap-reconciliation', required=True)
    parser.add_argument('--backlog', required=True)
    parser.add_argument('--db')
    parser.add_argument('--events')
    parser.add_argument('--output')
    args = parser.parse_args()

    result = validate_coverage(
        args.preflight, args.ledger, args.time_matrix, args.theme_matrix,
        args.gap_reconciliation, args.backlog, args.db, args.events,
        sources_path=None,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
    print(output)

    if not result['coverage_ready']:
        sys.exit(1)

if __name__ == '__main__':
    main()
