# 事实维护操作员指南

这份指南只讲你日常需要做的事。系统会自动采集新闻、自动筛出台南候选事实，你负责最终判断。

## 1. 查看待审核候选

在 `tw-news-monitor-simple` 目录运行：

```bat
python -m app.election_candidates.list_candidates --status review_required
```

也可以看全部状态：

```bat
python -m app.election_candidates.list_candidates
```

## 2. 查看候选详情和来源

```bat
python -m app.election_candidates.show_candidate --candidate-id <候选ID>
```

会显示标题、时间、人物、断言、来源链接、疑似重复项，以及系统建议。

## 3. 批准 / 合并 / 驳回 / 暂缓 / 需要修改

先导出审核模板：

```bat
python -m app.election_candidates.export_review_template --candidate-id <候选ID>
```

模板会写到 `data/election_candidates/tainan_2026/review_templates/<候选ID>.json`。编辑其中的：

- `decision`：`approve_new_event`（新事实）、`approve_as_subevent`（作为某个正式事件的分支）、`attach_to_existing_event`（挂到已有正式事件，需填 `target_formal_event_id`）、`reject`（驳回）、`hold`（暂缓）、`needs_edit`（需要修改）
- `reviewer`：你的名字
- `event` / `sources`：可修正日期、标题、摘要、来源

然后用统一入口提交，一次操作完成审核与（批准类）正式发布：

```bat
python -m app.election_candidates.review_and_publish --decision-file data\election_candidates\tainan_2026\review_templates\<候选ID>.json --reviewer <你的名字>
```

## 4. 批准后系统会自动做什么

批准类决定（新事件 / 分支事件 / 挂到已有事件）提交后，系统自动执行：

1. 记录你的审核决定（不可修改，只追加）
2. 生成发布预览并校验
3. 备份、暂存、bootstrap 校验
4. 原子替换正式种子并重建正式库
5. 正式状态校验
6. 刷新 Coverage / 快照 / 下游

不需要再手动执行 preview / prepare / commit / bootstrap / validate / refresh。

## 5. 完成某一天之前的审核

当你把某段日期的候选全部处理完，执行：

```bat
python -m app.election_candidates.complete_review --through 2026-08-07 --reviewer <你的名字> --update-facts-cutoff
```

程序会逐日检查：该日新闻是否已被候选管道处理完、该日候选是否全部有最终裁决。全部满足才写入“该日已审核完成”，并只连续推进 `facts_cutoff`。

## 6. 什么情况不能 complete

- 某天仍有 `pending` / `review_required` / `hold` / `needs_edit` 候选
- 某天的新闻还没有被候选管道扫描到
- 试图跳过中间未完成的日子

程序会拒绝并告诉你卡在哪一天。

## 7. 查看当前 facts_cutoff

```bat
type data\election_seed\tainan_2026\fact_coverage_20260801_v4\coverage_preflight.json
```

`facts_cutoff` 表示“已经完成人工研究/审核的连续日期边界”，不等于最新新闻日期。

## 8. 查看候选调度状态

```bat
scripts\status_candidate_monitor_task.ps1
```

会显示计划任务是否启用、上次运行时间与结果，以及最近日志。手动触发一次：

```bat
scripts\run_candidate_monitor_now.ps1
```

## 9. 发布失败如何重试

如果你已经批准，但正式发布因技术原因失败（网络、文件占用、校验失败等）：

- 你的审核决定不会丢失，候选会保持“已批准 / 发布失败”状态
- 直接用原来的审核记录重试，**不需要再次批准**：

```bat
python -m app.election_candidates.review_and_publish --review-decision-id <审核记录ID> --reviewer <你的名字>
```

审核记录 ID 可以从 `list_candidates` 详情或发布批次记录中看到。系统仍会走完整的备份、校验、暂存、提交流程，不会产生半写入的正式状态。
