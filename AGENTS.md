# AI Agent Guide

## Overview

`tw-news-monitor-simple` is a minimal Taiwan news monitoring tool.
Collects news from RSS feeds and HTML pages, deduplicates by URL, saves
to SQLite, and sends a categorized digest.

## Key Design Decisions

- Single `Article` dataclass with `slots=True`.
- Single `articles` table in SQLite with UNIQUE constraint on URL.
- URL normalization: lowercase scheme+host+path, remove fragment + trailing slash.
- Each collector has a self-contained `collect()` method returning up to 20 `Article` instances.
- Digest groups by category (politics, economy, international) and sorts by position, published_at (desc), source_name.
- Notifier factory: ConsoleNotifier by default, falls back from Feishu/Telegram gracefully.
- `--dry-run` uses a temporary SQLite DB that is deleted after the run.

## Architecture

```
main.py → load sources → for each source:
  collector.collect() → list[Article]
  db.article_exists(url) → dedup
  db.save_articles(new)
  build_digest(new) → notifier.send(text)
```

## Adding a Source

Add an entry to `config/sources.yaml` with `id`, `name`, `category`,
`collector` type, and `url`. If a new collector type is needed, add a
class in `app/collectors/` that extends `BaseCollector` and implements
`collect()`, then register it in the `COLLECTOR_MAP` in `main.py`.

## Collector Rules

1. Max 20 items per source.
2. Skip empty titles / URLs.
3. Normalize URL before dedup.
4. Do NOT fetch article body.
5. Do NOT filter by keywords inside collectors. Out-of-scope filtering
   (e.g. social trivia in economy feeds) is handled centrally by
   `app/content_filter.py` + `config/content_filter.yaml` before saving.
6. Do NOT classify as breaking news.
7. Single source failure must not stop others.
8. Set timeout per request.
9. Set reasonable User-Agent.
10. Log clear errors on failure.

## What Is NOT Implemented

- Event aggregation
- Title similarity detection
- News scoring / ranking
- Keyword filtering
- Page snapshots
- LLM classification
- Playwright browser automation
- Complex DB architecture (no events/snapshots/reports tables)
- Pydantic / SQLAlchemy / async frameworks / APScheduler

## What This Version Does NOT Have (Previous Project Issues)

The old `tw-news-monitor` had severe indentation and file-overwriting
problems. This project is rebuilt from scratch without copying any
Python code from the old project.

## 选情研判功能已移除（2026-11-13）

- 台南与新北的选情研判（research-driven 生产路径，以及 claim-centric /
  two-stage / R2 等 legacy 路径）、分析报告生成、人工终审与交付已整体删除：
  `app/assessment/`、`app/election_report.py`、`app/election_quality_check.py`、
  `config/election_assessment*.yaml`、研判提示词/计划任务脚本/测试均不再存在。
- 不要重新引入研判命令、计划任务或占位模块。候选事实发布后的下游流水线
  （`app/election_context/run_post_publication_pipeline.py`）只做 coverage
  重建/激活与 snapshot 刷新，不再创建研判触发器（`assessment_trigger_queue`
  表在旧库中保留但不再写入）。
- `data/election_assessment/`、`data/reports/` 中的历史报告为归档数据，保留不删。
- 通用工具已迁出：凭据扫描 `app/security_scan.py`；最新可用 coverage 版本选择
  `app.election_context.coverage_activation.select_coverage_version`。

## 项目主文件夹（2026-08-11 整合后）

- 唯一主文件夹/生产目录：`D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple`
- 所有计划任务（Taiwan News Monitor、Tainan Election Candidate Monitor、
  Tainan Election Fact Auto Publisher 及其新北对应任务）均指向本目录；旧事件管道任务
  `Taiwan News Event Pipeline` 已停用（被候选管线取代）。
- 历史副本（`D:\0801tw-news-monitor-simple`、`tw-news-monitor-simple-0809`、
  `-dev`、`-election-dev`、`.BACKUP`、`D:\TW News-Monitor`、`D:\news monitor`）
  已停止同步，仅供归档参考，不要再作为改动目标。
- 后续所有代码与配置改动只在本主文件夹进行；`data/news.db` 与
  `data/election_watch.db` 是实时运行数据（会随计划任务增长），
  相关测试保护的是完整性而非字节哈希。

## 新北选情候选事实管线（2026-09-03 上线，与台南并行同构）

- 第二选举：`new_taipei_mayoral_2026`（`TW-2026-NTP-MAYOR`，2026 新北市長）。
  通过独立 config 激活，台南默认行为零改动（向后兼容）。
- 关键文件：`config/election_candidate_pipeline.new_taipei.yaml`（候选管线）、
  `data/election_seed/new_taipei_2026/`（种子+coverage，含初始
  `fact_coverage_20260903_v1`）、`data/election_candidates/new_taipei_2026/`。
- **独立正式库**：`data/election_context.new_taipei.db`（不可与台南共享
  `election_context.db`——候选发布管线 commit 会整体替换 formal DB，共享会被覆盖）。
- news.db / election_watch.db 共享；匹配走 `election_watch.yaml` 的 new_taipei 块
  （词表含侯友宜/李四川/苏巧慧等，随选情演进人工补充）。
- 命令（多数支持 `--config` 覆盖）：
  - 候选监控：`python -m app.election_candidates.build_candidate_queue --config config/election_candidate_pipeline.new_taipei.yaml --since-last-success`
  - 自动审核编排：`python -m app.election_candidates.auto_review_orchestrator --config config/election_candidate_pipeline.new_taipei.yaml`
  - 人工审核：`list_candidates/show_candidate/export_review_template/review_and_publish/complete_review` 均加 `--config ...new_taipei.yaml`
- 计划任务（注册时加 `-Election new_taipei`，任务名带 New Taipei 前缀）：
  - `New Taipei Election Candidate Monitor`（每 30 分钟）
  - `New Taipei Election Fact Auto Publisher`（每 30 分钟错峰）
- 多选举注意：候选事件 ID 前缀随 `election.candidate_id_prefix` 派生
  （cand_tnn→evt_tnn_，cand_ntp→evt_ntp_）；`other_race_markers` 按主场反转
  （新北主场时台南是"他县"）；`match_reader.city_values` 决定分类器城市。

## 2026 九合一选举专题（2026-09-04 上线，Word 简报专栏）

- 每日新闻自动识别"2026 九合一选举"内容，Word 简报生成"九合一选举"一级
  栏目（官方信源之后、新闻媒体之前；空则隐藏、编号动态顺延）；官方稿与
  国际媒体稿不进九合一栏；九合一稿不再出现在普通政治新闻栏；重大九合一
  稿允许同时【重大】进重点提示与九合一栏（现有 importance 驱动，不改阈值）。
- 核心引擎：`app/election2026/`（纯本地确定性规则，无 LLM/网络依赖）：
  `classifier.py`（评分≥60 判正；40-59 疑似区保守子规则；负向词一票否决；
  政务抑制词拦"市长视察/施政满意度"类误判）、`region_resolver.py`
  （22 县市别名归一、重叠裁决防"竹北→北市"误吞、≥3 县市并列归全局动向）、
  `entity_loader.py`（候选人实体仅辅助证据）、`event_classifier.py`
  （12 类事件类型）、`hanzi_utils.py`（opencc 生成的繁简单字映射，台式
  校正：台/栗/杰等保持原字）、`config.py`（fail-closed：配置缺失→功能禁用）。
- 配置：`config/election_2026.yaml`（强/辅助/负向/政务抑制词、全国场景、
  event_type 映射、22 县市 display_order/merge_groups/aliases）与
  `config/election_2026_entities.yaml`（候选人实体库，随选情人工扩展）。
  词表繁体为主；简体标题经 hanzi_utils 归一后匹配。
- 数据持久化：news.db 新增 `news_topics` 表（url UNIQUE，幂等 INSERT OR
  REPLACE），`connect()` 自动建表（CREATE IF NOT EXISTS，兼容旧库可重复
  执行）；Word 渲染以内存分类为准（main 算好传入），`--export-word` 等
  历史稿场景由 word_digest 内同一纯函数兜底重算，结果一致。
- Word 结构：`app/word_digest.py::build_word_digest` 新增可选参数
  `election_config/election_entities/election_annotations`（默认 None =
  旧行为零改动）；九合一栏二级分组动态编号（一）…（十）→超过十回退
  （11）（12）…；县市排序按 `regions.display_order`，新竹縣+新竹市
  合并展示"新竹縣市"（底层仍分别存标准名）。
- 日志：`[election] matched=true score=… scope=… region=… type=…` /
  `[election] review score=…` / `[election] rejected reason=…` 单行审计。
- 测试：`tests/test_election2026_{classifier,region,word,db,config}.py`
  共 159 项（正≥30/负≥26/边界≥16/全局/多县市/Word 10 case/DB 兼容）。
  已知边界：现任市长家庭/施政争议、地方议题选举化、跨县市主地区判断、
  长摘要尾部选举词误伤——靠实体库与词表人工维护持续校正。
