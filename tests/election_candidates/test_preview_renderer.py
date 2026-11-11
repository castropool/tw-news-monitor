from __future__ import annotations

from app.election_candidates.preview_renderer import (
    build_queue_item,
    render_markdown,
)


def _candidate():
    return {
        "candidate_id": "cand_tnn_0123456789",
        "candidate_title": "陳亭妃出席競選活動",
        "canonical_event_date": "2026-07-19T00:00:00",
        "event_date_basis": "explicit_in_title",
        "event_date_precision": "day",
        "event_date_confidence": "high",
        "candidate_event_type": "campaign_launch",
        "primary_actor": "陳亭妃",
        "candidate_summary": "據1篇報導",
        "article_count": 1,
        "source_count": 1,
        "completeness_score": 0.9,
        "cluster_confidence": 0.9,
        "relevance_score": 0.8,
        "date_confidence": 1.0,
        "source_confidence": 0.8,
        "assertion_risk_score": 0.1,
        "formal_duplicate_score": 0.1,
        "risk_level": "low",
        "review_status": "review_required",
        "status_reason_codes_json": '["eligible"]',
        "assertion_profile_json": '{"risk_flags":[]}',
    }


def _assertions():
    return [
        {"assertion_kind": "observed_fact", "assertion_text": "陳亭妃出席活動",
         "evidence_article_id": "1"},
        {"assertion_kind": "actor_statement", "assertion_text": "陳亭妃表示支持",
         "evidence_article_id": "1"},
        {"assertion_kind": "allegation", "assertion_text": "指控對手買票",
         "evidence_article_id": "1"},
        {"assertion_kind": "media_interpretation", "assertion_text": "媒體分析選情",
         "evidence_article_id": "1"},
        {"assertion_kind": "uncertain_report", "assertion_text": "據悉可能換將",
         "evidence_article_id": "1"},
    ]


def test_queue_item_sections_separate_assertions():
    item = build_queue_item(
        _candidate(),
        [{"news_article_id": "1", "article_title": "t", "article_url": "u",
          "source_name": "s", "published_at": "p", "is_anchor": 1}],
        _assertions(),
        [{"normalized_source_name": "中央社", "formal_match_status": "exact", "formal_source_id": "src"}],
        [{"formal_event_id": "evt", "similarity_score": 0.1, "suggested_action": "no_material_match",
          "matching_reasons_json": "[]"}],
    )
    assert len(item["observed_facts"]) == 1
    assert len(item["actor_statements"]) == 1
    assert len(item["allegations"]) == 1
    assert len(item["media_interpretations"]) == 1
    assert len(item["unknowns"]) == 1
    assert item["source_list"][0]["formal_match_status"] == "exact"


def test_markdown_has_all_required_sections():
    item = build_queue_item(
        _candidate(),
        [{"news_article_id": "1", "article_title": "t", "article_url": "u",
          "source_name": "s", "published_at": "p", "is_anchor": 1}],
        _assertions(),
        [{"normalized_source_name": "中央社", "formal_match_status": "exact", "formal_source_id": "src"}],
        [{"formal_event_id": "evt", "similarity_score": 0.1, "suggested_action": "no_material_match",
          "matching_reasons_json": "[]"}],
    )
    md = render_markdown(item)
    for section in ["一、可观察事实", "二、人物及组织表态", "三、指控和争议",
                    "四、媒体解读", "五、不确定项", "六、关联新闻", "七、来源",
                    "八、疑似重复正式事件", "九、需要人工裁决的问题"]:
        assert section in md


def test_markdown_does_not_mix_allegation_into_facts():
    item = build_queue_item(
        _candidate(), [], _assertions(), [],
        [{"formal_event_id": "evt", "similarity_score": 0.1, "suggested_action": "no_material_match",
          "matching_reasons_json": "[]"}],
    )
    md = render_markdown(item)
    facts_part = md.split("二、人物及组织表态")[0]
    assert "指控對手買票" not in facts_part


def test_markdown_links_articles():
    item = build_queue_item(
        _candidate(),
        [{"news_article_id": "1", "article_title": "標題", "article_url": "https://a.com/1",
          "source_name": "中央社", "published_at": "2026-07-19T08:00:00", "is_anchor": 1}],
        _assertions(), [],
        [{"formal_event_id": "evt", "similarity_score": 0.1, "suggested_action": "no_material_match",
          "matching_reasons_json": "[]"}],
    )
    md = render_markdown(item)
    assert "https://a.com/1" in md


def test_review_queue_no_political_inference_terms():
    item = build_queue_item(_candidate(), [], [], [], [])
    assert "勝算提高" not in item["candidate_summary"]
