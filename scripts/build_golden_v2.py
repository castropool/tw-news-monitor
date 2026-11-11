"""Generate frozen Phase 1.5 golden fixtures (articles/cases/formal events)."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "election_candidates"


def M(people=None, parties=None, issues=None, region=True, election=True, score=1.0, relevance="high"):
    people = people or []
    parties = parties or []
    issues = issues or []
    terms = list(dict.fromkeys(people + parties + issues))
    basis = []
    if region:
        basis.append("region_match")
    if people:
        basis.append("candidate_match")
    if election:
        basis.append("election_context")
    return {
        "city": "tainan",
        "relevance": relevance,
        "matched_people": people,
        "matched_parties": parties,
        "matched_issues": issues,
        "matched_terms": terms,
        "matched_basis": basis,
        "region_match": region,
        "election_context_match": election,
        "match_score": score,
    }


ARTICLES = {
    "a01": {"title": "民進黨正式提名陳亭妃參選台南市長", "url": "https://www.cna.com.tw/news/aipl/g01a01.aspx", "source": "中央社", "date": "2026-01-21T10:00:00+08:00", "match": M(["陳亭妃"], ["民進黨"], ["提名"])},
    "a01b": {"title": "民進黨正式提名陳亭妃參選台南市長", "url": "https://news.ltn.com.tw/news/politics/g01a01b", "source": "自由時報", "date": "2026-01-21T10:05:00+08:00", "match": M(["陳亭妃"], ["民進黨"], ["提名"])},
    "a02": {"title": "陳亭妃完成登記參選台南市長", "url": "https://www.cna.com.tw/news/aipl/g01a02.aspx", "source": "中央社", "date": "2026-03-02T09:00:00+08:00", "match": M(["陳亭妃"], [], ["登記"])},
    "a03": {"title": "陳亭妃台南競選總部成立", "url": "https://www.cna.com.tw/news/aipl/g01a03.aspx", "source": "中央社", "date": "2026-04-10T10:00:00+08:00", "match": M(["陳亭妃"], [], ["競選"])},
    "a04": {"title": "陳亭妃安南區掃街拜票", "url": "https://www.cna.com.tw/news/aipl/g01a04.aspx", "source": "中央社", "date": "2026-06-20T11:00:00+08:00", "match": M(["陳亭妃"], [], ["競選"])},
    "a05": {"title": "陳亭妃成立大台南後援會", "url": "https://www.cna.com.tw/news/aipl/g01a05.aspx", "source": "中央社", "date": "2026-07-05T10:00:00+08:00", "match": M(["陳亭妃"], [], ["競選"])},
    "a06": {"title": "賴清德與陳亭妃同框看板啟用", "url": "https://www.cna.com.tw/news/aipl/g01a06.aspx", "source": "中央社", "date": "2026-07-10T10:00:00+08:00", "match": M(["陳亭妃"], ["民進黨"], ["競選"])},
    "a07": {"title": "台南市長最新民調公布 陳亭妃支持度領先", "url": "https://www.cna.com.tw/news/aipl/g01a07.aspx", "source": "中央社", "date": "2026-07-12T10:00:00+08:00", "match": M(["陳亭妃", "謝龍介"], [], ["民調", "支持度"])},
    "a08": {"title": "陳亭妃視察三爺溪 提出治理方案", "url": "https://www.cna.com.tw/news/aipl/g01a08.aspx", "source": "中央社", "date": "2026-07-06T10:00:00+08:00", "match": M(["陳亭妃"], [], ["市政"])},
    "a09": {"title": "謝龍介安南區宮廟聯合拜票", "url": "https://www.cna.com.tw/news/aipl/g01a09.aspx", "source": "中央社", "date": "2026-07-21T10:00:00+08:00", "match": M(["謝龍介"], [], ["競選"])},
    "a10": {"title": "謝龍介舉辦台南市長選舉造勢晚會", "url": "https://www.cna.com.tw/news/aipl/g01a10.aspx", "source": "中央社", "date": "2026-07-25T18:00:00+08:00", "match": M(["謝龍介"], [], ["造勢"])},
    "a10b": {"title": "謝龍介舉辦台南市長選舉造勢晚會", "url": "https://news.ebc.net.tw/news/politics/g01a10b", "source": "東森新聞", "date": "2026-07-25T19:00:00+08:00", "match": M(["謝龍介"], [], ["造勢"])},
    "a10c": {"title": "謝龍介造勢晚會 支持者擠爆會場", "url": "https://www.cna.com.tw/news/aipl/g01a10c.aspx", "source": "中央社", "date": "2026-07-25T20:00:00+08:00", "match": M(["謝龍介"], [], ["造勢"])},
    "a11": {"title": "藍白就台南市長人選協調", "url": "https://www.cna.com.tw/news/aipl/g01a11.aspx", "source": "中央社", "date": "2026-07-15T10:00:00+08:00", "match": M([], ["國民黨", "民眾黨"], ["選舉"])},
    "a12": {"title": "陳亭妃宣布參選台南市長", "url": "https://www.cna.com.tw/news/aipl/g01a12.aspx", "source": "中央社", "date": "2026-03-01T09:00:00+08:00", "match": M(["陳亭妃"], [], ["競選"])},
    "b01": {"title": "陳亭妃表示支持台南市政建設", "url": "https://www.cna.com.tw/news/aipl/g01b01.aspx", "source": "中央社", "date": "2026-07-08T10:00:00+08:00", "match": M(["陳亭妃"], [], ["選舉"])},
    "b02": {"title": "謝龍介：藍白合作有助在野整合", "url": "https://www.cna.com.tw/news/aipl/g01b02.aspx", "source": "中央社", "date": "2026-07-16T10:00:00+08:00", "match": M(["謝龍介"], [], ["選舉"])},
    "b03": {"title": "陳亭妃：承諾當選後推動老人福利", "url": "https://www.cna.com.tw/news/aipl/g01b03.aspx", "source": "中央社", "date": "2026-07-18T10:00:00+08:00", "match": M(["陳亭妃"], [], ["政見"])},
    "b04": {"title": "林俊憲表示台南選情需要團結", "url": "https://www.cna.com.tw/news/aipl/g01b04.aspx", "source": "中央社", "date": "2026-07-22T10:00:00+08:00", "match": M(["林俊憲"], [], ["選情"])},
    "c01": {"title": "黃博郎觀點 台南市長選情冷清", "url": "https://newtalk.tw/news/view/g01c01", "source": "Newtalk新聞", "date": "2026-07-21T19:00:00+08:00", "match": M([], [], ["選情"], region=True, election=True, score=0.65, relevance="medium")},
    "c02": {"title": "選情初探 台南議員第四選區競爭激烈", "url": "https://udn.com/news/story/g01c02", "source": "聯合新聞網", "date": "2026-07-18T00:00:00+08:00", "match": M([], [], ["選情"], region=True, election=True, score=0.65, relevance="medium")},
    "c03": {"title": "傳王定宇不再選立委", "url": "https://www.storm.mg/article/g01c03", "source": "風傳媒", "date": "2026-07-21T15:00:00+08:00", "match": M(["王定宇"], ["民進黨"], ["派系"])},
    "c04": {"title": "民進黨全代會19日登場 賴清德授戰旗給縣市長參選人", "url": "https://www.cna.com.tw/news/aipl/g01c04.aspx", "source": "中央社", "date": "2026-07-15T08:00:00+08:00", "match": M([], ["民進黨"], ["選舉"], region=False, election=True, score=0.35, relevance="low")},
    "c05": {"title": "賴清德驚爆早就不爽王定宇", "url": "https://www.storm.mg/article/g01c05", "source": "風傳媒", "date": "2026-07-21T18:00:00+08:00", "match": M(["王定宇"], [], ["派系"])},
    "c06": {"title": "王定宇交棒林智鴻 派系角力", "url": "https://news.ltn.com.tw/news/politics/g01c06", "source": "自由時報", "date": "2026-07-19T20:00:00+08:00", "match": M(["王定宇"], ["民進黨"], ["派系"])},
    "d01": {"title": "國民黨備戰年底選戰 新北高雄成決戰焦點", "url": "https://www.cna.com.tw/news/aipl/g01d01.aspx", "source": "中央社", "date": "2026-07-25T16:00:00+08:00", "match": M([], ["國民黨"], [], region=False, election=False, score=0.2, relevance="low")},
    "d02": {"title": "柯文哲談藍白合作", "url": "https://www.cna.com.tw/news/aipl/g01d02.aspx", "source": "中央社", "date": "2026-07-26T12:00:00+08:00", "match": M([], ["民眾黨"], [], region=False, election=False, score=0.2, relevance="low")},
    "d03": {"title": "花蓮市長徵召 戴于文參選", "url": "https://udn.com/news/story/g01d03", "source": "聯合新聞網", "date": "2026-07-15T17:00:00+08:00", "match": M([], ["民眾黨"], [], region=False, election=False, score=0.2, relevance="low")},
    "d04": {"title": "民進黨全代會 黨職改選", "url": "https://www.cna.com.tw/news/aipl/g01d04.aspx", "source": "中央社", "date": "2026-07-19T19:00:00+08:00", "match": M([], ["民進黨"], [], region=False, election=False, score=0.2, relevance="low")},
    "e01": {"title": "謝龍介質疑對手賄選", "url": "https://www.cna.com.tw/news/aipl/g01e01.aspx", "source": "中央社", "date": "2026-07-14T16:00:00+08:00", "match": M(["謝龍介"], [], ["選舉"])},
    "e02": {"title": "政院駁斥謝龍介指控不實", "url": "https://www.cna.com.tw/news/aipl/g01e02.aspx", "source": "中央社", "date": "2026-07-17T14:00:00+08:00", "match": M([], [], ["選舉"], region=True, election=True, score=0.65, relevance="medium")},
    "f01": {"title": "陳亭妃擬於月底舉辦造勢晚會", "url": "https://www.cna.com.tw/news/aipl/g01f01.aspx", "source": "中央社", "date": "2026-07-20T10:00:00+08:00", "match": M(["陳亭妃"], [], ["造勢"])},
    "h21a": {"title": "陳亭妃安南區造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h21a.aspx", "source": "中央社", "date": "2026-07-25T18:00:00+08:00", "match": M(["陳亭妃"], [], ["造勢"])},
    "h21b": {"title": "陳亭妃安南區造勢晚會 現場湧入千人", "url": "https://news.ltn.com.tw/news/politics/h21b", "source": "自由時報", "date": "2026-07-26T09:00:00+08:00", "match": M(["陳亭妃"], [], ["造勢"])},
    "h22a": {"title": "謝龍介指控對手抹黑", "url": "https://www.cna.com.tw/news/aipl/h22a.aspx", "source": "中央社", "date": "2026-07-20T10:00:00+08:00", "match": M(["謝龍介"], [], ["選舉"])},
    "h22b": {"title": "謝龍介澄清抹黑指控", "url": "https://www.cna.com.tw/news/aipl/h22b.aspx", "source": "中央社", "date": "2026-07-21T10:00:00+08:00", "match": M(["謝龍介"], [], ["選舉"])},
    "h23a": {"title": "陳亭妃舉辦造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h23a.aspx", "source": "中央社", "date": "2026-07-24T18:00:00+08:00", "match": M(["陳亭妃"], [], ["造勢"])},
    "h23b": {"title": "陳亭妃安南區掃街拜票", "url": "https://www.cna.com.tw/news/aipl/h23b.aspx", "source": "中央社", "date": "2026-07-24T10:00:00+08:00", "match": M(["陳亭妃"], [], ["競選"])},
    "h24a": {"title": "謝龍介舉辦造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h24a.aspx", "source": "中央社", "date": "2026-07-10T18:00:00+08:00", "match": M(["謝龍介"], [], ["造勢"])},
    "h24b": {"title": "謝龍介舉辦造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h24b.aspx", "source": "中央社", "date": "2026-07-25T18:00:00+08:00", "match": M(["謝龍介"], [], ["造勢"])},
    "h25a": {"title": "陳亭妃舉辦造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h25a.aspx", "source": "中央社", "date": "2026-07-22T18:00:00+08:00", "match": M(["陳亭妃"], [], ["造勢"])},
    "h25b": {"title": "謝龍介舉辦造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h25b.aspx", "source": "中央社", "date": "2026-07-22T18:30:00+08:00", "match": M(["謝龍介"], [], ["造勢"])},
    "h26a": {"title": "陳亭妃成立大台南後援會", "url": "https://www.cna.com.tw/news/aipl/h26a.aspx", "source": "中央社", "date": "2026-07-12T10:00:00+08:00", "match": M(["陳亭妃"], [], ["競選"])},
    "h26b": {"title": "陳亭妃表態支持台南在地產業", "url": "https://www.cna.com.tw/news/aipl/h26b.aspx", "source": "中央社", "date": "2026-07-13T10:00:00+08:00", "match": M(["陳亭妃"], [], ["選舉"])},
    "h27a": {"title": "民進黨正式提名陳亭妃參選台南市長", "url": "https://www.cna.com.tw/news/aipl/h27a.aspx", "source": "中央社", "date": "2026-07-19T10:00:00+08:00", "match": M(["陳亭妃"], ["民進黨"], ["提名"])},
    "h27b": {"title": "陳亭妃舉辦參選造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h27b.aspx", "source": "中央社", "date": "2026-07-20T18:00:00+08:00", "match": M(["陳亭妃"], [], ["造勢"])},
    "h28a": {"title": "陳亭妃出席市政座談會", "url": "https://www.cna.com.tw/news/aipl/h28a.aspx", "source": "中央社", "date": "2026-07-11T10:00:00+08:00", "match": M(["陳亭妃"], [], ["市政"])},
    "h28b": {"title": "謝龍介拜會農會 談選舉", "url": "https://www.cna.com.tw/news/aipl/h28b.aspx", "source": "中央社", "date": "2026-07-11T15:00:00+08:00", "match": M(["謝龍介"], [], ["選舉"])},
    "h29": {"title": "民進黨正式提名陳亭妃參選台南市長", "url": "https://www.cna.com.tw/news/aipl/h29.aspx", "source": "中央社", "date": "2026-01-21T10:00:00+08:00", "match": M(["陳亭妃"], ["民進黨"], ["提名"])},
    "h30": {"title": "謝龍介舉辦造勢晚會", "url": "https://www.cna.com.tw/news/aipl/h30.aspx", "source": "中央社", "date": "2026-07-22T18:00:00+08:00", "match": M(["謝龍介"], [], ["造勢"])},
}


def A(aid):
    a = ARTICLES[aid]
    return {
        "id": aid,
        "title": a["title"],
        "url": a["url"],
        "source_name": a["source"],
        "category": "politics",
        "published_at": a["date"],
        "summary": "",
        "match": a["match"],
    }


CASES = [
    # ---- calibration (20) ----
    {"case_id": "cal_01", "split": "calibration", "article_ids": ["a01", "a01b"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a01", "a01b"]], "expected_relationship": "same_event", "expected_event_type": "party_nomination", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "likely_duplicate", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_02", "split": "calibration", "article_ids": ["a10", "a10b", "a10c"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a10", "a10b", "a10c"]], "expected_relationship": "same_event", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_03", "split": "calibration", "article_ids": ["h23a", "h23b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h23a"], ["h23b"]], "expected_relationship": "possible_subevent", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_04", "split": "calibration", "article_ids": ["b01"], "expected_relevance": "direct_statement", "expected_cluster_count": 1, "expected_cluster_members": [["b01"]], "expected_relationship": "same_event", "expected_event_type": "policy_proposal", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["actor_statement"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_05", "split": "calibration", "article_ids": ["e01"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["e01"]], "expected_relationship": "same_event", "expected_event_type": "campaign_attack", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["allegation"], "expected_route_status": "hold", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_06", "split": "calibration", "article_ids": ["c01"], "expected_relevance": "contextual", "expected_cluster_count": 1, "expected_cluster_members": [["c01"]], "expected_relationship": "same_event", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["media_interpretation"], "expected_route_status": "hold", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_07", "split": "calibration", "article_ids": ["c03"], "expected_relevance": "contextual", "expected_cluster_count": 1, "expected_cluster_members": [["c03"]], "expected_relationship": "same_event", "expected_event_type": "faction_conflict", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["uncertain_report"], "expected_route_status": "hold", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_08", "split": "calibration", "article_ids": ["f01"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["f01"]], "expected_relationship": "same_event", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["planned_action"], "expected_route_status": "hold", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_09", "split": "calibration", "article_ids": ["a12"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a12"]], "expected_relationship": "same_event", "expected_event_type": "campaign_launch", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_10", "split": "calibration", "article_ids": ["a02"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a02"]], "expected_relationship": "same_event", "expected_event_type": "primary_registration", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_11", "split": "calibration", "article_ids": ["a05"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a05"]], "expected_relationship": "same_event", "expected_event_type": "support_organization", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_12", "split": "calibration", "article_ids": ["a06"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a06"]], "expected_relationship": "same_event", "expected_event_type": "joint_campaign", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_13", "split": "calibration", "article_ids": ["b02"], "expected_relevance": "direct_statement", "expected_cluster_count": 1, "expected_cluster_members": [["b02"]], "expected_relationship": "same_event", "expected_event_type": "alliance_proposal", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["actor_statement"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_14", "split": "calibration", "article_ids": ["a11"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a11"]], "expected_relationship": "same_event", "expected_event_type": "alliance_coordination", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_15", "split": "calibration", "article_ids": ["c06"], "expected_relevance": "contextual", "expected_cluster_count": 1, "expected_cluster_members": [["c06"]], "expected_relationship": "same_event", "expected_event_type": "faction_conflict", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["unknown"], "expected_route_status": "hold", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_16", "split": "calibration", "article_ids": ["e01", "e02"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["e01"], ["e02"]], "expected_relationship": "related_event", "expected_event_type": "campaign_attack", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["allegation", "actor_statement"], "expected_route_status": "hold", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_17", "split": "calibration", "article_ids": ["a07"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a07"]], "expected_relationship": "same_event", "expected_event_type": "poll_release", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_18", "split": "calibration", "article_ids": ["a08"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["a08"]], "expected_relationship": "same_event", "expected_event_type": "governance_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "cal_19", "split": "calibration", "article_ids": ["d01"], "expected_relevance": "irrelevant", "expected_cluster_count": 1, "expected_cluster_members": [["d01"]], "expected_relationship": "same_event", "expected_event_type": "", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": [], "expected_route_status": "auto_reject", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "cal_20", "split": "calibration", "article_ids": ["b04"], "expected_relevance": "direct_statement", "expected_cluster_count": 1, "expected_cluster_members": [["b04"]], "expected_relationship": "same_event", "expected_event_type": "party_integration", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["actor_statement"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    # ---- holdout (10) ----
    {"case_id": "hold_21", "split": "holdout", "article_ids": ["h21a", "h21b"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["h21a", "h21b"]], "expected_relationship": "same_event", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_22", "split": "holdout", "article_ids": ["h22a", "h22b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h22a"], ["h22b"]], "expected_relationship": "possible_subevent", "expected_event_type": "campaign_attack", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["allegation", "actor_statement"], "expected_route_status": "hold", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": True},
    {"case_id": "hold_23", "split": "holdout", "article_ids": ["h23a", "h23b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h23a"], ["h23b"]], "expected_relationship": "possible_subevent", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_24", "split": "holdout", "article_ids": ["h24a", "h24b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h24a"], ["h24b"]], "expected_relationship": "separate_event", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_25", "split": "holdout", "article_ids": ["h25a", "h25b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h25a"], ["h25b"]], "expected_relationship": "separate_event", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_26", "split": "holdout", "article_ids": ["h26a", "h26b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h26a"], ["h26b"]], "expected_relationship": "possible_subevent", "expected_event_type": "support_organization", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact", "actor_statement"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_27", "split": "holdout", "article_ids": ["h27a", "h27b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h27a"], ["h27b"]], "expected_relationship": "possible_subevent", "expected_event_type": "party_nomination", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_28", "split": "holdout", "article_ids": ["h28a", "h28b"], "expected_relevance": "direct_event", "expected_cluster_count": 2, "expected_cluster_members": [["h28a"], ["h28b"]], "expected_relationship": "separate_event", "expected_event_type": "governance_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_29", "split": "holdout", "article_ids": ["h29"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["h29"]], "expected_relationship": "same_event", "expected_event_type": "party_nomination", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "duplicate_candidate", "expected_formal_match": "likely_duplicate", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
    {"case_id": "hold_30", "split": "holdout", "article_ids": ["h30"], "expected_relevance": "direct_event", "expected_cluster_count": 1, "expected_cluster_members": [["h30"]], "expected_relationship": "same_event", "expected_event_type": "campaign_event", "expected_event_date_basis": "inferred_from_publication", "expected_assertion_kinds": ["observed_fact"], "expected_route_status": "review_required", "expected_formal_match": "no_match", "must_not_merge_with": [], "must_not_classify_as_observed_fact": False},
]


FORMAL_EVENTS = [
    {
        "event_id": "evt_golden_nom_20260121",
        "election_id": "TW-2026-TNN-MAYOR",
        "occurred_at": "2026-01-21T10:00:00+08:00",
        "event_type": "party_nomination",
        "title": "民进党正式提名陈亭妃",
        "fact_summary": "民进党中执会正式提名陈亭妃参选台南市长",
        "actors_json": '["陳亭妃"]',
        "issues_json": '["提名"]',
        "analysis_json": '{"verified_facts":["正式提名"]}',
    },
    {
        "event_id": "evt_golden_rally_20260725",
        "election_id": "TW-2026-TNN-MAYOR",
        "occurred_at": "2026-07-25T18:00:00+08:00",
        "event_type": "campaign_event",
        "title": "谢龙介台南市长选举造势晚会",
        "fact_summary": "谢龙介举办造势晚会",
        "actors_json": '["謝龍介"]',
        "issues_json": '["造勢"]',
        "analysis_json": '{"verified_facts":["造势晚会"]}',
    },
    {
        "event_id": "evt_golden_visit_20260721",
        "election_id": "TW-2026-TNN-MAYOR",
        "occurred_at": "2026-07-21T10:00:00+08:00",
        "event_type": "campaign_event",
        "title": "谢龙介安南宫庙联合拜票",
        "fact_summary": "谢龙介与蓝营议员参选人在安南区宫庙联合拜票",
        "actors_json": '["謝龍介"]',
        "issues_json": '["競選"]',
        "analysis_json": '{"verified_facts":["联合拜票"]}',
    },
    {
        "event_id": "evt_golden_poll_20260720",
        "election_id": "TW-2026-TNN-MAYOR",
        "occurred_at": "2026-07-20T10:00:00+08:00",
        "event_type": "poll_release",
        "title": "台南市长民调公布",
        "fact_summary": "陈亭妃支持度领先",
        "actors_json": '["陳亭妃"]',
        "issues_json": '["民調"]',
        "analysis_json": '{"verified_facts":["民调公布"]}',
    },
]


SOURCES = [
    {"source_id": "gsrc_cna", "publisher": "中央社", "title": "", "url": "https://www.cna.com.tw/news/aipl/g.aspx"},
    {"source_id": "gsrc_udn", "publisher": "聯合報", "title": "", "url": "https://udn.com/news/story/1"},
    {"source_id": "gsrc_ltn", "publisher": "自由時報", "title": "", "url": "https://news.ltn.com.tw/news/politics/1"},
    {"source_id": "gsrc_ebc", "publisher": "東森新聞", "title": "", "url": "https://news.ebc.net.tw/news/politics/1"},
    {"source_id": "gsrc_nt", "publisher": "新頭殼", "title": "", "url": "https://newtalk.tw/news/view/1"},
    {"source_id": "gsrc_storm", "publisher": "風傳媒", "title": "", "url": "https://www.storm.mg/article/1"},
]

EVENT_SOURCES = {
    "evt_golden_nom_20260121": ["gsrc_cna"],
    "evt_golden_rally_20260725": ["gsrc_cna", "gsrc_ebc"],
    "evt_golden_visit_20260721": ["gsrc_cna"],
    "evt_golden_poll_20260720": ["gsrc_cna"],
}


FORMAL_DUPLICATE_CASES = [
    {"case_id": "dup_01", "known_duplicate": True, "expected_formal_event_id": "evt_golden_nom_20260121", "candidate_title": "民進黨正式提名陳亭妃參選台南市長", "candidate_actors": ["陳亭妃"], "candidate_date": "2026-01-21T10:00:00+08:00", "candidate_event_type": "party_nomination", "candidate_keywords": ["提名", "陳亭妃"]},
    {"case_id": "dup_02", "known_duplicate": True, "expected_formal_event_id": "evt_golden_rally_20260725", "candidate_title": "謝龍介舉辦台南市長選舉造勢晚會", "candidate_actors": ["謝龍介"], "candidate_date": "2026-07-25T18:00:00+08:00", "candidate_event_type": "campaign_event", "candidate_keywords": ["造勢", "謝龍介"]},
    {"case_id": "dup_03", "known_duplicate": True, "expected_formal_event_id": "evt_golden_nom_20260121", "candidate_title": "民進黨正式提名陳亭妃參選台南市長 後續報導", "candidate_actors": ["陳亭妃"], "candidate_date": "2026-01-22T10:00:00+08:00", "candidate_event_type": "party_nomination", "candidate_keywords": ["提名", "陳亭妃"]},
    {"case_id": "dup_04", "known_duplicate": False, "expected_formal_event_id": "", "candidate_title": "謝龍介舉辦造勢晚會", "candidate_actors": ["謝龍介"], "candidate_date": "2026-07-22T18:00:00+08:00", "candidate_event_type": "campaign_event", "candidate_keywords": ["造勢", "謝龍介"]},
    {"case_id": "dup_05", "known_duplicate": False, "expected_formal_event_id": "", "candidate_title": "陳亭妃宣布參選台南市長", "candidate_actors": ["陳亭妃"], "candidate_date": "2026-03-01T09:00:00+08:00", "candidate_event_type": "campaign_launch", "candidate_keywords": ["參選", "陳亭妃"]},
    {"case_id": "dup_06", "known_duplicate": False, "expected_formal_event_id": "", "candidate_title": "謝龍介指控對手賄選", "candidate_actors": ["謝龍介"], "candidate_date": "2026-07-14T16:00:00+08:00", "candidate_event_type": "campaign_attack", "candidate_keywords": ["指控", "謝龍介"]},
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "golden_articles_v2.jsonl", "w", encoding="utf-8") as f:
        for aid in ARTICLES:
            f.write(json.dumps(A(aid), ensure_ascii=False) + "\n")
    with open(OUT / "golden_formal_events_v2.jsonl", "w", encoding="utf-8") as f:
        for evt in FORMAL_EVENTS:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    with open(OUT / "golden_sources_v2.jsonl", "w", encoding="utf-8") as f:
        for src in SOURCES:
            f.write(json.dumps(src, ensure_ascii=False) + "\n")
    (OUT / "golden_candidate_cases_v2.json").write_text(
        json.dumps(CASES, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "golden_formal_duplicate_cases.json").write_text(
        json.dumps(FORMAL_DUPLICATE_CASES, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"articles={len(ARTICLES)} cases={len(CASES)} calibration={sum(1 for c in CASES if c['split']=='calibration')} holdout={sum(1 for c in CASES if c['split']=='holdout')}")


if __name__ == "__main__":
    main()
