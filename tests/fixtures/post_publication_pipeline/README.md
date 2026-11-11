# Phase 3/3.5 隔离测试 Fixture

本目录仅用于隔离测试，不引用真实 `news.db`、真实 `election_context.db` 或真实候选库。
- `golden_coverage_cases.json`：20 组 Coverage 黄金案例（Phase 3.5 权威语义）
- `golden_snapshot_cases.json`：20 组 Snapshot 黄金案例
- `golden_trigger_cases.json`：15 组 Assessment Trigger 黄金案例
- `case_catalog.json`：端到端 Cases A–L 定义

另见 `tests/fixtures/election_candidates/coverage_semantic_golden_v1.json`：
30 组 Coverage 语义黄金案例（20 calibration + 10 holdout）。

运行 `python scripts/build_phase3_fixtures.py` 可确定性重建。