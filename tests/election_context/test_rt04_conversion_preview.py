"""
RT04 conversion preview tests.
"""
import json, sys, sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
SEED = BASE / 'data' / 'election_seed' / 'tainan_2026'
OUT = SEED / 'event_preview_rt04_blue_white_cooperation_20260125_20260727_v1'

sys.path.insert(0, str(BASE))

def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]

# ─── 1. 14 records ───
def test_14_records():
    m = json.loads((OUT / 'input_manifest.json').read_text(encoding='utf-8'))
    assert m['research_record_count'] == 14
    assert m['candidate_event_count'] == 7
    assert m['formal_evidence_baseline_count'] == 2
    assert m['baseline_reference_count'] == 1
    assert m['negative_finding_count'] == 4
    assert m['input_ready'] == True

# ─── 2. 13 sources ───
def test_13_sources():
    m = json.loads((OUT / 'input_manifest.json').read_text(encoding='utf-8'))
    assert m['source_record_count'] == 13

# ─── 3. Candidate disposition sums to 7 ───
def test_disposition_7():
    d = json.loads((OUT / 'event_disposition_report.json').read_text(encoding='utf-8'))
    ce = d['candidate_events']
    assert sum(ce.values()) == 7

# ─── 4. Formal evidence baseline reuse only ───
def test_formal_evidence_reuse():
    fr = load_jsonl(OUT / 'formal_evidence_reuse.jsonl')
    assert len(fr) == 2
    r3 = next((r for r in fr if r['research_record_id'] == 'rt04_rec_003'), {})
    r4 = next((r for r in fr if r['research_record_id'] == 'rt04_rec_004'), {})
    assert 'evt_tnn_20260128_hsieh_kmt_tpp_proposal' in r3['formal_event_ids']
    assert 'evt_tnn_20260307_hsieh_ko_bluewhite_xinying_sweep' in r4['formal_event_ids']

# ─── 5. Formal evidence baseline not in preview ───
def test_formal_reuse_not_in_preview():
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert 'evt_tnn_20260128_hsieh_kmt_tpp_proposal' not in e.get('event_id', '')
        assert 'evt_tnn_20260307_hsieh_ko_bluewhite_xinying_sweep' not in e.get('event_id', '')

# ─── 6. Baseline not in preview ───
def test_baseline_not_in_preview():
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert 'rt04_rec_001' not in str(e.get('event_id', ''))

# ─── 7. NF not in preview ───
def test_nf_not_in_preview():
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert 'nf_' not in str(e.get('event_id', ''))

# ─── 8. rec_002 not mapped to party_nomination ───
def test_rec002_not_formal_nomination():
    tm = json.loads((OUT / 'event_type_mapping_report.json').read_text(encoding='utf-8'))
    r2 = next((t for t in tm if t['research_record_id'] == 'rt04_rec_002'), {})
    assert r2.get('selected_formal_type') != 'party_nomination'
    assert r2.get('final_mapping_status') == 'hold_no_safe_type'

# ─── 9. rec_005 national scope ───
def test_rec005_national_scope():
    tm = json.loads((OUT / 'event_type_mapping_report.json').read_text(encoding='utf-8'))
    r5 = next((t for t in tm if t['research_record_id'] == 'rt04_rec_005'), {})
    assert r5.get('selected_formal_type') == 'alliance_agreement'
    assert r5.get('final_mapping_status') == 'mapped'

# ─── 10. rec_010 future event ───
def test_rec010_future_event_hold():
    holds = load_jsonl(OUT / 'events_hold.jsonl')
    r10 = next((h for h in holds if h['research_record_id'] == 'rt04_rec_010'), {})
    assert r10.get('hold_reason_code') == 'hold_future_event'
    assert r10.get('planned_event_date') == '2026-08-01'
    assert r10.get('announcement_date') == '2026-07-21'

# ─── 11. No future event in preview ───
def test_no_future_in_preview():
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert '20260801' not in e.get('event_id', '')

# ─── 12. rec_006/008 as party_nomination ───
def test_rec006_008_nomination():
    tm = json.loads((OUT / 'event_type_mapping_report.json').read_text(encoding='utf-8'))
    for rid in ['rt04_rec_006', 'rt04_rec_008']:
        r = next((t for t in tm if t['research_record_id'] == rid), {})
        assert r.get('selected_formal_type') == 'party_nomination'

# ─── 13. rec_009 not written as resource sharing ───
def test_rec009_limitations():
    holds = load_jsonl(OUT / 'events_hold.jsonl')
    r9 = next((h for h in holds if h['research_record_id'] == 'rt04_rec_009'), {})
    reason = r9.get('hold_reason', '')
    assert '票源' in reason or '搭配' in reason or 'no safe' in reason.lower() or '候选' in reason

# ─── 14. Source dedup correct ───
def test_source_dedup():
    d = json.loads((OUT / 'source_dedup_report.json').read_text(encoding='utf-8'))
    assert d['research_source_count'] == 13
    assert len(d['new_source_candidates']) == 10
    assert len(d['reused_sources']) == 3
    assert d['source_conflicts'] == []
    # specific reuse relationships
    reuse_map = json.loads((OUT / 'source_reuse_mapping.json').read_text(encoding='utf-8'))
    rm = {r['research_source_id']: r['formal_source_id'] for r in reuse_map}
    assert rm.get('rt04_src_001') == 'src_66f698790614'
    assert rm.get('rt04_src_003') == 'rt03_src_006'
    assert rm.get('rt04_src_004') == 'rt03_src_009'

# ─── 15. Mentions preserved ───
def test_mentions():
    mentions = load_jsonl(OUT / 'event_mentions_preview.jsonl')
    rec = json.loads((OUT / 'event_mention_reconciliation.json').read_text(encoding='utf-8'))
    assert rec['lost_mentions'] == []
    assert rec['actor_records_created'] == []

# ─── 16. Gate passes ───
def test_gate():
    g = json.loads((OUT / 'preview_release_validation.json').read_text(encoding='utf-8'))
    assert g['preview_ready'] == True
    assert g['errors'] == []
    assert g['future_event_boundary_ready'] == True
    assert g['formal_evidence_reuse_ready'] == True

# ─── 17. Fidelity ready ───
def test_fidelity():
    f = json.loads((OUT / 'conversion_fidelity.json').read_text(encoding='utf-8'))
    assert f['fidelity_ready'] == True
    assert f['formal_data_unchanged'] == True
    assert f['future_event_promoted_to_completed_fact'] == False

# ─── 18. Idempotency file ───
def test_idempotency():
    p = OUT / 'conversion_idempotency.json'
    assert p.exists()
    d = json.loads(p.read_text(encoding='utf-8'))
    assert d['idempotent'] == True

# ─── 19. Formal data unchanged ───
def test_formal_unchanged():
    conn = sqlite3.connect(str(BASE / 'data' / 'election_context.db'))
    c = (conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0])
    conn.close()
    assert c == (50, 113, 110)

# ─── 20. Preview types valid ───
def test_preview_types_valid():
    from app.election_context import EVENT_TYPES
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert e['event_type'] in EVENT_TYPES
