import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.election_candidates.config import load_config
from app.election_candidates.match_reader import inline_classify
from tests.election_candidates.golden_runner import article_from_row

rows = [
    {"id": "p1", "title": "陳亭妃宣布參選台南市長", "url": "https://www.cna.com.tw/news/aipl/p1.aspx", "source_name": "中央社", "category": "politics", "published_at": "2026-03-01T09:00:00+08:00", "summary": "", "match": {"city": "tainan", "relevance": "high", "matched_people": ["陳亭妃"], "matched_parties": [], "matched_issues": ["競選"], "matched_terms": ["陳亭妃", "競選"], "matched_basis": ["region_match", "candidate_match", "issue_match"], "region_match": True, "election_context_match": True, "match_score": 1.0}},
    {"id": "p2", "title": "謝龍介質疑對手賄選", "url": "https://www.cna.com.tw/news/aipl/p2.aspx", "source_name": "中央社", "category": "politics", "published_at": "2026-07-14T16:00:00+08:00", "summary": "", "match": {"city": "tainan", "relevance": "medium", "matched_people": ["謝龍介"], "matched_parties": [], "matched_issues": [], "matched_terms": ["謝龍介"], "matched_basis": ["region_match", "candidate_match"], "region_match": True, "election_context_match": False, "match_score": 0.65}},
]
config = load_config("config/election_candidate_pipeline.yaml")
arts = [article_from_row(r) for r in rows]
inline = inline_classify(arts, config)
for aid in ("p1", "p2"):
    m = inline.get(aid)
    print(aid, "->", m.matched_people if m else None, m.matched_issues if m else None)
