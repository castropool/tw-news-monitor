import hashlib
from datetime import datetime
from typing import Any

def make_event_id(articles: list[dict]) -> str:
    raw = '|'.join(sorted(a['url'] for a in articles))
    return 'evt_' + hashlib.sha256(raw.encode()).hexdigest()[:16]

def merge_articles_into_events(matches: list[dict], articles_map: dict[str, dict]) -> list[dict]:
    from collections import defaultdict
    title_groups = defaultdict(list)
    for m in matches:
        article = articles_map.get(m['article_url'], {})
        title = article.get('title', '')
        key = _normalize_title(title)
        title_groups[key].append(m)
    events = []
    for group in title_groups.values():
        if not group:
            continue
        first = group[0]
        article = articles_map.get(first['article_url'], {})
        event = {
            'event_id': make_event_id([{'url': m['article_url']} for m in group]),
            'city': first['city'],
            'event_date': article.get('published_at', ''),
            'event_title': article.get('title', ''),
            'actors': first.get('matched_people', ''),
            'parties': first.get('matched_parties', ''),
            'action': '',
            'issue': first.get('matched_issues', ''),
            'event_type': 'media_report',
            'election_significance': first.get('relevance', 'low'),
            'source_count': len(group),
            'confidence': 'high' if len(group) > 1 else 'medium',
            'sources': [{
                'article_url': m['article_url'],
                'title': articles_map.get(m['article_url'], {}).get('title', ''),
                'source_name': articles_map.get(m['article_url'], {}).get('source_name', ''),
                'published_at': articles_map.get(m['article_url'], {}).get('published_at', ''),
                'url': m['article_url'],
            } for m in group],
        }
        events.append(event)
    return events

def _normalize_title(title: str) -> str:
    import re
    t = re.sub(r'[^\u4e00-\u9fff\w]', '', title)
    return t[:50]
