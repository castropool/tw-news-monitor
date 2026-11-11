import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.election_event_merge import merge_articles_into_events, make_event_id, _normalize_title

class TestEventMerge:
    def test_same_title_merged(self):
        matches = [
            {'article_url': 'https://example.com/1', 'city': 'tainan', 'relevance': 'high',
             'matched_people': '林俊憲', 'matched_parties': '民進黨', 'matched_issues': '選舉',
             'matched_basis': ['election_context']},
            {'article_url': 'https://example.com/2', 'city': 'tainan', 'relevance': 'high',
             'matched_people': '林俊憲', 'matched_parties': '民進黨', 'matched_issues': '選舉',
             'matched_basis': ['election_context']},
        ]
        articles_map = {
            'https://example.com/1': {'title': '林俊憲宣布參選台南市長', 'source_name': '中央社',
                                      'published_at': '2026-07-26', 'url': 'https://example.com/1'},
            'https://example.com/2': {'title': '林俊憲宣布參選台南市長', 'source_name': '聯合報',
                                      'published_at': '2026-07-26', 'url': 'https://example.com/2'},
        }
        events = merge_articles_into_events(matches, articles_map)
        assert len(events) == 1
        assert events[0]['source_count'] == 2

    def test_different_titles_not_merged(self):
        matches = [
            {'article_url': 'https://example.com/a', 'city': 'tainan', 'relevance': 'high',
             'matched_people': '林俊憲', 'matched_parties': '', 'matched_issues': '',
             'matched_basis': ['election_context']},
            {'article_url': 'https://example.com/b', 'city': 'tainan', 'relevance': 'high',
             'matched_people': '謝龍介', 'matched_parties': '', 'matched_issues': '',
             'matched_basis': ['election_context']},
        ]
        articles_map = {
            'https://example.com/a': {'title': '林俊憲參選記者會', 'source_name': '中央社',
                                      'published_at': '2026-07-26', 'url': 'https://example.com/a'},
            'https://example.com/b': {'title': '謝龍介批市政缺失', 'source_name': '聯合報',
                                      'published_at': '2026-07-26', 'url': 'https://example.com/b'},
        }
        events = merge_articles_into_events(matches, articles_map)
        assert len(events) == 2

    def test_all_sources_retained(self):
        matches = [
            {'article_url': f'https://example.com/{i}', 'city': 'new_taipei', 'relevance': 'high',
             'matched_people': '蘇巧慧', 'matched_parties': '民進黨', 'matched_issues': '',
             'matched_basis': ['election_context']}
            for i in range(3)
        ]
        articles_map = {f'https://example.com/{i}': {
            'title': '蘇巧慧新北參選', 'source_name': f'來源{i}',
            'published_at': '2026-07-26', 'url': f'https://example.com/{i}'
        } for i in range(3)}
        events = merge_articles_into_events(matches, articles_map)
        assert len(events) == 1
        assert len(events[0]['sources']) == 3


class TestEventIdempotency:
    def test_same_inputs_same_id(self):
        matches = [{'article_url': 'https://example.com/x', 'city': 'tainan', 'relevance': 'high',
                     'matched_people': '', 'matched_parties': '', 'matched_issues': '',
                     'matched_basis': ['election_context']}]
        amap = {'https://example.com/x': {'title': '測試', 'source_name': '中央社',
                                           'published_at': '', 'url': 'https://example.com/x'}}
        e1 = merge_articles_into_events(matches, amap)
        e2 = merge_articles_into_events(matches, amap)
        assert e1[0]['event_id'] == e2[0]['event_id']


class TestNormalize:
    def test_normalize_strips_punctuation(self):
        result = _normalize_title('林俊憲：參選！台南市長？')
        assert '參選' in result
        assert '台南' in result

    def test_normalize_short(self):
        assert len(_normalize_title('A' * 100)) <= 50
