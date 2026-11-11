"""
RT03 conversion preview tests.
"""
import json, sys, hashlib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
SEED = BASE / 'data' / 'election_seed' / 'tainan_2026'
OUT = SEED / 'event_preview_rt03_hsieh_organization_20251224_20260727_v1'

sys.path.insert(0, str(BASE))

def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]

# ─── 1. 18 records parsed ───
def test_18_records():
    m = json.loads((OUT / 'input_manifest.json').read_text(encoding='utf-8'))
    assert m['research_record_count'] == 18
    assert m['candidate_event_count'] == 11
    assert m['existing_event_enrichment_count'] == 2
    assert m['baseline_reference_count'] == 1
    assert m['negative_finding_count'] == 4

# ─── 2. 22 sources parsed ───
def test_22_sources():
    m = json.loads((OUT / 'input_manifest.json').read_text(encoding='utf-8'))
    assert m['source_record_count'] == 22

# ─── 3. Candidate disposition sums to 11 ───
def test_candidate_disposition_11():
    d = json.loads((OUT / 'event_disposition_report.json').read_text(encoding='utf-8'))
    ce = d['candidate_events']
    assert sum(ce.values()) == 11

# ─── 4. Baseline not in preview ───
def test_baseline_not_in_preview():
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert 'rt03_rec_001' not in str(e.get('event_id', '')), 'baseline in preview'

# ─── 5. Negative findings not in preview ───
def test_nf_not_in_preview():
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert 'nf_' not in str(e.get('event_id', '')), 'negative finding in preview'

# ─── 6. rec_002 enrichment target exists ───
def test_rec002_enrichment_target():
    enrich = load_jsonl(OUT / 'existing_event_enrichment_preview.jsonl')
    r = next((e for e in enrich if e['research_record_id'] == 'rt03_rec_002'), None)
    assert r, 'rec_002 enrichment missing'
    assert r['formal_event_id'] == 'evt_tnn_20251224_hsieh_kmt_nomination'
    assert r.get('subevent_date') == '2025-12-25'

# ─── 7. rec_005 enrichment target exists ───
def test_rec005_enrichment_target():
    enrich = load_jsonl(OUT / 'existing_event_enrichment_preview.jsonl')
    r = next((e for e in enrich if e['research_record_id'] == 'rt03_rec_005'), None)
    assert r, 'rec_005 enrichment missing'
    assert r['formal_event_id'] == 'evt_tnn_20260128_hsieh_kmt_tpp_proposal'
    assert r.get('subevent_date') == '2026-02-23'

# ─── 8. rec_007 date conflict flagged ───
def test_rec007_date_conflict():
    dates = json.loads((OUT / 'event_date_basis_report.json').read_text(encoding='utf-8'))
    r = next((d for d in dates if d['research_record_id'] == 'rt03_rec_007'), {})
    assert r.get('date_conflicts'), 'rec_007 date conflict not flagged'

# ─── 9. All preview event types in EVENT_TYPES ───
def test_preview_types_valid():
    from app.election_context import EVENT_TYPES
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        assert e['event_type'] in EVENT_TYPES, f"Invalid type {e['event_type']}"

# ─── 10. Media interpretations not promoted to fact_summary ───
def test_no_claim_in_fact_summary():
    preview = load_jsonl(OUT / 'events_preview.jsonl')
    for e in preview:
        fs = e.get('fact_summary', '')
        analysis = json.loads(e.get('analysis', '{}'))
        # Media interpretations must not be copied verbatim into fact_summary
        for mi in analysis.get('media_interpretations', []):
            text = mi.get('interpretation', '') if isinstance(mi, dict) else str(mi)
            assert text not in fs, f'media interpretation promoted to fact: {e["event_id"]}'
        # Candidate claims must not be copied verbatim into fact_summary
        for c in analysis.get('research_claims', []):
            claim = c.get('claim', '')
            if claim:
                assert claim not in fs, f'candidate claim promoted to fact: {e["event_id"]}'

# ─── 11. Reused sources not in new source preview ───
def test_reused_not_in_new_sources():
    srcs = load_jsonl(OUT / 'event_sources_preview.jsonl')
    sids = [s['source_id'] for s in srcs]
    assert 'rt03_src_018' not in sids, 'reused source in new sources'
    assert 'rt03_src_022' not in sids, 'reused source in new sources'

# ─── 12. Source dedup report correct ───
def test_source_dedup():
    d = json.loads((OUT / 'source_dedup_report.json').read_text(encoding='utf-8'))
    assert d['research_source_count'] == 22
    assert len(d['new_source_candidates']) == 20
    assert len(d['reused_sources']) == 2
    assert d['source_conflicts'] == []

# ─── 13. Mentions preserved ───
def test_mentions():
    mentions = load_jsonl(OUT / 'event_mentions_preview.jsonl')
    assert len(mentions) == 16
    rec = json.loads((OUT / 'event_mention_reconciliation.json').read_text(encoding='utf-8'))
    assert rec['lost_mentions'] == []
    assert rec['actor_records_created'] == []

# ─── 14. Gate passes ───
def test_gate():
    g = json.loads((OUT / 'preview_release_validation.json').read_text(encoding='utf-8'))
    assert g['preview_ready'] == True
    assert g['errors'] == []

# ─── 15. Conversion fidelity ready ───
def test_fidelity():
    f = json.loads((OUT / 'conversion_fidelity.json').read_text(encoding='utf-8'))
    assert f['fidelity_ready'] == True
    assert f['formal_data_unchanged'] == True

# ─── 16. Idempotency file exists ───
def test_idempotency():
    p = OUT / 'conversion_idempotency.json'
    assert p.exists()
    d = json.loads(p.read_text(encoding='utf-8'))
    assert d['idempotent'] == True

# ─── 17. Formal data unchanged ───
def test_formal_unchanged():
    import sqlite3
    conn = sqlite3.connect(str(BASE / 'data' / 'election_context.db'))
    c = {
        'events': conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0],
        'sources': conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
        'links': conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0],
    }
    conn.close()
    assert c == {'events': 50, 'sources': 113, 'links': 110}

# ─── 18. rec_012 enriches existing fundraiser ───
def test_rec012_enrich_fundraiser():
    enrich = load_jsonl(OUT / 'existing_event_enrichment_preview.jsonl')
    r = next((e for e in enrich if e['research_record_id'] == 'rt03_rec_012'), None)
    assert r and r['formal_event_id'] == 'evt_tnn_20260605_hsieh_north_fundraiser'

# ─── 19. No source conflicts ───
def test_no_source_conflicts():
    d = json.loads((OUT / 'source_dedup_report.json').read_text(encoding='utf-8'))
    assert d['source_dedup_ready'] == True

# ─── 20. Input manifest ready ───
def test_input_manifest_ready():
    m = json.loads((OUT / 'input_manifest.json').read_text(encoding='utf-8'))
    assert m['input_ready'] == True
    assert m['duplicate_record_ids'] == []
    assert m['duplicate_source_ids'] == []
