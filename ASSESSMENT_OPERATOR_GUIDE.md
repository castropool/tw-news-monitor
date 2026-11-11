# 台南选情研判 操作手册（V1 生产版）

本手册只写日常操作，不解释内部实现。

## 平时需要做什么

不需要做什么。系统自动采集新闻、自动整理候选事实。

你只需要在有空时审核候选事实（Candidate），确保 9 日 / 22 日之前
事实审核已经覆盖到报告周期结束日（complete_review）。

## 1. 审核候选事实

在候选事实审核界面完成 approve / reject / context_only / hold 裁决，
并在审核完成后执行 complete_review（推进 facts_cutoff）。

facts_cutoff 表示“人工已经完整审核到哪一天”。报告生成前系统会检查：
`facts_cutoff >= 报告周期结束日`，不满足就不出报告（这不是故障）。

## 2. 报告什么时候出来

每月 9 日 09:00 与 22 日 09:00（Asia/Taipei）系统自动生成：

- 9 日 → 报告周期为上月 16 日至上月末
- 22 日 → 报告周期为当月 1 日至 15 日

报告生成后进入“人工终审”状态，等待你阅读确认。

## 3. 报告在哪里

报告目录：

```text
data/election_assessment/tainan_2026/production/
```

- 每期一个子目录，如 `20260716_20260731/`
- 该目录里有：
  - `final_article.md` —— 研判文章正文（Markdown）
  - `final_article.docx` —— 研判文章 Word（双击即可打开阅读）
  - `ASSESSMENT_RESEARCH_PACK.md` —— 本期研究包（可单独上传 ChatGPT 人工生成）
  - `research_pack.json` / `analysis_plan.json` / `run_metadata.json` —— 后台数据

最新一期的快速入口（自动复制）：

```text
data/election_assessment/tainan_2026/production/FINAL_ASSESSMENT_PREVIEW.docx
```

## 4. 打开 Word

双击 `FINAL_ASSESSMENT_PREVIEW.docx`，或进入当期目录打开
`final_article.docx`。Word 正文就是最终文章，只含标题、报告周期与正文，
没有内部 ID 和技术 JSON。

## 5. 查看最新报告状态

```text
python -m app.assessment.research_driven.status
python -m app.assessment.research_driven.review list
python -m app.assessment.research_driven.review show tainan_mayoral_2026__20260716__20260731
```

`show` 会给出报告周期、facts_cutoff、模型、事实安全检查结果、
review_notes（提醒哪些判断证据较弱、哪些民调较旧）和 Word 路径。

## 6. 批准 / 拒绝报告

```text
python -m app.assessment.research_driven.review approve <run_key> --reviewer 你的名字
python -m app.assessment.research_driven.review reject <run_key> --reviewer 你的名字 --reason 原因
```

批准前系统会校验文章哈希，防止审核前后内容变化。

## 7. 手动生成（非调度日）

```text
python -m app.assessment.research_driven.scheduled --period-start 2026-07-16 --period-end 2026-07-31 --trigger-type manual
```

## 8. 强制重新生成

同一周期已生成后不会自动重跑。确认需要重来：

```text
python -m app.assessment.research_driven.scheduled --period-start 2026-07-16 --period-end 2026-07-31 --trigger-type manual --force-regenerate
```

## 9. 报告没出来时怎么判断

```text
powershell -ExecutionPolicy Bypass -File scripts\status_r2_assessment_task.ps1
```

查看计划任务是否运行；日志在 `data/logs/r2_scheduler.log`：

- `REPORT_PERIOD_NOT_READY`：事实审核尚未覆盖报告周期结束日，先完成事实审核。
- `SKIPPED_ALREADY_GENERATED`：本期已有报告，不会重复生成。
- `GENERATED_READY_FOR_REVIEW`：报告已生成，等待人工终审。
- `MACHINE_HARD_BLOCKED`：事实安全检查发现严重问题（如未来事件泄漏、虚构民调数字），查看 `show <run_key>`。
- `GENERATION_FAILED`：模型调用失败；研究包已保留，可把
  `ASSESSMENT_RESEARCH_PACK.md` 直接上传 ChatGPT 人工生成文章。

## 10. 计划任务

```text
powershell -ExecutionPolicy Bypass -File scripts\install_r2_assessment_task.ps1 -DryRun   # 预览
powershell -ExecutionPolicy Bypass -File scripts\install_r2_assessment_task.ps1 -Force   # 安装/更新
powershell -ExecutionPolicy Bypass -File scripts\uninstall_r2_assessment_task.ps1        # 卸载
```

任务名：`Tainan Election Assessment`，每月 9 日、22 日 09:00，
只生成到“人工终审”，绝不自动发送飞书。

## 终审检查卡（阅读 Word 时逐项核对）

```text
□ 标题是不是判断型标题（有主体动作、格局变化、后续影响），而不是“选情分析/综述”
□ 一、核心判断是否直接回答“本期最重要的政治变化是什么”
□ 二、本期关键变化是否为 3-5 个事实群，且每条先给概括判断
□ 三、因果链是否解释了“为什么现在发生”
□ 四、主要阵营研判是否围绕组织权、人事权、资源、派系，而不只是曝光量
□ 六、趋势判断是否写明了具体方向、变量与风险，而不是“竞争将更激烈”
□ 七、风险与证据限制是否写明哪些判断证据较强、哪些是推断、民调是否过时
□ 人物、日期、民调数字是否与研究包一致；旧民调是否带日期与局限
□ 未经证实的主张是否保留了主体归属（“谢龙介称”）
```

以上任一项明显不满足，可拒绝并重新生成；轻微措辞问题不必阻塞。
