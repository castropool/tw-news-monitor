# 部署验收记录

发布候选：`tainan-assessment-production-rc2`  
部署电脑：`DESKTOP-N4R41A7`  
部署日期：`2026-08-08`  
Python：`3.12.10`  
DeepSeek：`deepseek-v4-pro`  
飞书交付模式：`app_file_upload`

| 验收项 | 结果 |
| --- | --- |
| DeepSeek 凭据配置 | PASS（中性 Live） |
| 飞书凭据轮换 | FAIL（未确认） |
| 安全审计 | PASS（无真实密钥值泄露）；轮换门禁 FAIL |
| Development preflight | PASS |
| Dry-run preflight | PASS |
| Live DeepSeek test | PASS |
| 飞书 TEST ONLY 文字与 Word | PASS |
| Production preflight | FAIL |
| 手工 production 运行 | NOT ATTEMPTED（门禁阻断） |
| 正式 Word 生成 | NOT ATTEMPTED（上游契约拒绝） |
| 正式飞书交付 | NOT ATTEMPTED |
| 9 日任务安装 | NOT INSTALLED |
| 22 日任务安装 | NOT INSTALLED |

```text
production_llm_ready: false
production_delivery_ready: false
production_pipeline_ready: false
production_ready: false
```

最终结论：**验收不通过，RC2 维持 production release candidate，不正式启用。**

验收证据见 `PRODUCTION_ACCEPTANCE_REPORT.md`、`release_manifest_phase4.json`、`final_production_manifest.json` 以及部署目录 `data/production/`。
