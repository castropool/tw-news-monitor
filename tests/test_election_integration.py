import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.election_classifier import ElectionClassifier
from app.election_fact_store import ElectionFactStore
from app.election_event_merge import merge_articles_into_events
import tempfile
import os

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'election_watch.yaml'

class TestIntegration:
    def test_classifier_does_not_crash(self):
        c = ElectionClassifier(CONFIG_PATH)
        r = c.classify_article('測試新聞', 'politics', '中央社')
        assert isinstance(r, list)

    def test_fact_store_create_tables(self):
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            store = ElectionFactStore(db_path)
            store.connect()
            store.create_tables()
            assert store.conn is not None
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_fact_store_save_and_read(self):
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            store = ElectionFactStore(db_path)
            store.connect()
            store.create_tables()
            store.save_match(
                article_url='https://example.com/1',
                city='tainan', relevance='high',
                matched_people=['林俊憲'], matched_parties=['民進黨'],
                matched_issues=['選舉'], matched_basis=['election_context'],
            )
            assert store.get_match_count('tainan') == 1
            assert store.get_match_count() == 1
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_scan_state(self):
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            store = ElectionFactStore(db_path)
            store.connect()
            store.create_tables()
            store.set_scan_state('last_scanned_article_id', '42')
            state = store.get_scan_state()
            assert state['last_scanned_article_id'] == '42'
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_event_merge_different_cities(self):
        matches = [
            {'article_url': 'https://tn/1', 'city': 'tainan', 'relevance': 'high',
             'matched_people': '林俊憲', 'matched_parties': '', 'matched_issues': '',
             'matched_basis': ['election_context']},
            {'article_url': 'https://nt/1', 'city': 'new_taipei', 'relevance': 'high',
             'matched_people': '蘇巧慧', 'matched_parties': '', 'matched_issues': '',
             'matched_basis': ['election_context']},
        ]
        amap = {
            'https://tn/1': {'title': '林俊憲參選', 'source_name': '中央社',
                             'published_at': '2026-07-26', 'url': 'https://tn/1'},
            'https://nt/1': {'title': '蘇巧慧參選', 'source_name': '聯合報',
                             'published_at': '2026-07-26', 'url': 'https://nt/1'},
        }
        events = merge_articles_into_events(matches, amap)
        assert len(events) == 2

    def test_classifier_main_flow_unaffected(self):
        c = ElectionClassifier(CONFIG_PATH)
        r = c.classify_article('颱風逼近 台南嚴陣以待', 'politics', '中央社')
        assert len(r) == 0

    def test_report_period_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            store = ElectionFactStore(db_path)
            store.connect()
            store.create_tables()
            assert not store.is_report_generated('2026-07-26')
            store.conn.execute(
                "INSERT INTO report_runs (report_period, cutoff_time, generated_at) VALUES (?,?,?)",
                ('2026-07-26', '2026-07-26T00:00:00', '2026-07-26T00:00:00')
            )
            store.conn.commit()
            assert store.is_report_generated('2026-07-26')
            assert not store.is_report_generated('2026-07-27')
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
