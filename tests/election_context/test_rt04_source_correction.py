"""
RT04 source plan correction tests.
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

def _formal_source_ids():
    return set(s['source_id'] for s in load_jsonl(SEED / 'sources.jsonl'))

# ─── 1. Enrichment sources present in release plan ───
def test_enrichment_sources_in_plan():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    new_sids = set(plan['new_sources_to_insert'])
    assert 'rt04_src_009' in new_sids, 'rt04_src_009 missing'
    assert 'rt04_src_012' in new_sids, 'rt04_src_012 missing'
    assert 'rt04_src_013' in new_sids, 'rt04_src_013 missing'

# ─── 2. New source count = 9 (includes enrichment) ───
def test_new_source_count_9():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    assert plan['new_source_count'] == 9
    assert plan['new_source_count'] == len(plan['new_sources_to_insert'])

# ─── 3. sources_release_candidate has 9 rows ───
def test_release_candidate_9():
    srcs = load_jsonl(RC / 'sources_release_candidate.jsonl')
    assert len(srcs) == 9
    sids = set(s['source_id'] for s in srcs)
    assert {'rt04_src_009', 'rt04_src_012', 'rt04_src_013'} <= sids

# ─── 4. Release source set == new link source set (intended pre-import scope) ───
def test_release_set_matches_links():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    links = load_jsonl(RC / 'event_source_links_release_candidate.jsonl')
    release_ids = set(plan['new_sources_to_insert'])
    # All release candidate sources must be referenced by links (as new sources in the plan scope)
    link_ids = set(l['source_id'] for l in links)
    release_used = release_ids & link_ids
    # Every plan new source must appear in at least one link
    assert release_ids <= link_ids, f'Plan sources not in links: {release_ids - link_ids}'
    # Every link source is either a plan new source or a formal source
    formal_ids = _formal_source_ids()
    for l in links:
        assert l['source_id'] in formal_ids or l['source_id'] in release_ids, f'unmapped link source: {l["source_id"]}'

# ─── 5. All link sources accounted ───
def test_all_link_sources_accounted():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    links = load_jsonl(RC / 'event_source_links_release_candidate.jsonl')
    formal_ids = _formal_source_ids()
    release_ids = set(plan['new_sources_to_insert'])
    for l in links:
        assert l['source_id'] in formal_ids or l['source_id'] in release_ids, f'orphan link source: {l["source_id"]}'

# ─── 6. No orphan links ───
def test_no_orphan_links():
    links = load_jsonl(RC / 'event_source_links_release_candidate.jsonl')
    srcs = set(s['source_id'] for s in load_jsonl(RC / 'sources_release_candidate.jsonl'))
    formal_ids = _formal_source_ids()
    for l in links:
        assert l['source_id'] in formal_ids or l['source_id'] in srcs

# ─── 7. Enrichment links = 3 ───
def test_enrichment_links_3():
    rec = json.loads((RC / 'rt04_event_source_link_reconciliation.json').read_text(encoding='utf-8'))
    assert len(rec['enrichment_links']) == 3
    assert len(rec['new_event_links']) == 6
    assert len(rec['new_links_to_insert']) == 9

# ─── 8. No formal reuse counted as new ───
def test_no_reuse_in_new():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    new_sids = set(plan['new_sources_to_insert'])
    assert 'rt04_src_001' not in new_sids
    assert 'rt04_src_003' not in new_sids
    assert 'rt04_src_004' not in new_sids

# ─── 9. Hold sources excluded ───
def test_hold_sources_excluded():
    plan = json.loads((RC / 'rt04_source_import_plan.json').read_text(encoding='utf-8'))
    new_sids = set(plan['new_sources_to_insert'])
    assert 'rt04_src_002' not in new_sids

# ─── 10. Gate passes with corrected counts ───
def test_gate_corrected():
    g = json.loads((RC / 'rt04_release_gate.json').read_text(encoding='utf-8'))
    assert g['formal_import_ready'] == True
    assert g['new_source_count'] == 9
    assert g['reused_source_count'] == 0
    assert g['new_link_count'] == 9
    assert g['all_new_link_sources_accounted'] == True
    assert g['release_source_set_matches_new_link_source_set'] == True
    assert g['missing_release_sources'] == []

# ─── 11. Link count consistency with links file ───
def test_link_counts_consistent():
    links = load_jsonl(RC / 'event_source_links_release_candidate.jsonl')
    g = json.loads((RC / 'rt04_release_gate.json').read_text(encoding='utf-8'))
    assert len(links) == g['new_link_count'] == 9

# ─── 12. Formal data unchanged ───
def test_formal_unchanged():
    conn = sqlite3.connect(str(BASE / 'data' / 'election_context.db'))
    c = (conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
         conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0])
    conn.close()
    assert c == (50, 113, 110)
