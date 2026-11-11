"""Build the July 2026 human-style adjudication ledger (Phase 1.5).

Every label is derived only from the article title/summary and the recorded
ElectionClassifier match evidence.  The default label is `irrelevant`; explicit
overrides below mark contextual articles and duplicate groups.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
QC = ROOT / "data" / "election_candidates" / "tainan_2026" / "quality_calibration"


# article_id -> adjudication fields
OVERRIDES = {
    74: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:谢龙介(台南市长候选人)",
            "action_evidence:质询/口角",
            "negative_evidence:内容为全国食安专报攻防，未直接指向台南市长选举动作",
        ],
        "expected_event_type": "campaign_attack",
        "expected_event_date": "2026-07-14",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["allegation", "actor_statement"],
        "expected_cluster_key": "74",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [965, 1250, 2114],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "謝龍介質疑沒為毒油道歉爆口角 卓榮泰：今天是專案報告，不是專案道歉",
    },
    965: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:陈以信(台南市长候选人)",
            "action_evidence:公开表态要求赖清德道歉、卓荣泰下台",
            "negative_evidence:表态围绕全国食安/725集会，非台南市长选举直接动作",
        ],
        "expected_event_type": "campaign_attack",
        "expected_event_date": "2026-07-18",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["actor_statement"],
        "expected_cluster_key": "965",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [74, 1250, 2114],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "在野725為食安上凱道 陳以信：賴總統道歉 卓榮泰下台",
    },
    1250: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:陈以信(台南市长候选人)",
            "action_evidence:公开表态公投案立场",
            "negative_evidence:表态围绕全国性公投/核能议题，非台南市长选举",
        ],
        "expected_event_type": "policy_proposal",
        "expected_event_date": "2026-07-20",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["actor_statement"],
        "expected_cluster_key": "1250",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [74, 965],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "陳以信：公投案　國民黨中央以重啟核電優先",
    },
    2114: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:林俊宪(台南市长候选人)",
            "action_evidence:公开评论725反毒油集会与政党对决",
            "negative_evidence:评论围绕全国性集会，非台南市长选举直接动作",
        ],
        "expected_event_type": "campaign_response",
        "expected_event_date": "2026-07-26",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["actor_statement"],
        "expected_cluster_key": "2114",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [74, 965, 1250],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "725反毒油藍3本柱沒來 林俊憲：鄭麗文拉回政黨對決 還端走蔣萬安焦點",
    },
    1424: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:王定宇(台南政治人物)",
            "action_evidence:罗正方公开回应王定宇评价",
            "negative_evidence:回应内容为党内/个人评价，非台南市长选举动作",
        ],
        "expected_event_type": "campaign_response",
        "expected_event_date": "2026-07-20",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["actor_statement"],
        "expected_cluster_key": "1424",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [1833, 1824],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "羅正方：說王定宇是績效卓越的細胞 與事實並不符",
    },
    1833: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:王定宇",
            "election_evidence:派系/立委接棒传闻",
            "negative_evidence:匿名传闻，未确认台南市长选举直接动作",
        ],
        "expected_event_type": "faction_conflict",
        "expected_event_date": "2026-07-21",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["uncertain_report"],
        "expected_cluster_key": "1833",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [1424, 1824, 1122],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "傳王定宇不再選立委　要讓陳柏惟接棒？黃揚明揭民進黨派系角力",
    },
    1824: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:王定宇",
            "election_evidence:赖清德与王定宇关系传闻",
            "negative_evidence:重点新闻汇编中的传闻/媒体解读",
        ],
        "expected_event_type": "faction_conflict",
        "expected_event_date": "2026-07-21",
        "expected_date_basis": "explicit_in_title",
        "expected_assertion_kinds": ["uncertain_report", "media_interpretation"],
        "expected_cluster_key": "1824",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [1424, 1833, 1122],
        "adjudication_confidence": "low",
        "evidence_excerpt": "今日（7/21）重點新聞！...賴清德驚爆早就不爽王定宇",
    },
    827: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "region_evidence:台南",
            "election_evidence:台南议员第四选区选情初探",
            "negative_evidence:媒体分析，无新选举动作或表态",
        ],
        "expected_event_type": "campaign_event",
        "expected_event_date": "2026-07-18",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["media_interpretation"],
        "expected_cluster_key": "827",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [1978],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "選情初探／台南議員第四選區 綠營「山區巡守員」挑戰周家根基",
    },
    1978: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "region_evidence:台南",
            "election_evidence:台南市长选情结构分析",
            "negative_evidence:媒体评论，无新选举动作或表态",
        ],
        "expected_event_type": "campaign_event",
        "expected_event_date": "2026-07-21",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["media_interpretation"],
        "expected_cluster_key": "1978",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [827],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "黃博郎觀點》台南市長選情冷清背後的結構賽局",
    },
    1122: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "actor_evidence:王定宇",
            "election_evidence:民进党权力核心重组/交棒",
            "negative_evidence:党内组织变动，非台南市长选举直接动作",
        ],
        "expected_event_type": "faction_conflict",
        "expected_event_date": "2026-07-19",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["unknown"],
        "expected_cluster_key": "1122",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [1833, 1824],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "綠營權力核心重組！泛賴系中常委強勢過半 王定宇交棒林智鴻",
    },
    264: {
        "relevance_label": "irrelevant",
        "relevance_reasons": [
            "election_evidence:民进党全代会将向县市长参选人授战旗",
            "negative_evidence:全国性党大会预告，未点名台南，无台南市长选举直接动作或表态",
        ],
        "expected_event_type": "party_integration",
        "expected_event_date": "2026-07-19",
        "expected_date_basis": "explicit_in_title",
        "expected_assertion_kinds": ["planned_action"],
        "expected_cluster_key": "264",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [966, 1017, 1087],
        "adjudication_confidence": "high",
        "evidence_excerpt": "民進黨全代會19日登場　賴清德授戰旗給縣市長參選人",
    },
    325: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "election_evidence:选举官网整合19县市长参选人政见",
            "negative_evidence:全国性选举宣传，无台南市长选举直接动作",
        ],
        "expected_event_type": "party_integration",
        "expected_event_date": "",
        "expected_date_basis": "unknown",
        "expected_assertion_kinds": ["planned_action"],
        "expected_cluster_key": "325",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [264, 966, 1017],
        "adjudication_confidence": "low",
        "evidence_excerpt": "民進黨全代會將登場！選舉官網整合19縣市長參選人政見",
    },
    966: {
        "relevance_label": "irrelevant",
        "relevance_reasons": [
            "election_evidence:民进党全代会明日登场",
            "negative_evidence:全国性党大会预告，未点名台南",
        ],
        "expected_event_type": "party_integration",
        "expected_event_date": "2026-07-19",
        "expected_date_basis": "explicit_in_title",
        "expected_assertion_kinds": ["planned_action"],
        "expected_cluster_key": "966",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [264, 325, 1017],
        "adjudication_confidence": "high",
        "evidence_excerpt": "民進黨全代會明登場 蔡英文連3年缺席、陳水扁會到",
    },
    1017: {
        "relevance_label": "irrelevant",
        "relevance_reasons": [
            "election_evidence:民进党全代会19日登场、党职改选",
            "negative_evidence:全国性党大会预告，未点名台南",
        ],
        "expected_event_type": "party_integration",
        "expected_event_date": "2026-07-19",
        "expected_date_basis": "explicit_in_title",
        "expected_assertion_kinds": ["planned_action"],
        "expected_cluster_key": "1017",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [264, 325, 966],
        "adjudication_confidence": "high",
        "evidence_excerpt": "民進黨全代會19日登場　黨職改選牽動權力布局",
    },
    1087: {
        "relevance_label": "irrelevant",
        "relevance_reasons": [
            "election_evidence:民进党全代会赖清德授战旗",
            "negative_evidence:全国性党大会，未点名台南",
        ],
        "expected_event_type": "party_integration",
        "expected_event_date": "2026-07-19",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["observed_fact"],
        "expected_cluster_key": "1087",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [264, 966, 1017],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "民進黨全代會　賴清德授戰旗展團結氣勢、首邀駐台使節",
    },
    1124: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "election_evidence:民进党中评委选举结果",
            "negative_evidence:全国性党内选举，与台南市长选举无直接动作",
        ],
        "expected_event_type": "party_integration",
        "expected_event_date": "2026-07-19",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["observed_fact"],
        "expected_cluster_key": "1124",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "民進黨11席中評委出爐！ 邱志偉當選主委",
    },
    1210: {
        "relevance_label": "contextual",
        "relevance_reasons": [
            "election_evidence:民进党全代会领衔拼选战",
            "negative_evidence:全国性党大会，未点名台南",
        ],
        "expected_event_type": "party_integration",
        "expected_event_date": "2026-07-20",
        "expected_date_basis": "inferred_from_publication",
        "expected_assertion_kinds": ["planned_action"],
        "expected_cluster_key": "1210",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "medium",
        "evidence_excerpt": "民進黨全代會登場》領軍拚選戰 賴推3原則爭取人民支持",
    },
    189: {
        "relevance_label": "irrelevant",
        "relevance_reasons": ["duplicate_normalized_url:与89同文", "negative_evidence:全国性评论"],
        "expected_event_type": "",
        "expected_event_date": "",
        "expected_date_basis": "",
        "expected_assertion_kinds": [],
        "expected_cluster_key": "dup_udn_9626502",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "high",
        "evidence_excerpt": "【重磅快評】民進黨無理卡桃園2年 藉輝達找到了下台階",
    },
    89: {
        "relevance_label": "irrelevant",
        "relevance_reasons": ["duplicate_normalized_url:与189同文", "negative_evidence:全国性评论"],
        "expected_event_type": "",
        "expected_event_date": "",
        "expected_date_basis": "",
        "expected_assertion_kinds": [],
        "expected_cluster_key": "dup_udn_9626502",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "high",
        "evidence_excerpt": "【重磅快評】民進黨無理卡桃園2年 藉輝達找到了下台階",
    },
    67: {
        "relevance_label": "irrelevant",
        "relevance_reasons": ["duplicate_normalized_url:与64同文", "negative_evidence:花莲市长选举"],
        "expected_event_type": "",
        "expected_event_date": "",
        "expected_date_basis": "",
        "expected_assertion_kinds": [],
        "expected_cluster_key": "dup_udn_9627393",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "high",
        "evidence_excerpt": "民眾黨傳徵召戴于文戰花蓮市長 魏嘉彥：積極推動選務爭取支持",
    },
    167: {
        "relevance_label": "irrelevant",
        "relevance_reasons": ["duplicate_normalized_url:与64/67同文", "negative_evidence:花莲市长选举"],
        "expected_event_type": "",
        "expected_event_date": "",
        "expected_date_basis": "",
        "expected_assertion_kinds": [],
        "expected_cluster_key": "dup_udn_9627393",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "high",
        "evidence_excerpt": "民眾黨傳徵召戴于文戰花蓮市長 魏嘉彥：積極推動選務爭取支持",
    },
    64: {
        "relevance_label": "irrelevant",
        "relevance_reasons": ["duplicate_normalized_url:与67/167同文", "negative_evidence:花莲市长选举"],
        "expected_event_type": "",
        "expected_event_date": "",
        "expected_date_basis": "",
        "expected_assertion_kinds": [],
        "expected_cluster_key": "dup_udn_9627393",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "high",
        "evidence_excerpt": "民眾黨傳徵召戴于文戰花蓮市長 魏嘉彥：積極推動選務爭取支持",
    },
    2118: {
        "relevance_label": "irrelevant",
        "relevance_reasons": ["duplicate_normalized_url:与2058同文", "negative_evidence:全国蓝白合作表态"],
        "expected_event_type": "",
        "expected_event_date": "",
        "expected_date_basis": "",
        "expected_assertion_kinds": [],
        "expected_cluster_key": "dup_cna_udn_20260726_cooperation",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "high",
        "evidence_excerpt": "談藍白合作 柯文哲：民眾黨說不定有更強總統候選人",
    },
    2058: {
        "relevance_label": "irrelevant",
        "relevance_reasons": ["duplicate_normalized_url:与2118同文", "negative_evidence:全国蓝白合作表态"],
        "expected_event_type": "",
        "expected_event_date": "",
        "expected_date_basis": "",
        "expected_assertion_kinds": [],
        "expected_cluster_key": "dup_cna_udn_20260726_cooperation",
        "expected_formal_event_ids": [],
        "must_not_merge_with_article_ids": [],
        "adjudication_confidence": "high",
        "evidence_excerpt": "談藍白合作 柯文哲：民眾黨說不定有更強總統候選人",
    },
}


DEFAULT = {
    "relevance_label": "irrelevant",
    "relevance_reasons": [
        "negative_evidence:未发现台南市长选举直接动作或围绕台南市长选举的公开表态",
    ],
    "expected_event_type": "",
    "expected_event_date": "",
    "expected_date_basis": "",
    "expected_assertion_kinds": [],
    "expected_cluster_key": "",
    "expected_formal_event_ids": [],
    "must_not_merge_with_article_ids": [],
    "adjudication_confidence": "low",
    "evidence_excerpt": "",
}


def main():
    base = json.loads((QC / "july_2026_article_adjudication.base.json").read_text(encoding="utf-8"))
    articles = []
    for a in base["articles"]:
        aid = a["article_id"]
        override = OVERRIDES.get(aid, DEFAULT)
        entry = {
            "article_id": aid,
            "title": a["title"],
            "summary": a["summary"],
            "source_name": a["source_name"],
            "url": a["url"],
            "published_at": a["published_at"],
            "classifier_match": a["classifier_match"],
            "relevance_label": override["relevance_label"],
            "relevance_reasons": list(override["relevance_reasons"]),
            "expected_event_type": override["expected_event_type"],
            "expected_event_date": override["expected_event_date"],
            "expected_date_basis": override["expected_date_basis"],
            "expected_assertion_kinds": list(override["expected_assertion_kinds"]),
            "expected_cluster_key": override["expected_cluster_key"],
            "expected_formal_event_ids": list(override["expected_formal_event_ids"]),
            "must_not_merge_with_article_ids": list(override["must_not_merge_with_article_ids"]),
            "adjudication_confidence": override["adjudication_confidence"],
            "evidence_excerpt": override["evidence_excerpt"] or a["title"][:80],
        }
        articles.append(entry)

    (QC / "july_2026_article_adjudication.json").write_text(
        json.dumps({"scope": base["scope"], "matched_count": len(articles), "articles": articles},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Markdown ledger
    lines = ["# 2026年7月 台南选情匹配新闻人工裁决底稿", ""]
    for a in articles:
        lines.append(f"## {a['article_id']}｜{a['title']}")
        lines.append("")
        lines.append(f"- 来源：{a['source_name']}｜{a['published_at']}")
        lines.append(f"- 相关性裁决：{a['relevance_label']}")
        lines.append(f"- 理由：{'；'.join(a['relevance_reasons'])}")
        lines.append(f"- 预期事件类型：{a['expected_event_type'] or '未知'}")
        lines.append(f"- 预期事件日期：{a['expected_event_date'] or '未知'}（{a['expected_date_basis'] or '无'}）")
        lines.append(f"- 预期Assertion：{','.join(a['expected_assertion_kinds']) or '无'}")
        lines.append(f"- 预期聚类键：{a['expected_cluster_key'] or '独立'}")
        lines.append(f"- 裁决置信度：{a['adjudication_confidence']}")
        lines.append(f"- 证据摘录：{a['evidence_excerpt']}")
        lines.append("")
    (QC / "july_2026_article_adjudication.md").write_text("\n".join(lines), encoding="utf-8")

    # Cluster adjudication: group by expected_cluster_key
    clusters = {}
    for a in articles:
        key = a["expected_cluster_key"] or f"single_{a['article_id']}"
        clusters.setdefault(key, []).append(a["article_id"])
    cluster_adj = {
        "cluster_count": len(clusters),
        "clusters": [
            {
                "cluster_key": k,
                "article_ids": v,
                "relevance_labels": sorted({a["relevance_label"] for a in articles if (a["expected_cluster_key"] or f"single_{a['article_id']}") == k}),
            }
            for k, v in sorted(clusters.items())
        ],
    }
    (QC / "july_2026_cluster_adjudication.json").write_text(
        json.dumps(cluster_adj, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Formal match adjudication
    formal_adj = {
        "known_duplicate_article_ids": [],
        "possible_related_article_ids": [74, 965, 1250, 2114],
        "notes": [
            "7月9条正式事件中，79篇新闻无逐字重复；74/965/1250/2114为候选人围绕全国议题的表态，仅可能与正式事件相关而非重复。",
        ],
    }
    (QC / "july_2026_formal_match_adjudication.json").write_text(
        json.dumps(formal_adj, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from collections import Counter
    labels = Counter(a["relevance_label"] for a in articles)
    summary = {
        "matched_count": len(articles),
        "label_counts": dict(labels),
        "direct_event_count": labels.get("direct_event", 0),
        "direct_statement_count": labels.get("direct_statement", 0),
        "contextual_count": labels.get("contextual", 0),
        "irrelevant_count": labels.get("irrelevant", 0),
        "collection_error_count": labels.get("collection_error", 0),
        "duplicate_groups": 3,
        "cluster_count": len(clusters),
    }
    (QC / "adjudication_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
