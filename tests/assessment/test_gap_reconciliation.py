from app.assessment.gap_reconciliation import reconcile_gaps, match_stable_gap


def _gaps(**kw):
    defaults = dict(
        previous_gap_texts=["2026年3月12日以后缺少可比的公开追踪民调"],
        current_gap_texts=["3月13日至7月27日存在公开民调空窗（RT01 negative finding）"],
        gap_reconciliation=[
            {
                "gap_id": "gap_polling",
                "v2_gap_text": "2026年3月12日以后缺少可比的公开追踪民调",
                "previous_status": "active",
                "current_status": "reframed",
                "new_formal_evidence_ids": [],
            }
        ],
        backlog=[],
        blocker_triage={},
    )
    defaults.update(kw)
    return reconcile_gaps(**defaults)


class TestGapReconciliation:
    def test_renamed_same_stable_not_new(self):
        r = _gaps()
        assert r["new_gaps"] == []
        assert all(g["stable_gap_id"] == "gap_polling" for g in r["gap_changes"])

    def test_renamed_not_resolved(self):
        r = _gaps()
        assert r["resolved_gaps"] == []
        assert r["gap_changes"][0]["change_type"] == "reframed"

    def test_reframed_not_resolved(self):
        r = _gaps()
        assert r["gap_changes"][0]["change_type"] == "reframed"
        assert r["gap_changes"][0]["material_for_report"] is False

    def test_narrowed_not_resolved(self):
        r = _gaps(
            current_gap_texts=["民调空窗持续"],
            gap_reconciliation=[
                {
                    "gap_id": "gap_polling",
                    "v2_gap_text": "2026年3月12日以后缺少可比的公开追踪民调",
                    "previous_status": "active",
                    "current_status": "narrowed",
                    "new_formal_evidence_ids": ["evt_x"],
                }
            ],
        )
        assert r["gap_changes"][0]["change_type"] == "narrowed"
        assert r["resolved_gaps"] == []

    def test_resolved_requires_evidence_and_completed(self):
        r = _gaps(
            current_gap_texts=["民调缺口已补齐"],
            gap_reconciliation=[
                {
                    "gap_id": "gap_polling",
                    "v2_gap_text": "2026年3月12日以后缺少可比的公开追踪民调",
                    "previous_status": "missing",
                    "current_status": "resolved",
                    "new_formal_evidence_ids": ["evt_new_poll"],
                }
            ],
        )
        assert r["gap_changes"][0]["change_type"] == "resolved"
        assert r["resolved_gaps"] == ["gap_polling"]

    def test_new_stable_gap(self):
        r = _gaps(
            previous_gap_texts=[],
            current_gap_texts=["2—3月局部事实连续性仍有缺口（RT07 non_blocking）"],
            gap_reconciliation=[],
            backlog=[
                {
                    "research_task_id": "RT07",
                    "title": "2026年2月至3月台南竞选活动断层",
                    "coverage_status": "missing",
                    "current_evidence_ids": [],
                }
            ],
            blocker_triage={
                "rt07_feb_mar_gap": {
                    "classification": "non_blocking_gap",
                    "snapshot_handling": "列为已知限制：2—3月局部事实连续性仍有缺口",
                }
            },
        )
        assert "rt07_feb_mar_gap" in r["new_gaps"]

    def test_split_merge_aliases_keep_mapping(self):
        aliases = {
            "gap_x": ["旧甲", "旧乙"],
        }
        assert match_stable_gap("旧甲", aliases) == "gap_x"
        assert match_stable_gap("旧乙", aliases) == "gap_x"

