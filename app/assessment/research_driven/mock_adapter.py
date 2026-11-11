"""确定性 Mock Adapter（无网络、无密钥），供测试与离线验收使用。

fixture 由 user_payload 中的 ``_mock_fixture`` 键控制（生成器注入），
Mock 不读环境变量，输出完全由输入决定。
"""

from __future__ import annotations

import json
from typing import Any


class MockAdapter:
    def __init__(self) -> None:
        pass

    def complete(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        json_mode: bool = True,
    ) -> dict[str, Any]:
        fixture = str(user_payload.get("_mock_fixture") or "valid")
        if fixture == "api_failure":
            raise RuntimeError("mock api failure")
        pack = user_payload.get("research_pack") or {}
        if fixture == "invalid_json":
            return {"raw_text": "this is not json {{{"}
        if fixture == "valid":
            return self._valid(pack)
        if fixture == "future_leakage":
            out = self._valid(pack)
            body = out["structured"]["final_article"]["body"]
            out["structured"]["final_article"]["body"] = (
                body + "\n\n另据报道，12月18日赖清德宣布改组台南市党部。"
            )
            return out
        if fixture == "fabricated_poll":
            out = self._valid(pack)
            body = out["structured"]["final_article"]["body"]
            out["structured"]["final_article"]["body"] = (
                body + "\n\n最新民调显示陈亭妃支持度52.7%。"
            )
            return out
        if fixture == "missing_sections":
            out = self._valid(pack)
            out["structured"]["final_article"]["body"] = "本期选情平稳，各方按既定节奏推进。"
            return out
        raise ValueError(f"unknown mock fixture: {fixture}")

    @staticmethod
    def _valid(pack: dict) -> dict[str, Any]:
        period = pack.get("period") or {}
        events = pack.get("period_events") or []
        event_titles = "；".join(
            (e.get("title") or "")[:24] for e in events[:3]
        ) or "本期正式事件"
        structured = {
            "analysis_plan": {
                "primary_thesis": {
                    "judgment": "绿营整合由表态支持进入组织协同，但赖系对关键人事与资源的控制使其" "胜选尚未完全掌权；谢龙介借绿营权力过渡期抢攻整合裂缝。",
                    "evidence_strength": "HIGH",
                    "supporting_event_ids": [e.get("event_id") for e in events[:3] if e.get("event_id")],
                    "why_it_matters": "本期变化的本质是权力过渡，而非单纯的竞选动作。",
                },
                "key_changes": [
                    {
                        "rank": 1,
                        "change": "陈亭妃整合由表态转向组织化协作",
                        "category": "派系变化",
                        "change_tag": "strengthened",
                        "supporting_event_ids": [e.get("event_id") for e in events[:2] if e.get("event_id")],
                        "news_action_or_structural": "structural",
                        "evidence_strength": "HIGH",
                    }
                ],
                "candidate_theses": [
                    {
                        "judgment": "陈亭妃正由初选胜者向组织控盘者转化",
                        "supporting_facts": ["本期多项整合与组织动作方向一致"],
                        "counterevidence": "赖系关键资源控制未见让渡的正式证据",
                        "why_important": "决定绿营能否把初选优势转化为选票优势",
                        "future_implication": "若组织整合完成，绿营基本盘动员能力将显著增强",
                    }
                ],
                "causal_chain": [
                    {"layer": 1, "level_name": "直接政治原因", "analysis": "初选结束后的权力过渡窗口打开"},
                    {"layer": 2, "level_name": "权力意图", "analysis": "陈亭妃需要组织控制权，赖系需要保住影响"},
                ],
                "power_relations": [
                    {
                        "relation": "陈亭妃与赖系",
                        "analysis": "候选人取得提名但组织资源仍由赖系掌握",
                        "supporting_event_ids": [e.get("event_id") for e in events[:1] if e.get("event_id")],
                    }
                ],
                "camp_intents": [
                    {
                        "actor": "陈亭妃",
                        "action": "整合地方组织",
                        "likely_goal": "确立组织主导权",
                        "target_audience": "绿营桩脚与地方议员",
                        "beneficiary": "陈亭妃阵营",
                        "constrained_party": "赖系地方系统",
                        "nature": "进攻",
                        "short_term_effect": "整合表态增多",
                        "medium_term_effect": "若成功则基本盘动员增强",
                    }
                ],
                "trend_outlook": {
                    "short_term": "未来半个月仍以组织整合与人事安排为主",
                    "medium_term": "1-3个月内关键看赖系是否让渡关键人事",
                    "key_turning_conditions": ["赖系是否公开让渡关键人事", "蓝白是否形成联合助选"],
                    "who_has_initiative": "陈亭妃在竞选主轴上有主动权，赖系在组织资源上仍有制约力",
                    "biggest_risk": "绿营内部整合反复导致基本盘动员不畅",
                },
                "previous_outlook_verification": None,
                "camp_status": [
                    {"camp": "陈亭妃阵营", "status_change": "strengthened", "assessment": "整合进入组织化阶段"},
                ],
                "risk_notes": ["民调空窗：正式民调截止于报告期前"],
            },
            "final_article": {
                "title": "陈亭妃加速收拢地方组织确立主导，赖系转守关键人事保持制约，谢龙介借绿营过渡期抢攻裂缝",
                "body": (
                    "# 台南选情研判（{start}至{end}）\n\n"
                    "## 一、核心判断\n\n"
                    "本期最重要的政治变化是：绿营整合由表态支持进入组织协同阶段，"
                    "但陈亭妃作为初选胜者尚未完全掌握地方组织与关键人事；"
                    "赖系以对组织资源的控制保持制约；谢龙介则试图利用这一权力过渡期，"
                    "将绿营内部裂缝转化为蓝营的选举突破口。\n\n"
                    "## 二、本期关键变化\n\n"
                    "一是整合由表态支持转向组织控制。本期正式事件显示，"
                    "陈亭妃阵营的动作已从争取表态转向组建可动员的组织网络。\n\n"
                    "二是赖系的回应方式由竞争转向人事控制。"
                    "中央层面保持公开支持的同时，关键资源与人事安排仍未见让渡的正式证据。\n\n"
                    "三是谢龙介的竞选策略由基本盘经营转向绿营裂缝利用。\n\n"
                    "## 三、因果链与权力逻辑\n\n"
                    "第一层，直接政治原因：初选结束、提名确定后，绿营进入权力过渡期。\n\n"
                    "第二层，权力意图：陈亭妃需要把初选胜利转化为组织控制，"
                    "赖系需要防止自身在台南的地方影响力被架空。\n\n"
                    "## 四、主要阵营研判\n\n"
                    "陈亭妃：整合提速，但组织控盘尚未完成。\n\n"
                    "赖系：转守关键人事与资源，保持隐性制约。\n\n"
                    "谢龙介：试图把绿营过渡期变成自己的整合窗口。\n\n"
                    "## 五、治理与社会议题\n\n"
                    "本期治理议题尚未成为主导选举议题，暂居次要位置。\n\n"
                    "## 六、趋势判断\n\n"
                    "未来半个月：预计仍以组织整合与人事安排为主轴。\n\n"
                    "未来1-3个月：关键变量是赖系是否在关键人事上实质让渡。\n\n"
                    "关键观察指标：绿营地方组织活动频次、蓝白联合助选是否落地。\n\n"
                    "## 七、风险与证据限制\n\n"
                    "证据较强的是整合方向与权力过渡的判断；"
                    "属于有限推断的是赖系具体人事安排；"
                    "正式民调截止于报告期之前，本期无新增正式民调，"
                    "不支持对支持度变化的任何判断。"
                ).format(
                    start=str((period.get("period_start") or "")),
                    end=str((period.get("period_end") or "")),
                ),
            },
        }
        return {
            "raw_text": json.dumps(structured, ensure_ascii=False),
            "structured": structured,
        }
