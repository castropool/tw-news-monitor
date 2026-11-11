# tw-news-monitor-simple — 台湾新闻监测 + 选情事实底表系统

本项目是一个运行在 Windows 上的台湾政治新闻监测与「选情辅助研判」一体化系统，由两大子系统构成：

1. **新闻采集与推送子系统**：定时采集台湾政治/财经/国际新闻，去重入库，生成摘要与 Word 简报，推送至控制台 / 飞书 / Telegram。
2. **选情事实底表子系统**（`election_context`）：面向「2026 年台南市长选举」的可验证事实库——从研究资料包经人工裁决 → 转换预览 → 发布门禁 → 正式入库 → 覆盖矩阵 → 态势快照 → 研判报告，全链路留痕、可审计、可回滚。

当前正式数据基线（2026-08-01）：

| 指标 | 数量 |
|---|---|
| elections | 1 |
| actors | 6 |
| events（正式事件） | 41 |
| sources（正式来源） | 112 |
| event_source_links | 101 |
| event_fts（全文检索） | 41 |
| polls（正式民调） | 15 |
| poll_questions / poll_results | 39 / 116 |
| snapshots（态势快照） | 4（1 active：`tn_state_20260801_v1`） |

---

## 一、快速开始

```bash
pip install -r requirements.txt

# 首次初始化采集（只采集入库，不推送）
python -m app.main --bootstrap

# 常规运行（采集 + 去重 + 推送 + Word 简报）
python -m app.main

# 只读演练（不写库、不推送）
python -m app.main --dry-run

# 查看新闻库统计
python -m app.main --db-stats

# 运行全部测试
python -m pytest -q
```

## 二、新闻采集子系统

### 数据流

```
config/sources.yaml
   └─> main.py 载入并校验配置（失败即退出）
   └─> COLLECTOR_MAP 选择采集器（RSS / HTML 列表页 / 总统府 JSON）
   └─> 逐来源 collect() → list[Article]（单来源失败不影响其他）
   └─> URL 归一化去重 → 文章身份(identity)去重（UDN 别名等）
   └─> 与历史库比对（URL + identity 双层）→ 新增入库
   └─> 内容过滤（config/content_filter.yaml：经济栏目里的彩票/美食/营销等社会琐事）
   └─> 时效分级（fresh / catch-up / stale / unknown / future）
   └─> 重要度分级（config/importance_rules.yaml）
   └─> 文章梗概（RSS 导语直取；缺失时抓正文 + LLM 按五要素生成，SUMMARIZER_MODE 可配；LLM 默认经本地 Command Code 网关调用 deepseek flash，见 `app/cmd_gateway.py`）
   └─> 文本摘要 + 统计 → notifier 推送
   └─> Word 简报（python-docx）→ 飞书文档发送
```

### 来源与采集器

来源定义在 `config/sources.yaml`，主要采集器类型如下（`app/collectors/`）：

| 类型 | 采集器 | 说明 |
|---|---|---|
| `rss` | RSSCollector | 通用 RSS/Atom（含 Newtalk、Storm 等） |
| `udn` | UDNCollector | 联合新闻网 HTML 列表页 |
| `ebc` | EBCCollector | 东森新闻 HTML 列表页 |
| `cna_list_html` | CNAHtmlCollector | 中央社列表页 |
| `zaobao` | ZaobaoCollector | 联合早报（官方 Google News Sitemap，含发布时间） |
| `ltn_rss` | LtnRSSCollector | 自由时报 RSS |
| `president_json` | PresidentCollector | 总统府新闻稿 API/JSON |
| `reuters` | ReutersCollector | Reuters 官方新闻 sitemap，仅元数据 |
| `ft_alphaville` | FTAlphavilleCollector | FT Alphaville 官方 RSS，仅 feed 摘要 |
| `wsj_rss` | WSJRSSCollector | WSJ 官方冻结 RSS，Phase I 默认停用 |

采集器规则：单来源上限 20 条、空标题/空 URL 跳过、统一设置超时与 User-Agent、不抓取正文、单来源故障不中断整体。
采集器本身不做关键词过滤；关注范围过滤由 `config/content_filter.yaml` 统一执行。生产默认 `exclude_from_delivery`：命中经济栏目里的彩票开奖、美食消费、营销活动、职场生活等社会琐事的文章仍保留在历史库，但不进入摘要、Word 或通知；特殊环境可切换为 `drop_before_save`。

### 国际媒体免费监测层 Phase I

- Reuters 与 FT Alphaville collector 已接入现有 Article、SQLite、freshness、importance、Word 与通知链；生产 `config/sources.yaml` 默认保持关闭，由人工决定启用。
- WSJ RSS 已冻结且条目为 PAID，Bloomberg robots 限制 Python/feed 聚合器；两者 Phase I 仅保留本地 NewsletterAdapter，不抓订阅正文、不接邮箱 OAuth。
- 国际稿全部可按 URL 入库，但只有新鲜且与台湾/两岸/涉台国际政策相关的文章进入简报。跨媒体近似事件只在显示层合并，数据库不删除各媒体 URL。
- `metadata_only`、`newsletter` 以及其他显式国际 access level 不进入正文或 meta-description 网络抓取。
- 独立真实验收见 `validation/international_phase1/`，不会使用生产数据库、Scheduler 或飞书。

### 去重机制（`article_identity.py`）

- **可点击 URL 清理**：scheme+host 小写、保留 path 大小写，去 tracking 参数、fragment 与尾部斜杠；身份键单独负责查重规范化。
- **身份键（identity key）**：独立提取来源、日期与标题指纹，用于捕获同一文章的别名 URL（如 UDN 重复路径），不会改写用户看到的链接。
- 三轮去重：本轮内 URL 去重 → 本轮内 identity 去重 → 与历史库 URL+identity 比对。

### 时效与重要度

- `freshness.py`：`app/main.py` 中固定 90 分钟时效窗口内的为 fresh；`NEWS_CATCHUP_ENABLED=true` 时可对窗口外的新文章做补发（`NEWS_CATCHUP_MAX_MINUTES`，默认 720，且必须大于 90）。
- `importance.py`：按 `config/importance_rules.yaml` v2 双轨道规则（选情轨道 / 政经安全轨道）打分，产出 critical / important / normal 分级；每次推送 critical+important 合计不超过 5 条（有选情候选时选情轨道至少保留 1 条），飞书高亮卡片（highlight card）可选发送。
- `source_registry.py`：官方来源识别（行政院、总统府等），Word 简报中单独分区。

### 通知渠道（`notifier.py` / `feishu.py`）

`NOTIFIER=console|feishu|telegram`（`.env` 配置），缺配置时优雅回退控制台。飞书走 App 机器人（发送文本、Word 文档、高亮卡片）；`DISABLE_FEISHU_SEND` 可关停发送。
重点新闻高亮卡片由 `main.py` 直接调用飞书 App 机器人发送，不依赖 `NOTIFIER` 类型——即使 `NOTIFIER=console`，只要配置了 `FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID`，卡片仍会送达群聊。

### 常用 CLI

| 命令 | 功能 |
|---|---|
| `python -m app.main --bootstrap` | 初始化采集，不推送 |
| `python -m app.main --export-word [N]` | 导出最近 N 条（默认 30）为 Word |
| `python -m app.main --backfill-run <YYYYMMDD_HHMM> [--send]` | 对历史批次补发（幂等标记在 `data/backfill_markers/`） |
| `python -m app.main --diagnose-collection / --diagnose-source <id> / --diagnose-file <json>` | 采集诊断（只读，输出到 `data/diagnostics/`） |
| `python -m app.main --test-notify / --test-feishu-app / --test-feishu-file / --list-feishu-chats` | 通知渠道自检 |

### Windows 定时任务

`install_task.bat` 安装计划任务「Taiwan News Monitor」，每 30 分钟运行 `run_monitor.bat`（单实例锁保护，`data/monitor.lock`），日志滚动写入 `data/monitor.log`。

---

## 三、选情事实底表子系统（`app/election_context/`）

这是面向「2026 台南市长选举」的核心工程，遵循 **种子(seed) → 数据库(DB) 双写一致、全流程门禁、幂等可回滚** 的原则。

### 核心概念

- **正式种子（formal seed）**：`data/election_seed/tainan_2026/` 下的权威文本文件，是事实的唯一来源（source of truth）：
  - `election.json`（选举定义）、`actors.yaml`（人物）、`events.jsonl`（41 条正式事件）、`sources.jsonl`（112 条正式来源）、`polls.jsonl`（15 条正式民调）、`poll_source_links.jsonl`、`taxonomy.yaml`（事件类型定义）、`initial_snapshot.json` + `snapshot_history.jsonl`（快照）。
- **生产数据库**：`data/election_context.db`，由种子经 bootstrap 生成，必须与种子完全等价（有等价性校验）。
- **事件（event）**：事实最小单元，含 `event_id`、`occurred_at`、`event_type`、`title`、`analysis_json`（含 scope / verified_facts / limitations / subevents / mentions 等）、`sources[]`（内嵌来源）。
- **证据台账（evidence ledger）**：覆盖矩阵 v4 中 88 条唯一 `evidence_ref`，每条绑定事件、来源、主题、限制与修订版本（`rt04_imported` / `rt04_enriched`）。

### 模块职责（`app/election_context/`）

| 模块 | 职责 |
|---|---|
| `models.py` | Election / Actor / Source / ElectionEvent / ElectionStateSnapshot 数据模型 |
| `repository.py` | SQLite 仓储层：建表、保存、查询、FTS 全文检索与相关度排序 |
| `bootstrap.py` | 从种子构建/重置生产 DB，幂等（重复运行结果一致） |
| `importer.py` | events.jsonl / sources.jsonl 导入 |
| `retriever.py` | `build_election_context()`：检索 + 组装选情上下文 |
| `state_builder.py` | `build_snapshot()`：生成态势快照 |
| `audit.py` | 数据库完整性审计（计数、孤儿、快照约束等） |
| `poll_validator.py` | 民调记录/集合校验、可比性分组（cross-tracker 比对前提） |
| `validate_*.py` | 各阶段门禁验证器（见下） |

### 验证器（门禁）

| 验证器 | 门禁内容 |
|---|---|
| `validate_poll_import.py` / `validate_poll_release.py` | 民调导入与发布校验 |
| `validate_event_release.py` | 事件发布：正式数据不变、限制披露、唯一性 |
| `validate_search_release.py` | 检索案例 + 黄金检索集通过 |
| `validate_seed_db_equivalence.py` | 种子 ↔ 生产 DB 等价性 |
| `validate_snapshot_release.py` / `validate_snapshot_v2_preview.py` | 快照发布：只引用正式证据、唯一 active、supersede 正确、幂等 |
| `validate_fact_coverage.py` | 覆盖矩阵：preflight / ledger / 时间/主题矩阵 / 快照缺口对账 / backlog |

### 研究 → 入库流水线（RT 流程）

每条研究任务（RT02 陈亭妃整合、RT03 谢龙介组织、RT04 蓝白合作…）按固定管线推进，每步产出可审计报告：

```
data/research/tainan_2026/<rtXX>_research_pack_*.jsonl/.md   # 研究资料包（含人工裁决标记）
   ↓ 转换预览
event_preview_rtXX_*/  → conversion_report / event_similarity_report /
                         event_type_mapping_report / human_adjudication_report /
                         negative_findings.jsonl / final_release_candidate/
   ↓ 发布门禁
rtXX_release_gate.json / rtXX_source_import_plan.json（新来源数、复用数）
   ↓ 正式入库（双写种子 + DB）
event_import_rtXX_*/ → import_preflight / import_validation / post_release_reconciliation /
                       seed_db_equivalence / bootstrap_idempotency / second_import_idempotency /
                       search_results / golden_search_results / import_audit
   ↓ 覆盖更新
fact_coverage_*/ v1 → v2 → v3 → v4（ledger / 时间矩阵 / 主题矩阵 / gap对账 / backlog / closure_record / blocker_triage）
   ↓ 快照
snapshot_release_candidate_*.json → snapshot_validation → 原子发布（supersede 旧 active）
   ↓ 研判报告
data/reports/tainan_2026/tainan_election_assessment_*.md + _evidence.json
```

关键约束（历史教训固化为门禁）：

- **种子必须与 DB 等价**（重建验证）；导入前必须备份到 `data/backup_rtXX_pre_import/`。
- **enrichment（子事件/新增内容）必须写进种子**，否则重建丢失（RT02/RT03 教训）。
- **`analysis_json` 必须以解析后的 dict 写入种子**，否则 scope 等顶层键丢失（RT04 教训）。
- 发布前核验：目标事件哈希不变、非目标事件不动、hold / negative finding 不得作为正式证据。
- 快照发布：先候选 → 验证 → 原子发布（唯一 active、旧快照 supersede、二次发布幂等）。

### 覆盖矩阵版本（`data/election_seed/tainan_2026/fact_coverage_*/`）

| 版本 | 说明 |
|---|---|
| v1（20260727） | 基线：RT01 民调缺口负发现 |
| v2（20260727） | RT02 陈亭妃整合（closure_record） |
| v3（20260727） | RT03 谢龙介组织（closure_record） |
| **v4（20260801）** | **RT04 蓝白合作结项 + 快照阻断分级 + 首个生产快照** |

每个版本包含：`coverage_preflight.json`、`coverage_evidence_ledger.jsonl`（88 条）、`time_coverage_matrix.{json,csv}`、`theme_coverage_matrix.{json,csv}`、`snapshot_gap_reconciliation.json`、`research_priority_backlog.json`、`coverage_validation.json`；RT 结项版本另含 `rtXX_closure_record.json`、`snapshot_blocker_triage.json` 等。**旧版本禁止修改**（构建前有哈希快照校验）。

### 态势快照（snapshot）

- 正式快照：`initial_snapshot.json`（当前 active）+ `snapshot_history.jsonl`（历史，superseded）。
- 当前 active：`tn_state_20260801_v1`（事实截止 2026-07-27，民调截止 2026-03-12）。
- 快照内容：总体格局、候选人判断（陈亭妃整合 / 谢龙介组织 / 蓝白合作）、民调判断（含空窗披露）、已知限制、核心议题、关键风险、里程碑事件。
- 门禁：只引用正式事件/民调、active P0=0、无 hard blocker、民调空窗与治理缺口必须披露、pytest 全过。

---

## 四、选情研判子系统（实时扫描 + 报告）

- `election_watch.py`：扫描 `news.db` 新文章，`ElectionClassifier`（`config/election_watch.yaml` 关键词规则）识别涉台南选举文章，打分 relevance，写入 `election_fact_store`（事实库）。
- `election_classifier.py`：城市/人物/政党/议题匹配 + 选举上下文判定。
- `election_fact_store.py`：扫描状态与匹配事实存储（增量扫描、幂等）。
- `deepseek_analysis.py`：可选调用 DeepSeek API 做深度分析（`DEEPSEEK_API_KEY`）。
- `election_report.py`：汇总事实库 + 人工补充事实（`config/election_manual_facts.json`）+ 风格配置（`config/election_analysis_style.yaml`），经 `election_quality_check.py` 质检后生成报告。
- `election_event_merge.py`：将同事件的多篇文章聚合为事件。

---

## 五、项目结构

```
tw-news-monitor-simple/
├── app/
│   ├── main.py                  # 新闻采集 CLI 入口
│   ├── collectors/              # RSS / UDN / EBC / CNA / LTN / 总统府
│   ├── database.py models.py    # 新闻库（articles 表）
│   ├── digest.py notifier.py    # 摘要与推送（console/feishu/telegram）
│   ├── feishu.py                # 飞书 App 机器人（文本/文档/卡片）
│   ├── word_digest.py           # Word 简报生成
│   ├── freshness.py importance.py article_identity.py source_registry.py time_utils.py lock.py
│   ├── summarizer.py            # 文章梗概（RSS 导语 + DeepSeek 批量 + meta 兜底）
│   ├── diagnose.py              # 采集诊断
│   ├── election_classifier.py election_watch.py election_fact_store.py election_report.py
│   ├── election_quality_check.py election_event_merge.py election_utils.py deepseek_analysis.py
│   └── election_context/        # 选情事实底表子系统
│       ├── models.py repository.py bootstrap.py importer.py retriever.py
│       ├── state_builder.py audit.py poll_validator.py
│       └── validate_*.py        # 各阶段门禁
├── config/                      # sources.yaml / election_watch.yaml / importance_rules.yaml /
│                                # election_analysis_style.yaml / election_manual_facts.json
├── data/
│   ├── news.db                  # 新闻库（勿删！去重状态在此）
│   ├── election_context.db      # 选情事实底表生产库
│   ├── election_seed/tainan_2026/   # ★ 正式种子（事实唯一来源）
│   │   ├── events.jsonl sources.jsonl polls.jsonl election.json actors.yaml taxonomy.yaml
│   │   ├── initial_snapshot.json snapshot_history.jsonl
│   │   ├── event_preview_rtXX_*/     # 转换预览 + 人工裁决 + release candidate
│   │   ├── event_import_rtXX_*/      # 正式入库报告（验证/对账/幂等/审计）
│   │   └── fact_coverage_*/          # 覆盖矩阵 v1-v4
│   ├── research/tainan_2026/         # 研究资料包（人工裁决输入）
│   ├── reports/tainan_2026/          # 选情研判报告 + 证据映射
│   ├── backup_rtXX_pre_import/       # 导入前备份
│   └── diagnostics/                  # 采集诊断输出
├── prompts/                     # DeepSeek 分析提示词模板
├── tests/                       # 629+ 个测试（含 election_context 门禁测试）
├── *.bat / *.ps1                # 计划任务安装/状态/卸载
├── requirements.txt
└── README.md
```

---

## 六、配置

复制 `.env.example` 为 `.env`：

| 变量 | 用途 |
|---|---|
| `NOTIFIER` | `console` / `feishu` / `telegram` |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` | 飞书 App 机器人 |
| `FEISHU_WEBHOOK_URL` | 飞书 Webhook（旧渠道） |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram |
| `DEEPSEEK_API_KEY` | 选举评估等深度分析 API（摘要链路不依赖它） |
| `SUMMARIZER_MODE` / `SUMMARIZER_MAX_LENGTH` / `SUMMARIZER_BATCH_SIZE` | 文章梗概：模式（rss/llm/meta/hybrid/none）、长度上限、LLM 批量大小 |
| `SUMMARIZER_CONTENT_CHARS` / `SUMMARIZER_RETRY_HOURS` | 正文截取字符数（默认800）、抓取失败重试间隔（小时） |
| `SUMMARIZER_BASE_URL` / `SUMMARIZER_MODEL` | 摘要 LLM 端点（默认本地 Command Code 网关 `http://127.0.0.1:8765/v1` + `deepseek/deepseek-v4-flash`；未设置时回退 `DEEPSEEK_*`） |
| `NEWS_DB_PATH` / `SOURCES_CONFIG_PATH` / `DATABASE_PATH` | 路径覆盖 |
| `NEWS_CATCHUP_ENABLED` / `NEWS_CATCHUP_MAX_MINUTES` | 补发开关与窗口 |
| `DISABLE_FEISHU_SEND` | 停用飞书发送 |

## 七、测试

```bash
python -m pytest -q      # 2564 passed, 4 skipped（2026-08-14）
```

测试覆盖：采集器解析（含国际媒体真实结构离线 fixture）、去重/身份键、时效/重要度、国际相关性与跨媒体显示去重、Word 生成、通知渠道、配置校验、民调校验，以及 election_context 全部门禁（RT02/RT03/RT04 转换预览、发布门禁、来源校正、结项快照、种子↔DB 等价、哈希不变）。

## 八、维护注意事项

1. **不要删除 `data/news.db`**：包含文章历史与去重状态。
2. **不要直接改正式种子**：任何事件/来源/民调变更必须走「备份 → 转换 → 门禁 → 双写 → 对账 → 测试」流程。
3. **不要修改既有覆盖矩阵版本**（v1-v4 是历史快照）。
4. **导入前必须备份**：`backup_rtXX_pre_import/` 保留每个任务的前置状态，用于回滚。
5. **快照发布遵循候选→验证→原子发布**：连续发布不得破坏唯一 active 约束。

## License

MIT
