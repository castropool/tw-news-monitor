# 候选事实审核操作员指南

这份指南只讲你日常需要做的事。系统会自动采集新闻、自动筛出台南（及新北）候选事实，你负责最终判断。

> 候选管线现为**只监控、不发布**：审核决定只记录在候选库
> （`data/election_candidates/<选举>/candidate_fact_pipeline.db`），不会写入正式事实库，
> 也不再刷新 Coverage / 快照。原有正式事实库与种子作为历史数据保留，候选生成时只读用于查重。
> 新北选举的所有命令加 `--config config/election_candidate_pipeline.new_taipei.yaml`。

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

## 3. 记录审核决定（批准 / 合并 / 驳回 / 暂缓 / 需要修改）

先导出审核模板：

```bat
python -m app.election_candidates.export_review_template --candidate-id <候选ID>
```

模板会写到 `data/election_candidates/tainan_2026/review_templates/<候选ID>.json`。编辑其中的：

- `decision`：`approve_new_event`（新事实）、`approve_as_subevent`（作为某个已知事件的分支）、`attach_to_existing_event`（挂到已有事件，需填 `target_formal_event_id`）、`reject`（驳回）、`hold`（暂缓）、`needs_edit`（需要修改）
- `reviewer`：你的名字
- `event` / `sources`：可修正日期、标题、摘要、来源

然后提交审核决定：

```bat
python -m app.election_candidates.review_candidate --candidate-id <候选ID> --decision <决定> --decision-file data\election_candidates\tainan_2026\review_templates\<候选ID>.json --reviewer <你的名字>
```

审核决定只追加、不可修改；候选状态随之更新（如 `review_approved` / `review_rejected`）。

## 4. 完成某一天之前的审核

当你把某段日期的候选全部处理完，执行：

```bat
python -m app.election_candidates.complete_review --through 2026-08-07 --reviewer <你的名字>
```

默认从“最近一个已完成审核的日期”的次日开始；第一次使用（尚无完成记录）时需加 `--from <起始日期>`。
程序会逐日检查：该日新闻是否已被候选管道处理完、该日候选是否全部有最终裁决。全部满足才写入
“该日已审核完成”，输出中的 `reviewed_through` 是连续已审核完成的日期边界（不等于最新新闻日期）。

## 5. 什么情况不能 complete

- 某天仍有 `pending` / `review_required` / `hold` / `needs_edit` 候选
- 某天的新闻还没有被候选管道扫描到
- 试图跳过中间未完成的日子

程序会拒绝并告诉你卡在哪一天。

## 6. 查看候选调度状态

```bat
scripts\status_candidate_monitor_task.ps1
```

会显示计划任务是否启用、上次运行时间与结果，以及最近日志。手动触发一次：

```bat
scripts\run_candidate_monitor_now.ps1
```
