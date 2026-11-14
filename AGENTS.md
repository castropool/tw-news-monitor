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

## 民调 / 地下赌盘 / KOL 信息源（2026-11-13 上线）

- 民调（`category: poll`）与地下赌盘（`category: betting`）是普通文章分类：
  `config/sources.yaml` 的 `poll_*` / `betting_*`（`type: google_news_rss`，
  `app/collectors/google_news.py`）→ 现有去重/入库/时效/重要度/摘要/Word/飞书链路。
- 相关性门禁与标注集中在 `app/special_topics/`（纯本地确定性规则，配置
  `config/special_topics.yaml`）：不相关条目入库但 `delivery_eligible=0`、
  `filter_reason=topic_relevance`；标注写 `news_topics`（topic=poll/betting，
  `metadata_json`）。Word/摘要渲染用同一纯函数重算标注行。poll/betting 永远
  留在自己的小节：不改路由进「九合一」或「国际媒体」（`is_dedicated_topic_article`）。
- KOL：`config/kol_social_sources.yaml`（20 人，`person_category=kol`）复用
  `app/social_monitor/`。`political_person.person_category`、
  `social_account.feed_url` 为追加列（行按位置映射，新列只能追加在末尾）。
  只有已验证账号可采集：`python -m app.social_monitor.cli verify` 在联网机器
  证明标识（YouTube 频道 feed / RSS feed 标题须匹配姓名），结果存
  `data/social_monitor/account_verifications.json` 并在同步注册表时覆盖。
  不要在配置里猜测账号标识或手工标 verified。
- 常规新闻任务通过 `app/social_monitor/news_run.py` 采集到期 KOL 账号
  （`config/social_monitor.yaml` 的 `news_run`），Word「KOL 社群动态」与飞书
  文本「KOL动态」只取近 24 小时、通过相关性筛选的帖子（`kol_delivery`）。
  所有社群步骤失败均不得阻断新闻交付。

## 台海安全试点基础与 Program 4（2026-11-14）

- 设计文档（需求与架构）在 claude.ai 文档中维护；本仓库按其 phase 0 / phase 1 实施。
- 正文：`app/article_text.py` + 表 `article_texts`（保留 730 天）；国际媒体 `access_level` 条目永不抓正文。
- 检索：`app/search.py`，FTS5 表 `article_search` 懒创建（不进 `Database.connect()`），汉字逐字成词 + 短语匹配，简体折为繁体。
- AI：所有模型调用只走 `DeepSeekClient.analyze`（预算检查 → 缓存 → 调用 → 台账 `data/ai_usage.db`）；新代码用
  `app/ai_client.make_client(task, "fast"|"strong")`，不要直接 new 客户端或绕过台账。
- 共军动态：`app/military/pla_activity.py`（表 `pla_activity_daily`，按报告文字解析，未提及记 None 而非 0）；
  `app/military/pla_baseline.py`（纯统计基线与异常）。
- 试点：`app/pilot/claims.py`（断言须附原文逐字引文，校验失败存为 invalid）、`app/pilot/weekly.py`
  （周报草稿、固定规则的假设指标、自动检查、分析员批准；只写本地文件，不发送）。
- 新增的常规任务钩子（`PLA_ACTIVITY_ENABLED`、`PILOT_EXTRACTION_ENABLED`）默认关闭，且失败不得阻断新闻交付。
- 测试：`tests/conftest.py` 把 AI 台账、周报目录指向临时目录，并关闭正文额外抓取；CI 见 `.github/workflows/tests.yml`。

## 选情研判功能已移除（2026-11-13）

- 台南与新北的选情研判（research-driven 生产路径，以及 claim-centric /
  two-stage / R2 等 legacy 路径）、分析报告生成、人工终审与交付已整体删除：
  `app/assessment/`、`app/election_report.py`、`app/election_quality_check.py`、
  `config/election_assessment*.yaml`、研判提示词/计划任务脚本/测试均不再存在。
- 不要重新引入研判命令、计划任务或占位模块（`assessment_trigger_queue` 表在旧库中
  保留但不再写入）。
- `data/election_assessment/`、`data/reports/` 中的历史报告为归档数据，保留不删。
- 通用工具已迁出：凭据扫描 `app/security_scan.py`。

## 选情正式事实库与候选发布已移除（2026-11-14）

- `app/election_context/`（正式入库、bootstrap、覆盖矩阵、态势快照、发布后下游流水线）
  与 `app/election_candidates/` 中全部发布链路已删除：`publication_*`、`review_and_publish`、
  `publish_reviewed_candidates`、`auto_publish_*`、`auto_review_orchestrator`、
  `recover/rollback_publication`、`formal_diff`、`input_inspector`/`inspect_inputs`；
  以及 `config/coverage_acceptance_rules.yaml`、自动发布计划任务脚本与相关测试。
- 候选管线现为**只监控、不发布**：`build_candidate_queue` 生成候选，人工用
  `review_candidate` 记录决定、`complete_review` 逐日标记审核完成（起点默认取候选库中
  最近完成日，不再读写 coverage 的 facts_cutoff）。
- 历史正式数据（`data/election_context*.db`、`data/election_seed/`、`data/research/`）保留不删；
  候选生成只读打开 `paths.formal_db` 做查重与数据保护哈希，绝不写入。候选库中旧的
  publication/downstream 表保留但不再创建或写入。不要重新引入发布或正式入库代码。

## 项目主文件夹（2026-08-11 整合后）

- 唯一主文件夹/生产目录：`D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple`
- 所有计划任务（Taiwan News Monitor、Tainan Election Candidate Monitor 及其新北对应任务）
  均指向本目录；旧事件管道任务 `Taiwan News Event Pipeline` 已停用（被候选管线取代）；
  `Tainan / New Taipei Election Fact Auto Publisher` 已随发布功能移除。
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
  `data/election_candidates/new_taipei_2026/`；只读查重用的历史正式库为
  `data/election_context.new_taipei.db`（与台南分开配置）。
- news.db / election_watch.db 共享；匹配走 `election_watch.yaml` 的 new_taipei 块
  （词表含侯友宜/李四川/苏巧慧等，随选情演进人工补充）。
- 命令（多数支持 `--config` 覆盖）：
  - 候选监控：`python -m app.election_candidates.build_candidate_queue --config config/election_candidate_pipeline.new_taipei.yaml --since-last-success`
  - 人工审核：`list_candidates/show_candidate/export_review_template/review_candidate/complete_review` 均加 `--config ...new_taipei.yaml`
- 计划任务（注册时加 `-Election new_taipei`，任务名带 New Taipei 前缀）：
  - `New Taipei Election Candidate Monitor`（每 30 分钟）
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
