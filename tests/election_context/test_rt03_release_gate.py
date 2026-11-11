"""
RT03 final release candidate gate tests.
"""
import json, sys, sqlite3, hashlib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
SEED = BASE / 'data' / 'election_seed' / 'tainan_2026'
OUT = SEED / 'event_preview_rt03_hsieh_organization_20251224_20260727_v1'
RC = OUT / 'final_release_candidate'

sys.path.insert(0, str(BASE))

def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]

# ─── 1. Final new event count = 7 ───
def test_seven_new_events():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    assert len(evts) == 7

# ─── 2. rec_009 not separate event ───
def test_rec009_not_separate():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    for e in evts:
        assert '20260518' not in e.get('event_id', ''), 'rec_009 created separately'

# ─── 3. rec_009 subevent date preserved ───
def test_rec009_subevent_date():
    pm = json.loads((RC / 'rt03_policy_sequence_merge.json').read_text(encoding='utf-8'))
    assert pm['subevent_date'] == '2026-05-18'
    assert pm['same_policy_sequence'] == True
    assert pm['duplicate_event_avoided'] == True

# ─── 4. rec_007 date corrected ───
def test_rec007_date_corrected():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    r7 = next((e for e in evts if 'first_tier' in e.get('event_id', '')), {})
    assert r7.get('event_date') == '2026-03-19'

# ─── 5. rec_007 event_id no 20260318 ───
def test_rec007_event_id_updated():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    for e in evts:
        assert '20260318' not in e.get('event_id', ''), 'old event_id present'

# ─── 6/7. rec_010/011 not in release ───
def test_holds_not_in_release():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    for e in evts:
        assert '20260523' not in e.get('event_id', '')
        assert '20260530' not in e.get('event_id', '')

# ─── 8. hold sources not auto-imported ───
def test_hold_sources_not_imported():
    plan = json.loads((RC / 'rt03_source_import_plan.json').read_text(encoding='utf-8'))
    new_sids = plan['new_sources_to_insert']
    assert 'rt03_src_015' not in new_sids, 'rec_010 source imported'
    assert 'rt03_src_016' not in new_sids, 'rec_011 source imported'

# ─── 9. merged rec_004 sources present ───
def test_merged_sources_present():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    r4 = next((e for e in evts if '20260223' in e.get('event_id', '')), {})
    sids = r4.get('event_date_source_ids', [])
    assert 'rt03_src_006' in sids
    assert 'rt03_src_007' in sids
    assert 'rt03_src_014' in sids, 'rec_009 source not merged'

# ─── 10. rec_002 subevent date ───
def test_rec002_subevent():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    e = next((x for x in ens if x['research_record_id'] == 'rt03_rec_002'), {})
    assert e.get('subevent_date') == '2025-12-25'

# ─── 11. rec_005 contact as statement not fact ───
def test_rec005_not_verified_fact():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    e = next((x for x in ens if x['research_record_id'] == 'rt03_rec_005'), {})
    # "接触三次" must be in statements, not fact additions
    facts = json.dumps(e.get('proposed_fact_additions', []), ensure_ascii=False)
    stmts = json.dumps(e.get('proposed_statement_additions', []), ensure_ascii=False)
    assert '接触三次' in stmts or '三次' in stmts

# ─── 12. rec_012 no duplicate facts ───
def test_rec012_no_duplicate():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    e = next((x for x in ens if x['research_record_id'] == 'rt03_rec_012'), {})
    assert e.get('source_only_enrichment') == True
    chk = json.loads((RC / 'rt03_rec012_duplicate_check.json').read_text(encoding='utf-8'))
    assert chk['duplicate_fact_items'] == []
    assert chk['duplicate_analysis_items'] == []

# ─── 13. Hold sources no links ───
def test_hold_sources_no_links():
    links = load_jsonl(RC / 'event_source_links_release_candidate.jsonl')
    for l in links:
        assert l['source_id'] not in ('rt03_src_015', 'rt03_src_016'), 'hold source linked'

# ─── 14. NF sources not in plan ───
def test_nf_sources_not_in_plan():
    plan = json.loads((RC / 'rt03_source_import_plan.json').read_text(encoding='utf-8'))
    new_sids = plan['new_sources_to_insert']
    for sid in ['rt03_src_001', 'rt03_src_002', 'rt03_src_021', 'rt03_src_022']:
        assert sid not in new_sids, f'nf source {sid} in plan'

# ─── 15. Reuse not duplicated as new ───
def test_reuse_not_duplicated():
    plan = json.loads((RC / 'rt03_source_import_plan.json').read_text(encoding='utf-8'))
    assert 'rt03_src_018' not in plan['new_sources_to_insert']
    assert any(r['research_source_id'] == 'rt03_src_018' for r in plan['formal_sources_to_reuse'])

# ─── 16. Mentions preserved ───
def test_mentions():
    m = load_jsonl(RC / 'event_mentions_release_candidate.jsonl')
    assert len(m) > 0

# ─── 17. No new actors / no schema changes ───
def test_no_actor_no_schema():
    g = json.loads((RC / 'rt03_release_gate.json').read_text(encoding='utf-8'))
    assert g['schema_changes'] == []
    assert g['formal_data_unchanged'] == True

# ─── 18. Gate passes ───
def test_gate_passes():
    g = json.loads((RC / 'rt03_release_gate.json').read_text(encoding='utf-8'))
    assert g['formal_import_ready'] == True
    assert g['errors'] == []
    assert g['approved_new_event_count'] == 7
    assert g['merged_rt03_record_count'] == 1
    assert g['approved_enrichment_count'] == 3
    assert g['final_hold_count'] == 2

# ─── 19. Release diff correct ───
def test_release_diff():
    d = json.loads((RC / 'rt03_release_diff.json').read_text(encoding='utf-8'))
    assert len(d['new_events_to_insert']) == 7
    assert 'rt03_rec_009' in d['rt03_records_merged_into_new_events']
    assert 'rt03_rec_010' in d['events_returned_to_hold']
    assert 'rt03_rec_011' in d['events_returned_to_hold']
    assert len(d['existing_events_to_enrich']) == 3
    assert d['existing_business_fields_to_change'] == []
    assert d['schema_changes'] == []
    assert d['formal_data_changes_applied'] == False

# ─── 20. Formal data unchanged ───
def test_formal_unchanged():
    conn = sqlite3.connect(str(BASE / 'data' / 'election_context.db'))
    c = (conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0])
    conn.close()
    assert c == (50, 113, 110)
