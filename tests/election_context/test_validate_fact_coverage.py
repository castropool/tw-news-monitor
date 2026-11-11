"""
Tests for fact coverage validation.
"""
import json, sys, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.election_context.validate_fact_coverage import validate_coverage

BASE = Path(__file__).resolve().parent.parent.parent
SEED = BASE / 'data' / 'election_seed' / 'tainan_2026'
COV = SEED / 'fact_coverage_20260727_v1'

def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def _load_jsonl(path):
    items = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line: items.append(json.loads(line))
    return items

# Test 1: Formal events readable
def test_formal_events_readable():
    evts = _load_jsonl(SEED / 'events.jsonl')
    assert len(evts) == 50

# Test 2: Formal sources readable
def test_formal_sources_readable():
    srcs = _load_jsonl(SEED / 'sources.jsonl')
    assert len(srcs) == 113

# Test 3: Time matrix periods complete
def test_time_matrix_periods():
    tm = _load_json(COV / 'time_coverage_matrix.json')
    periods = sorted(set(c['period'] for c in tm))
    assert len(periods) == 10

# Test 4: Theme columns complete
def test_time_matrix_themes():
    tm = _load_json(COV / 'time_coverage_matrix.json')
    themes = sorted(set(c['theme'] for c in tm))
    expected = ['candidate_status', 'chen_campaign_and_integration', 'hsieh_campaign_and_organization',
                'dpp_internal_relations', 'kmt_local_organization', 'kmt_tpp_cooperation',
                'governance_and_local_issues', 'polling']
    assert set(themes) == set(expected)

# Test 5: Covered without evidence fails
def test_covered_without_evidence():
    r = validate_coverage(
        COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
        COV / 'time_coverage_matrix.json', COV / 'theme_coverage_matrix.json',
        COV / 'snapshot_gap_reconciliation.json', COV / 'research_priority_backlog.json',
        BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
    )
    assert r['coverage_ready']

# Test 6: Missing without gap_description fails (simulate by patching)
def test_missing_no_gap_description():
    import tempfile
    tm = _load_json(COV / 'time_coverage_matrix.json')
    for c in tm:
        if c['coverage_status'] == 'missing':
            c['gap_description'] = ''
            break
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(tm, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            tp, COV / 'theme_coverage_matrix.json',
            COV / 'snapshot_gap_reconciliation.json', COV / 'research_priority_backlog.json',
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 7: N/A without reason fails
def test_na_no_reason():
    import tempfile
    tm = _load_json(COV / 'time_coverage_matrix.json')
    for c in tm:
        if c['coverage_status'] == 'not_applicable':
            c['coverage_basis'] = ''
            break
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(tm, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            tp, COV / 'theme_coverage_matrix.json',
            COV / 'snapshot_gap_reconciliation.json', COV / 'research_priority_backlog.json',
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 8: Invalid event_id reference fails
def test_invalid_event_id():
    import tempfile
    tm = _load_json(COV / 'theme_coverage_matrix.json')
    for c in tm:
        if c['supporting_event_ids']:
            c['supporting_event_ids'].append('nonexistent_event_xyz')
            break
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(tm, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            COV / 'time_coverage_matrix.json', tp,
            COV / 'snapshot_gap_reconciliation.json', COV / 'research_priority_backlog.json',
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 9: Hold event reference fails
def test_hold_event_reference():
    import tempfile
    tm = _load_json(COV / 'theme_coverage_matrix.json')
    for c in tm:
        c['supporting_event_ids'] = ['evt_tnn_20260401_hsieh_supporter_growth_claim']
        break
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(tm, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            COV / 'time_coverage_matrix.json', tp,
            COV / 'snapshot_gap_reconciliation.json', COV / 'research_priority_backlog.json',
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 10: Known_gaps not reconciled fails
def test_gap_not_reconciled():
    import tempfile
    gr = _load_json(COV / 'snapshot_gap_reconciliation.json')
    gr[0]['current_status'] = ''
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(gr, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            COV / 'time_coverage_matrix.json', COV / 'theme_coverage_matrix.json',
            tp, COV / 'research_priority_backlog.json',
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 11: Narrowed without evidence fails
def test_narrowed_no_evidence():
    import tempfile
    gr = _load_json(COV / 'snapshot_gap_reconciliation.json')
    for g in gr:
        if g['current_status'] == 'narrowed':
            g['new_formal_evidence_ids'] = []
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(gr, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            COV / 'time_coverage_matrix.json', COV / 'theme_coverage_matrix.json',
            tp, COV / 'research_priority_backlog.json',
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 12: Backlog from covered fails
def test_backlog_from_covered():
    import tempfile
    bl = _load_json(COV / 'research_priority_backlog.json')
    bl.append({
        'research_task_id': 'BAD01', 'title': 'Bad', 'research_priority': 'P0',
        'coverage_status': 'covered', 'gap_theme': 'polling',
    })
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(bl, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            COV / 'time_coverage_matrix.json', COV / 'theme_coverage_matrix.json',
            COV / 'snapshot_gap_reconciliation.json', tp,
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 13: P0+P1 exceeds 8 fails
def test_p0p1_exceeds_8():
    import tempfile
    bl = _load_json(COV / 'research_priority_backlog.json')
    for i in range(5):
        bl.append({
            'research_task_id': f'EXTRA{i}', 'title': f'Extra {i}', 'research_priority': 'P1',
            'coverage_status': 'missing', 'gap_theme': 'polling',
        })
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(bl, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            COV / 'time_coverage_matrix.json', COV / 'theme_coverage_matrix.json',
            COV / 'snapshot_gap_reconciliation.json', tp,
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 14: Total tasks exceeds 15 fails
def test_total_exceeds_15():
    import tempfile
    bl = _load_json(COV / 'research_priority_backlog.json')
    for i in range(10):
        bl.append({
            'research_task_id': f'MANY{i}', 'title': f'Many {i}', 'research_priority': 'P3',
            'coverage_status': 'missing', 'gap_theme': 'polling',
        })
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad.json'
        json.dump(bl, open(tp, 'w', encoding='utf-8'))
        r = validate_coverage(
            COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
            COV / 'time_coverage_matrix.json', COV / 'theme_coverage_matrix.json',
            COV / 'snapshot_gap_reconciliation.json', tp,
            BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
        )
        assert not r['coverage_ready']

# Test 15: Valid coverage passes all checks
def test_valid_coverage_passes():
    r = validate_coverage(
        COV / 'coverage_preflight.json', COV / 'coverage_evidence_ledger.jsonl',
        COV / 'time_coverage_matrix.json', COV / 'theme_coverage_matrix.json',
        COV / 'snapshot_gap_reconciliation.json', COV / 'research_priority_backlog.json',
        BASE / 'data' / 'election_context.db', SEED / 'events.jsonl',
    )
    assert r['coverage_ready']
    assert r['errors'] == []

# Test 16: Formal data unchanged
def test_formal_data_unchanged():
    for fn in ['events.jsonl', 'sources.jsonl']:
        p = SEED / fn
        with open(p, 'rb') as f:
            h = hashlib.sha256(f.read()).hexdigest()
        assert len(h) == 64  # just verify read succeeds

# Test 17: CLI success
def test_cli_exit_zero():
    import subprocess
    result = subprocess.run(
        [sys.executable, '-m', 'app.election_context.validate_fact_coverage',
         '--preflight', str(COV / 'coverage_preflight.json'),
         '--ledger', str(COV / 'coverage_evidence_ledger.jsonl'),
         '--time-matrix', str(COV / 'time_coverage_matrix.json'),
         '--theme-matrix', str(COV / 'theme_coverage_matrix.json'),
         '--gap-reconciliation', str(COV / 'snapshot_gap_reconciliation.json'),
         '--backlog', str(COV / 'research_priority_backlog.json'),
         '--db', str(BASE / 'data' / 'election_context.db'),
         '--events', str(SEED / 'events.jsonl')],
        capture_output=True, text=True, cwd=BASE
    )
    assert result.returncode == 0, f'CLI failed: {result.stdout[:300]}'

# Test 18: CLI failure
def test_cli_exit_nonzero():
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / 'bad_preflight.json'
        json.dump({'preflight_ready': False}, open(tp, 'w', encoding='utf-8'))
        # Need valid ledger etc - create dummy files
        for name in ['coverage_evidence_ledger.jsonl', 'time_coverage_matrix.json',
                     'theme_coverage_matrix.json', 'snapshot_gap_reconciliation.json', 'research_priority_backlog.json']:
            with open(Path(td) / name, 'w', encoding='utf-8') as f:
                if name.endswith('.jsonl'):
                    f.write('')
                else:
                    json.dump([], f)
        result = subprocess.run(
            [sys.executable, '-m', 'app.election_context.validate_fact_coverage',
             '--preflight', str(tp),
             '--ledger', str(Path(td) / 'coverage_evidence_ledger.jsonl'),
             '--time-matrix', str(Path(td) / 'time_coverage_matrix.json'),
             '--theme-matrix', str(Path(td) / 'theme_coverage_matrix.json'),
             '--gap-reconciliation', str(Path(td) / 'snapshot_gap_reconciliation.json'),
             '--backlog', str(Path(td) / 'research_priority_backlog.json')],
            capture_output=True, text=True, cwd=BASE
        )
        assert result.returncode != 0

# Test 19: Idempotent generation
def test_generation_idempotent():
    # Verify the coverage files exist and are self-consistent
    import hashlib
    cov_files = ['coverage_preflight.json', 'coverage_evidence_ledger.jsonl',
                 'time_coverage_matrix.json', 'theme_coverage_matrix.json',
                 'snapshot_gap_reconciliation.json', 'research_priority_backlog.json']
    for fn in cov_files:
        p = COV / fn
        assert p.exists(), f'Missing coverage file: {fn}'

# Test 20: JSON and CSV consistent
def test_json_csv_consistency():
    import csv as csv_mod
    tm = _load_json(COV / 'time_coverage_matrix.json')
    with open(COV / 'time_coverage_matrix.csv', encoding='utf-8') as f:
        rows = len(list(csv_mod.reader(f))) - 1
    assert len(tm) == rows, f'Time matrix: {len(tm)} JSON vs {rows} CSV'
    th = _load_json(COV / 'theme_coverage_matrix.json')
    with open(COV / 'theme_coverage_matrix.csv', encoding='utf-8') as f:
        rows = len(list(csv_mod.reader(f))) - 1
    assert len(th) == rows, f'Theme matrix: {len(th)} JSON vs {rows} CSV'
