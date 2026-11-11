# 台南选情自动报告系统 — 离线发布候选冻结声明

```text
release_name=tainan-assessment-offline-rc1
release_status=offline_release_candidate
production_status=blocked_pending_live_validation
```

## 冻结声明

1. 全部离线开发已经完成，本轮之后不再新增业务功能。
2. 当前版本停止新增业务功能，仅保留缺陷修复与安全处置通道。
3. 当前版本可进行 development 和 dry-run 运行，且已通过对应回归。
4. 当前版本**不可**用于真实 production：
   - `production_llm_ready=false`
   - `live_deepseek_test=not_run`
   - 飞书旧凭据轮换尚未确认（`feishu_credentials_rotated_after_incident=false`）
   - 真实计划任务未安装
5. 生产启用必须完成真实 DeepSeek、飞书及计划任务验收，验收流程见 `DEPLOYMENT_HANDOFF_CHECKLIST.md`，结果记录见 `DEPLOYMENT_ACCEPTANCE_TEMPLATE.md`。
6. Mock 结果不得作为生产验收依据；生产启用仅以真实环境验收为准。

## 冻结基线

```text
pytest=1095 passed, 4 skipped, 0 failed
formal_data_frozen=true
formal_data_unchanged=true
evidence_business_semantics_unchanged=true
deployment_bundle_valid=true
release_archive_valid=true
security_scan_passed=true
development_pipeline_ready=true
dry_run_pipeline_ready=true
production_pipeline_blocked=true
rotation_required=true
production_delivery_blocked_until_rotation_acknowledged=true
```

## 相关文件

- `release/frozen_formal_data_manifest.json`
- `release/release_manifest.json`
- `release/DEPLOYMENT_HANDOFF_CHECKLIST.md`
- `release/DEPLOYMENT_ACCEPTANCE_TEMPLATE.md`
- `dist/releases/tainan-assessment-offline-rc1.zip`

冻结日期：2026-08-06
