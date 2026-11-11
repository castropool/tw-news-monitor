"""
RT02 seed reconciliation tests.
"""
import json, sqlite3, hashlib, sys, tempfile, shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
SEED = BASE / 'data' / 'election_seed' / 'tainan_2026'
OUT = SEED / 'rt02_seed_reconciliation_v1'

sys.path.insert(0, str(BASE))
from app.election_context.bootstrap import run_bootstrap

def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]

def _tmpdb():
    return Path(tempfile.gettempdir()) / f'rt02_sr_test_{hashlib.md5(str(__import__("uuid").uuid4()).encode()).hexdigest()[:8]}.db'

def _rebuild(dbpath):
    """Rebuild a DB from the formal seed."""
    if Path(dbpath).exists(): Path(dbpath).unlink()
    ok, stats = run_bootstrap(str(SEED), str(dbpath), reset=True)
    return ok, stats

# ─── 1. Production DB has enrichment but seed lacks → fail ───
def test_db_enrichment_missing_from_seed_fails():
    # Load seed target events
    events = load_jsonl(SEED / 'events.jsonl')
    seed_enrich = set()
    for e in events:
        if e['event_id'] in ('evt_tnn_20260120_dpp_caucus_conflict', 'evt_tnn_20260720_dpp_tainan_team_campaign_photos'):
            aj = e.get('analysis_json', '{}')
            a = json.loads(aj) if isinstance(aj, str) else aj
            for k in a:
                if 'enrich' in k: seed_enrich.add(k)
    # After fix, seed should contain all 3 markers
    assert {'enrich_002', 'enrich_009', 'enrich_010'} <= seed_enrich, \
        f'Seed missing enrichment: {seed_enrich}'

# ─── 2. Seed rebuild links == current formal baseline ───
def test_seed_rebuild_links_75():
    dbp = _tmpdb()
    ok, _ = _rebuild(dbp)
    conn = sqlite3.connect(str(dbp))
    links = conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0]
    conn.close()
    Path(dbp).unlink(missing_ok=True)
    assert links == 110, f'Expected 110 links from seed rebuild, got {links}'

# ─── 3. Enrichment source_ids present after rebuild ───
def test_enrichment_source_ids_after_rebuild():
    dbp = _tmpdb()
    _rebuild(dbp)
    conn = sqlite3.connect(str(dbp))
    pairs = set(conn.execute('SELECT event_id, source_id FROM event_sources').fetchall())
    conn.close()
    Path(dbp).unlink(missing_ok=True)
    expected = {
        ('evt_tnn_20260120_dpp_caucus_conflict', 'rt02_src_002'),
        ('evt_tnn_20260120_dpp_caucus_conflict', 'rt02_src_003'),
        ('evt_tnn_20260720_dpp_tainan_team_campaign_photos', 'rt02_src_013'),
        ('evt_tnn_20260720_dpp_tainan_team_campaign_photos', 'rt02_src_015'),
    }
    assert expected <= pairs, f'Missing enrichment links: {expected - pairs}'

# ─── 4. Enrichment analysis entries present after rebuild ───
def test_enrichment_analysis_after_rebuild():
    dbp = _tmpdb()
    _rebuild(dbp)
    conn = sqlite3.connect(str(dbp))
    conn.row_factory = sqlite3.Row
    markers = set()
    for eid in ['evt_tnn_20260120_dpp_caucus_conflict', 'evt_tnn_20260720_dpp_tainan_team_campaign_photos']:
        row = conn.execute('SELECT analysis_json FROM election_events WHERE event_id=?', (eid,)).fetchone()
        a = json.loads(row['analysis_json']) if row['analysis_json'] else {}
        for k in a:
            if 'enrich' in k: markers.add(k)
    conn.close()
    Path(dbp).unlink(missing_ok=True)
    assert {'enrich_002', 'enrich_009', 'enrich_010'} <= markers, f'Missing: {markers}'

# ─── 5. subevent_date=2026-07-09 present ───
def test_subevent_date_preserved():
    dbp = _tmpdb()
    _rebuild(dbp)
    conn = sqlite3.connect(str(dbp))
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT analysis_json FROM election_events WHERE event_id=?', ('evt_tnn_20260720_dpp_tainan_team_campaign_photos',)).fetchone()
    a = json.loads(row['analysis_json']) if row['analysis_json'] else {}
    conn.close()
    Path(dbp).unlink(missing_ok=True)
    assert a.get('enrich_009', {}).get('subevent_date') == '2026-07-09', 'subevent_date missing'

# ─── 6. No duplicate links after 2nd bootstrap ───
def test_no_duplicate_links_after_second_bootstrap():
    dbp = _tmpdb()
    _rebuild(dbp)
    _rebuild(dbp)  # idempotent second run
    conn = sqlite3.connect(str(dbp))
    dup = conn.execute('SELECT event_id, source_id, COUNT(*) c FROM event_sources GROUP BY event_id, source_id HAVING c > 1').fetchall()
    conn.close()
    Path(dbp).unlink(missing_ok=True)
    assert len(dup) == 0, f'Duplicate links: {dup}'

# ─── 7. No duplicate analysis entries after 2nd bootstrap ───
def test_no_duplicate_analysis_after_second_bootstrap():
    dbp = _tmpdb()
    _rebuild(dbp)
    _rebuild(dbp)
    conn = sqlite3.connect(str(dbp))
    conn.row_factory = sqlite3.Row
    for eid in ['evt_tnn_20260120_dpp_caucus_conflict', 'evt_tnn_20260720_dpp_tainan_team_campaign_photos']:
        row = conn.execute('SELECT analysis_json FROM election_events WHERE event_id=?', (eid,)).fetchone()
        a = json.loads(row['analysis_json']) if row['analysis_json'] else {}
        enrich_keys = [k for k in a.keys() if 'enrich' in k]
        assert len(enrich_keys) == len(set(enrich_keys)), f'Duplicate enrich keys in {eid}'
    conn.close()
    Path(dbp).unlink(missing_ok=True)

# ─── 8. All event-source pairs present in rebuilt DB ───
def test_75_pairs_present():
    dbp = _tmpdb()
    _rebuild(dbp)
    conn = sqlite3.connect(str(dbp))
    pairs = set(conn.execute('SELECT event_id, source_id FROM event_sources').fetchall())
    conn.close()
    Path(dbp).unlink(missing_ok=True)
    assert len(pairs) == 110, f'Expected 110 pairs, got {len(pairs)}'

# ─── 9. Target event business fields unchanged after rebuild ───
def test_target_event_business_fields_unchanged():
    events = load_jsonl(SEED / 'events.jsonl')
    seed_map = {e['event_id']: e for e in events}
    for eid in ['evt_tnn_20260120_dpp_caucus_conflict', 'evt_tnn_20260720_dpp_tainan_team_campaign_photos']:
        e = seed_map[eid]
        assert e['event_id'] == eid
        assert e['event_type'] in ('faction_conflict', 'campaign_launch', 'governance_event', 'joint_campaign')
        assert e['fact_status'] in ('verified', 'multi_source_verified', 'pending_verification')

# ─── 10. Non-target events unchanged ───
def test_non_target_events_unchanged():
    events = load_jsonl(SEED / 'events.jsonl')
    target = {'evt_tnn_20260120_dpp_caucus_conflict', 'evt_tnn_20260720_dpp_tainan_team_campaign_photos'}
    others = [e for e in events if e['event_id'] not in target]
    assert len(others) == 48, f'Expected 48 non-target events, got {len(others)}'

# ─── 11. Equivalence report exists and ready ───
def test_equivalence_report_ready():
    p = OUT / 'rt02_seed_db_equivalence.json'
    assert p.exists(), 'Equivalence report missing'
    eq = json.loads(p.read_text(encoding='utf-8'))
    assert eq['equivalence_ready'] == True
    assert eq['event_source_pairs_equal'] == True

# ─── 12. Idempotency report exists ───
def test_idempotency_report():
    p = OUT / 'rt02_seed_bootstrap_idempotency.json'
    assert p.exists()
    idem = json.loads(p.read_text(encoding='utf-8'))
    assert idem['bootstrap_idempotent'] == True

# ─── 13. Reconciliation result ready ───
def test_reconciliation_result():
    p = OUT / 'rt02_seed_reconciliation_result.json'
    assert p.exists()
    r = json.loads(p.read_text(encoding='utf-8'))
    assert r['reconciliation_ready'] == True
    assert r['missing_seed_links_after'] == []
    assert r['missing_seed_enrichments_after'] == []
    assert r['production_db_changed'] == False

# ─── 14. Production DB not modified ───
def test_production_db_unchanged():
    import sqlite3 as _sc
    conn = _sc.connect(str(BASE / 'data' / 'election_context.db'))
    c = {
        'events': conn.execute('SELECT COUNT(*) FROM election_events').fetchone()[0],
        'sources': conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
        'links': conn.execute('SELECT COUNT(*) FROM event_sources').fetchone()[0],
    }
    conn.close()
    assert c == {'events': 50, 'sources': 113, 'links': 110}

# ─── 15. Preflight report exists ───
def test_preflight_report():
    p = OUT / 'rt02_seed_reconciliation_preflight.json'
    assert p.exists()
    pf = json.loads(p.read_text(encoding='utf-8'))
    assert pf['missing_links_match_expected'] == True
