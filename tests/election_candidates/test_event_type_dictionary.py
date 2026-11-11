from __future__ import annotations

import pytest

from app.election_candidates.event_type_dictionary import classify_event_type

from .conftest import make_config


@pytest.mark.parametrize(
    "title,expected",
    [
        ("民進黨台南市長初選起跑", "primary_procedure"),
        ("陳亭妃完成登記參選台南市長", "primary_registration"),
        ("台南市長初選辯論登場", "primary_debate"),
        ("陳亭妃初選勝出", "primary_result"),
        ("民進黨正式提名陳亭妃參選台南市長", "party_nomination"),
        ("謝龍介啟動台南市長競選準備", "campaign_launch"),
        ("謝龍介安南區宮廟聯合拜票", "campaign_event"),
        ("陳亭妃競選總部成立", "campaign_organization"),
        ("陳亭妃成立大台南後援會", "support_organization"),
        ("賴清德與陳亭妃同框看板啟用", "joint_campaign"),
        ("謝龍介：藍白合作有助在野整合", "alliance_proposal"),
        ("藍白就台南市長人選協調", "alliance_coordination"),
        ("民進黨台南黨內整合", "party_integration"),
        ("王定宇交棒林智鴻 派系角力", "faction_conflict"),
        ("謝龍介質疑對手賄選", "campaign_attack"),
        ("政院駁斥謝龍介指控不實", "campaign_response"),
        ("台南市長最新民調公布", "poll_release"),
        ("陳亭妃視察三爺溪", "governance_event"),
        ("台南市府防災整備", "disaster_response"),
        ("陳亭妃提出七大市政政見", "policy_proposal"),
        ("賴清德為陳亭妃站台", "endorsement"),
        ("王世堅接任競選總部副主委", "personnel_appointment"),
        ("黃博郎觀點 台南市長選情冷清", "unknown"),
    ],
)
def test_event_type_dictionary(tmp_path, title, expected):
    config = make_config(tmp_path)
    assert classify_event_type(title, "", config) == expected
