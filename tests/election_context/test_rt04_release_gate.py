"""
RT04 final release candidate gate tests.
"""
import json, sys, sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
SEED = BASE / 'data' / 'election_seed' / 'tainan_2026'
OUT = SEED / 'event_preview_rt04_blue_white_cooperation_20260125_20260727_v1'
RC = OUT / 'final_release_candidate'

sys.path.insert(0, str(BASE))

def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]

# ─── 1. Final new events = 3 ───
def test_three_new_events():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    assert len(evts) == 3

# ─── 2. rec005 national framework not local ───
def test_rec005_national_framework():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    r5 = next((e for e in evts if 'kmt_tpp_local_election' in e.get('event_id', '')), {})
    analysis = json.loads(r5.get('analysis', '{}'))
    assert analysis.get('scope', {}).get('event_scope') == 'national_framework'
    assert analysis.get('scope', {}).get('tainan_seat_allocation_not_specified') == True
    assert '全国性' in str(r5.get('limitations', []))

# ─── 3. rec006/008 separate ───
def test_rec006_008_separate():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    ids = [e['event_id'] for e in evts]
    assert 'evt_tnn_20260318_tpp_first_wave_council_nomination' in ids
    assert 'evt_tnn_20260513_tpp_second_wave_council_nomination' in ids

# ─── 4/5. rec007/009 not separate events ───
def test_rec007_009_not_separate():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    for e in evts:
        assert '20260505' not in e.get('event_id', '')
        assert '20260722' not in e.get('event_id', '')

# ─── 6. Enrichment target correct ───
def test_enrichment_target():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    assert len(ens) == 1
    assert ens[0]['formal_event_id'] == 'evt_tnn_20260504_kmt_second_tier_council_nomination'

# ─── 7/8. Subevent dates preserved ───
def test_subevent_dates():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    sd = ens[0].get('subevent_dates', [])
    assert '2026-05-05' in sd
    assert '2026-07-22' in sd

# ─── 9. rec007 provisional not formal ───
def test_rec007_not_formal_concession():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    for fact in ens[0].get('proposed_fact_additions', []):
        if fact.get('subevent_date') == '2026-05-05':
            assert '拟' in fact.get('fact', '') or '方向' in fact.get('fact', '') or '安排' in fact.get('fact', '')
            assert '正式礼让' not in fact.get('fact', '')

# ─── 10. rec009 not resource sharing ───
def test_rec009_no_resource_sharing():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    for fact in ens[0].get('proposed_fact_additions', []):
        assert '共享志工' not in fact.get('fact', '')
        assert '数据库' not in fact.get('fact', '')
        assert '财务' not in fact.get('fact', '')
    assert any('票源' in l for l in ens[0].get('proposed_limitations_additions', []))

# ─── 11/12. Holds not in release ───
def test_holds_not_in_release():
    evts = load_jsonl(RC / 'events_release_candidate.jsonl')
    allowed = {'evt_tnn_20260318_kmt_tpp_local_election_cooperation_agreement',
               'evt_tnn_20260318_tpp_first_wave_council_nomination',
               'evt_tnn_20260513_tpp_second_wave_council_nomination'}
    for e in evts:
        assert e['event_id'] in allowed, f'Unexpected event in release: {e["event_id"]}'
    holds = load_jsonl(RC / 'events_hold_final.jsonl')
    assert len(holds) == 2
    for h in holds:
        assert h['research_record_id'] in ('rt04_rec_002', 'rt04_rec_010')

# ─── 13. rec010 not opened / not joint office ───
def test_rec010_future_hold():
    holds = load_jsonl(RC / 'events_hold_final.jsonl')
    r10 = next((h for h in holds if h['research_record_id'] == 'rt04_rec_010'), {})
    assert r10.get('hold_reason_code') == 'hold_future_event'
    assert '蓝白共同办公室' in r10.get('hold_reason', '')
    assert 'actual_opening_verified=false' in r10.get('hold_reason', '')

# ─── 14/15. Formal evidence reuse only ───
def test_formal_reuse_only():
    fr = load_jsonl(RC / 'formal_evidence_reuse_final.jsonl')
    assert len(fr) == 2
    v = json.loads((RC / 'rt04_formal_evidence_reuse_validation.json').read_text(encoding='utf-8'))
    assert v['reuse_ready'] == True
    assert v['duplicate_enrichment_required'] == False

# ─── 16. No duplicate enrichment for reuse ───
def test_no_dup_enrichment():
    ens = load_jsonl(RC / 'event_enrichment_release_candidate.jsonl')
    for en in ens:
        assert 'rt04_rec_003' not in str(en.get('research_record_id', ''))
        assert 'rt04_rec_004' not in str(en.get('research_record_id', ''))

# ─── 17. Hold sources not imported ───
def test_hold_sources_not_imported():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    new_sids = plan['new_sources_to_insert']
    assert 'rt04_src_001' not in new_sids
    assert 'rt04_src_002' not in new_sids

# ─── 18. Formal reuse sources not imported ───
def test_reuse_sources_not_imported():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    new_sids = plan['new_sources_to_insert']
    assert 'rt04_src_003' not in new_sids
    assert 'rt04_src_004' not in new_sids

# ─── 19. No duplicate links ───
def test_no_duplicate_links():
    links = load_jsonl(RC / 'event_source_links_release_candidate.jsonl')
    keys = [(l['event_id'], l['source_id']) for l in links]
    assert len(keys) == len(set(keys))

# ─── 20. Mentions preserved ───
def test_mentions():
    m = load_jsonl(RC / 'event_mentions_release_candidate.jsonl')
    assert len(m) == 6
    names = [x['mention_name'] for x in m]
    assert any('陈诗薇' in n for n in names)

# ─── 21. Scope check ───
def test_scope_check():
    sc = json.loads((RC / 'rt04_national_framework_scope_check.json').read_text(encoding='utf-8'))
    assert sc['scope_ready'] == True
    assert sc['national_framework_promoted_to_local_fact'] == False

# ─── 22. Coordination sequence ───
def test_coordination_sequence():
    cs = json.loads((RC / 'rt04_first_district_coordination_sequence.json').read_text(encoding='utf-8'))
    assert cs['formal_target_event_id'] == 'evt_tnn_20260504_kmt_second_tier_council_nomination'
    assert set(cs['merged_research_record_ids']) == {'rt04_rec_007', 'rt04_rec_009'}
    assert cs['duplicate_event_avoided'] == True
    assert cs['formal_written_concession_found'] == False

# ─── 23. Release diff ───
def test_release_diff():
    d = json.loads((RC / 'rt04_release_diff.json').read_text(encoding='utf-8'))
    assert len(d['new_events_to_insert']) == 3
    assert set(d['merged_research_records']) == {'rt04_rec_007', 'rt04_rec_009'}
    assert set(d['formal_evidence_records_reused']) == {'rt04_rec_003', 'rt04_rec_004'}
    assert set(d['events_returned_to_hold']) == {'rt04_rec_002', 'rt04_rec_010'}
    assert d['existing_business_fields_to_change'] == []
    assert d['schema_changes'] == []
    assert d['formal_data_changes_applied'] == False

# ─── 24. Gate passes ───
def test_gate():
    g = json.loads((RC / 'rt04_release_gate.json').read_text(encoding='utf-8'))
    assert g['formal_import_ready'] == True
    assert g['errors'] == []
    assert g['approved_new_event_count'] == 3
    assert g['merged_research_record_count'] == 2
    assert g['formal_evidence_reuse_count'] == 2
    assert g['final_hold_count'] == 2
    assert g['scope_semantic_loss'] == False

# ─── 25. Formal data unchanged ───
def test_formal_unchanged():
    conn = sqlite3.connect(str(BASE / 'data' / 'election_context.db'))
    c = (conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0])
    conn.close()
    assert c == (50, 113, 110)
