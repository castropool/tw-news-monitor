import argparse
import json
import logging
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.election_classifier import ElectionClassifier
from app.election_fact_store import ElectionFactStore
from app.election_event_merge import merge_articles_into_events
from app.election_utils import format_taipei_now
from app.settings import get_settings, load_environment

TAIPEI = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config' / 'election_watch.yaml'
DB_PATH = PROJECT_ROOT / 'data' / 'election_watch.db'

def load_news_db(db_path: str | Path):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def scan_new_articles(store: ElectionFactStore, classifier: ElectionClassifier,
                      news_conn, since_id: int | None = None):
    if since_id is None:
        since_id = 0
    rows = news_conn.execute(
        'SELECT rowid, source_id, source_name, category, title, url, published_at, fetched_at '
        'FROM articles WHERE rowid > ? ORDER BY rowid', (since_id,)
    ).fetchall()
    matched = []
    articles_map = {}
    for row in rows:
        article = dict(row)
        articles_map[article['url']] = article
        results = classifier.classify_article(
            article['title'], article['category'], article['source_name']
        )
        for r in results:
            store.save_match(
                article_url=article['url'],
                city=r['city'],
                relevance=r['relevance'],
                matched_people=r.get('matched_people', []),
                matched_parties=r.get('matched_parties', []),
                matched_issues=r.get('matched_issues', []),
                matched_basis=r.get('matched_basis', []),
            )
            matched.append({
                'article_url': article['url'],
                'city': r['city'],
                'relevance': r['relevance'],
                'matched_people': r.get('matched_people', []),
                'matched_parties': r.get('matched_parties', []),
                'matched_issues': r.get('matched_issues', []),
                'matched_basis': r.get('matched_basis', []),
            })
    if rows:
        last_id = rows[-1]['rowid']
        store.set_scan_state('last_scanned_article_id', str(last_id))
    store.set_scan_state('last_scan_time', format_taipei_now())
    return matched, articles_map

def collect_stats(store: ElectionFactStore, news_conn, classifier: ElectionClassifier, days: int = 90):
    cutoff = (datetime.now(TAIPEI) - timedelta(days=days)).isoformat()
    rows = news_conn.execute(
        'SELECT rowid, source_id, source_name, category, title, url, published_at, fetched_at '
        'FROM articles WHERE fetched_at >= ? ORDER BY fetched_at', (cutoff,)
    ).fetchall()
    total = len(rows)
    tainan_matches = 0
    nt_matches = 0
    articles_map = {}
    match_list = []
    for row in rows:
        article = dict(row)
        articles_map[article['url']] = article
        results = classifier.classify_article(
            article['title'], article['category'], article['source_name']
        )
        for r in results:
            match_list.append({
                'article_url': article['url'],
                'city': r['city'],
                'relevance': r['relevance'],
                'matched_people': r.get('matched_people', []),
                'matched_parties': r.get('matched_parties', []),
                'matched_issues': r.get('matched_issues', []),
                'matched_basis': r.get('matched_basis', []),
            })
            if r['city'] == 'tainan':
                tainan_matches += 1
            elif r['city'] == 'new_taipei':
                nt_matches += 1
    events = merge_articles_into_events(match_list, articles_map)
    tainan_events = sum(1 for e in events if e['city'] == 'tainan')
    nt_events = sum(1 for e in events if e['city'] == 'new_taipei')
    source_dist = {}
    people_dist = {}
    issue_dist = {}
    tainan_match_samples = [m for m in match_list if m['city'] == 'tainan'][:50]
    nt_match_samples = [m for m in match_list if m['city'] == 'new_taipei'][:50]
    total_urls = set()
    for m in match_list:
        total_urls.add(m['article_url'])
        src = articles_map.get(m['article_url'], {}).get('source_name', 'unknown')
        source_dist[src] = source_dist.get(src, 0) + 1
        for p in (m.get('matched_people') or []):
            people_dist[p] = people_dist.get(p, 0) + 1
        for iss in (m.get('matched_issues') or []):
            issue_dist[iss] = issue_dist.get(iss, 0) + 1
    sampled_matched = random.sample(list(total_urls), min(50, len(total_urls)))
    all_urls = set(a['url'] for a in rows)
    unmatched = list(all_urls - total_urls)
    sampled_unmatched = random.sample(unmatched, min(30, len(unmatched)))
    return {
        'total_scanned': total,
        'tainan_matches': tainan_matches,
        'tainan_unique_urls': len(set(m['article_url'] for m in match_list if m['city'] == 'tainan')),
        'tainan_events': tainan_events,
        'nt_matches': nt_matches,
        'nt_unique_urls': len(set(m['article_url'] for m in match_list if m['city'] == 'new_taipei')),
        'nt_events': nt_events,
        'source_distribution': source_dist,
        'people_distribution': people_dist,
        'issue_distribution': issue_dist,
        'sampled_matched_urls': sampled_matched,
        'sampled_unmatched_urls': sampled_unmatched,
    }

def main():
    load_environment(PROJECT_ROOT)
    settings = get_settings(PROJECT_ROOT, load_env=False)
    parser = argparse.ArgumentParser(description='Election Watch Scanner')
    parser.add_argument('command', choices=['scan', 'backfill', 'status'])
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--db', type=str, default=str(settings.news_db_path))
    args = parser.parse_args()
    classifier = ElectionClassifier(CONFIG_PATH)
    store = ElectionFactStore(DB_PATH)
    store.connect()
    store.create_tables()
    news_conn = load_news_db(args.db)
    if args.command == 'scan':
        state = store.get_scan_state()
        last_id = int(state.get('last_scanned_article_id', '0'))
        matched, _ = scan_new_articles(store, classifier, news_conn, since_id=last_id)
        print(f'扫描完成: {len(matched)} 条匹配')
    elif args.command == 'backfill':
        stats = collect_stats(store, news_conn, classifier, days=args.days)
        print(f'扫描文章数: {stats["total_scanned"]}')
        print(f'台南候选文章数: {stats["tainan_unique_urls"]}')
        print(f'台南有效事件数: {stats["tainan_events"]}')
        print(f'新北候选文章数: {stats["nt_unique_urls"]}')
        print(f'新北有效事件数: {stats["nt_events"]}')
        print(f'\n来源分布: {json.dumps(stats["source_distribution"], ensure_ascii=False)}')
        print(f'\n人物分布: {json.dumps(people_top5(stats["people_distribution"]), ensure_ascii=False)}')
        print(f'\n议题分布: {json.dumps(issue_top5(stats["issue_distribution"]), ensure_ascii=False)}')
        print(f'\n误报抽样 ({len(stats["sampled_matched_urls"])}篇):')
        for u in stats['sampled_matched_urls'][:5]:
            print(f'  {u}')
        print(f'\n漏报抽样 ({len(stats["sampled_unmatched_urls"])}篇):')
        for u in stats['sampled_unmatched_urls'][:5]:
            print(f'  {u}')
        report_path = PROJECT_ROOT / 'data' / 'reports' / 'election' / 'backfill_stats.json'
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        print(f'\n回溯统计已保存: {report_path}')
    elif args.command == 'status':
        tainan_matches = store.get_match_count('tainan')
        nt_matches = store.get_match_count('new_taipei')
        tainan_events = store.get_event_count('tainan')
        nt_events = store.get_event_count('new_taipei')
        print(f'台南匹配文章: {tainan_matches}')
        print(f'台南事件: {tainan_events}')
        print(f'新北匹配文章: {nt_matches}')
        print(f'新北事件: {nt_events}')
    store.close()
    news_conn.close()

def people_top5(d: dict) -> dict:
    return dict(sorted(d.items(), key=lambda x: -x[1])[:5])

def issue_top5(d: dict) -> dict:
    return dict(sorted(d.items(), key=lambda x: -x[1])[:5])

if __name__ == '__main__':
    main()
