import sys, os, json, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from app.election_context.repository import ElectionContextRepository
from app.election_context.bootstrap import run_bootstrap, dry_run
from app.election_context.audit import audit_database

SEED = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'election_seed', 'tainan_2026')

def _tmpdb():
    return os.path.join(tempfile.gettempdir(), f'test_ec_{uuid.uuid4().hex}.db')
import tempfile

class TestBootstrap:
    def test_dry_run_valid(self):
        ok = dry_run(SEED)
        assert ok

    def test_first_import_counts(self):
        p = _tmpdb()
        ok, stats = run_bootstrap(SEED, p, reset=True)
        assert ok
        assert stats['elections'] == 1
        assert stats['actors'] == 7
        assert stats['sources'] == 113
        assert stats['events'] == 50
        assert stats['snapshots'] == 5
        assert stats['fts'] == 50
        os.unlink(p)

    def test_second_import_idempotent(self):
        p = _tmpdb()
        ok1, s1 = run_bootstrap(SEED, p, reset=True)
        ok2, s2 = run_bootstrap(SEED, p)
        assert ok1 and ok2
        for k in ['elections','actors','sources','events','snapshots','fts']:
            assert s1[k] == s2[k], f'{k}: {s1[k]} vs {s2[k]}'
        # Verify snapshot statuses
        import sqlite3 as _sc
        _conn = _sc.connect(p)
        _active = _conn.execute("SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'").fetchone()[0]
        _superseded = _conn.execute("SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='superseded'").fetchone()[0]
        assert _active == 1, f'active: {_active}'
        assert _superseded == 4, f'superseded: {_superseded}'
        _conn.close()
        os.unlink(p)

    def test_audit_after_bootstrap(self):
        p = _tmpdb()
        ok, _ = run_bootstrap(SEED, p, reset=True)
        assert ok
        repo = ElectionContextRepository(p)
        repo.connect()
        audit = audit_database(repo)
        assert audit['ok'] is True
        assert len(audit['errors']) == 0
        repo.close()
        os.unlink(p)

    def test_supporting_event_ids_exist(self):
        p = _tmpdb()
        ok, _ = run_bootstrap(SEED, p, reset=True)
        assert ok
        repo = ElectionContextRepository(p)
        repo.connect()
        snap = repo.get_latest_snapshot('TW-2026-TNN-MAYOR')
        assert snap is not None
        for eid in snap.get('supporting_event_ids', []):
            evt = repo.get_event(eid)
            assert evt is not None
        repo.close()
        os.unlink(p)

    def test_primary_result_before_nomination(self):
        p = _tmpdb()
        ok, _ = run_bootstrap(SEED, p, reset=True)
        assert ok
        repo = ElectionContextRepository(p)
        repo.connect()
        pri = repo.conn.execute(
            "SELECT occurred_at FROM election_events WHERE event_type='primary_result'"
        ).fetchone()
        nom = repo.conn.execute(
            "SELECT occurred_at FROM election_events WHERE event_type='party_nomination'"
        ).fetchone()
        assert pri[0] < nom[0]
        repo.close()
        os.unlink(p)


class TestChineseSearch:
    def setup_method(self):
        self.p = _tmpdb()
        ok, _ = run_bootstrap(SEED, self.p, reset=True)
        assert ok
        self.repo = ElectionContextRepository(self.p)
        self.repo.connect()

    def teardown_method(self):
        self.repo.close()
        if os.path.exists(self.p):
            os.unlink(self.p)

    def _search(self, keyword=''):
        return self.repo.search_events(keyword=keyword, election_id='TW-2026-TNN-MAYOR')

    def test_chen_ting_fei(self):
        r = self._search('陈亭妃')
        assert len(r) >= 1

    def test_ting_fei_alias(self):
        r = self._search('亭妃')
        assert len(r) >= 1

    def test_chu_xuan(self):
        r = self._search('初选')
        assert len(r) >= 1

    def test_integration(self):
        r = self._search('党内整合')
        assert len(r) >= 1

    def test_lin_jun_xian(self):
        r = self._search('林俊宪')
        assert len(r) >= 1

    def test_two_keywords(self):
        r = self.repo.search_events(keyword='陈亭妃 初选', election_id='TW-2026-TNN-MAYOR')
        assert len(r) >= 1

    def test_alias_search_expands(self):
        r = self.repo.search_events(keyword='清德', election_id='TW-2026-TNN-MAYOR')
        assert len(r) >= 1

    def test_special_chars_no_crash(self):
        r = self._search('陈亭妃 - 整合')
        assert len(r) >= 0

    def test_special_chars_parentheses(self):
        r = self._search('提名(民进党)')
        assert len(r) >= 0

    def test_empty_query_no_crash(self):
        r = self.repo.search_events(keyword='', election_id='TW-2026-TNN-MAYOR')
        assert len(r) >= 0

    def test_fts_no_result_like_fallback(self):
        r = self._search('赖清德')
        assert len(r) >= 1


class TestSuperseded:
    def setup_method(self):
        self.p = _tmpdb()
        ok, _ = run_bootstrap(SEED, self.p, reset=True)
        assert ok
        self.repo = ElectionContextRepository(self.p)
        self.repo.connect()

    def teardown_method(self):
        self.repo.close()
        if os.path.exists(self.p):
            os.unlink(self.p)

    def test_superseded_excluded(self):
        self.repo.mark_event_superseded('evt_chen_expelled_202407')
        evt = self.repo.get_event('evt_chen_expelled_202407')
        assert evt['fact_status'] == 'superseded'
        r = self.repo.search_events(fact_status='verified')
        assert all(x['fact_status'] != 'superseded' for x in r)
