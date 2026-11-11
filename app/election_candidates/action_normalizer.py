"""Canonical action normalization for clustering and duplicate checking."""

from __future__ import annotations


ACTION_PATTERNS: list[tuple[str, list[str]]] = [
    ("primary_result", ["初選結果", "初选结果", "勝出", "胜出"]),
    ("primary_procedure", ["初選", "初选"]),
    ("poll_release", ["民調", "民调", "支持度"]),
    ("support_organization", ["成立後援會", "成立后援会", "後援會", "后援会", "支持會", "支持会"]),
    ("joint_campaign_display", ["合體看板", "合体看板", "聯合看板", "联合看板", "同框看板"]),
    ("party_nomination", ["正式提名", "提名", "徵召", "征召"]),
    ("registration", ["完成登記", "完成登记", "登記參選", "登记参选", "領表", "领表"]),
    ("campaign_rally", ["聯合造勢", "联合造势", "造勢", "造势"]),
    ("campaign_launch", ["宣布參選", "宣布参选", "啟動競選", "启动竞选", "競選準備", "竞选准备"]),
    ("campaign_visit", ["拜會", "拜会", "參訪", "参访", "會勘", "会勘", "視察", "视察", "跑行程"]),
    ("alliance_proposal", ["藍白合作", "蓝白合作", "藍白合", "蓝白合", "在野整合", "協調禮讓", "协调礼让"]),
    ("campaign_response", ["回應", "回应", "澄清", "駁斥", "驳斥", "否認", "否认"]),
    ("campaign_attack", ["指控", "批評", "批评", "質疑", "质疑", "抨擊", "砲轟", "炮轰"]),
    ("alliance_coordination", ["協調", "协调", "會商", "会商", "禮讓", "礼让"]),
    ("support", ["宣布支持", "宣布支持", "表態支持", "表态支持", "站台"]),
    ("campaign_event", ["競選活動", "竞选活动", "掃街", "扫街", "拜票", "座談會", "座谈会"]),
]


def normalize_action(text: str, config=None) -> tuple[str, str]:
    """Return (canonical_action, matched_phrase)."""
    if not text:
        return "", ""
    for action, phrases in ACTION_PATTERNS:
        for phrase in phrases:
            if phrase in text:
                return action, phrase
    return "", ""


def action_family(action: str) -> str:
    """Map related actions to a family for loose duplicate matching."""
    families = {
        "support": "support",
        "support_organization": "support",
        "campaign_rally": "campaign",
        "campaign_event": "campaign",
        "campaign_launch": "campaign",
        "campaign_visit": "campaign",
        "joint_campaign_display": "joint_campaign",
        "joint_campaign": "joint_campaign",
        "alliance_proposal": "alliance",
        "alliance_coordination": "alliance",
    }
    return families.get(action, action)
