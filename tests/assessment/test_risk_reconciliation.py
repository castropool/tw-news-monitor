from app.assessment.risk_reconciliation import classify_risks


def _classify(**kw):
    defaults = dict(
        current_risks=[
            {"risk": "民调空窗导致实时支持度无法核验", "risk_type": "polling_staleness"},
        ],
        previous_risks=[
            {"risk": "现有正式民调最新调查结束于2026年3月12日，无法直接代表7月实时支持度", "risk_type": "polling_staleness"},
        ],
        previous_limitations=["民调空窗2026-03-13至07-27"],
        previous_supporting_event_ids={"e1"},
    )
    defaults.update(kw)
    return classify_risks(**defaults)


class TestRiskReconciliation:
    def test_long_standing_limitation_not_new_risk(self):
        r = _classify(
            current_risks=[
                {"risk": "丹娜丝和三爷溪治理议题可能改变地方治理评价", "risk_type": "governance", "supporting_event_ids": []}
            ],
            previous_risks=[],
            previous_limitations=["丹娜丝灾后治理影响尚未专项核查", "三爷溪预算攻防尚未专项核查"],
        )
        assert r["risk_changes"][0]["change_type"] == "existing_limitation_carried_forward"
        assert r["newly_emerged_risk_count"] == 0

    def test_new_event_supported_risk_is_newly_emerged(self):
        r = _classify(
            current_risks=[
                {"risk": "全新的外部干预风险", "risk_type": "new_type", "supporting_event_ids": ["e_new"]}
            ],
            previous_risks=[],
            previous_limitations=[],
            previous_supporting_event_ids={"e1"},
        )
        assert r["risk_changes"][0]["change_type"] == "newly_emerged_risk"
        assert r["risk_changes"][0]["material_for_report"] is True
        assert r["newly_emerged_risks"] == [r["risk_changes"][0]["risk_id"]]

    def test_carried_forward_classified(self):
        r = _classify(
            current_risks=[
                {"risk": "长期治理议题持续存在", "risk_type": "governance", "supporting_event_ids": []}
            ],
            previous_risks=[],
            previous_limitations=["长期治理议题"],
        )
        assert r["risk_changes"][0]["change_type"] == "existing_limitation_carried_forward"
        assert r["carried_forward_risks"] == [r["risk_changes"][0]["risk_id"]]

    def test_risk_count_from_array(self):
        r = _classify(
            current_risks=[
                {"risk": "风险甲", "risk_type": "t1"},
                {"risk": "风险乙", "risk_type": "t2"},
            ],
            previous_risks=[],
            previous_limitations=[],
        )
        assert r["risk_change_count"] == 2
        assert len(r["risk_changes"]) == 2

    def test_existing_risk_reaffirmed(self):
        r = _classify()
        assert r["risk_changes"][0]["change_type"] in (
            "existing_risk_reaffirmed",
            "risk_reframed",
        )
        assert r["risk_changes"][0]["previously_present"] is True
        assert r["risk_changes"][0]["material_for_report"] is False

