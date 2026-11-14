# tw-news-monitor-simple — 台湾新闻监测 + 选情候选事实监控

本项目是一个运行在 Windows 上的台湾政治新闻监测与选情候选事实监控系统，由两大子系统构成：

1. **新闻采集与推送子系统**：定时采集台湾政治/财经/国际新闻、民调、地下赌盘与 KOL 社群动态，去重入库，生成摘要与 Word 简报，推送至控制台 / 飞书 / Telegram。
2. **选情候选事实监控**（`app/election_candidates/`）：从新闻中自动生成 2026 台南 / 新北市长选举的候选事实，供人工审核与逐日完成审核记录（只监控、不发布）。

> 已移除的功能：选情研判（分析报告生成、终审与交付），以及选情正式事实库（`app/election_context/`：
> 正式入库、覆盖矩阵、态势快照、候选事实发布 / 自动发布）。`data/` 下的历史报告、正式事实库
> `election_context.db` 与种子均作为历史数据保留；候选生成时只读引用它们做查重，不再写入。

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
| `google_news_rss` | GoogleNewsRSSCollector | Google 新闻 RSS 检索（民调 / 地下赌盘专题），发布媒体作 `source_name` |

采集器规则：单来源上限 20 条、空标题/空 URL 跳过、统一设置超时与 User-Agent、不抓取正文、单来源故障不中断整体。
采集器本身不做关键词过滤；关注范围过滤由 `config/content_filter.yaml` 统一执行。生产默认 `exclude_from_delivery`：命中经济栏目里的彩票开奖、美食消费、营销活动、职场生活等社会琐事的文章仍保留在历史库，但不进入摘要、Word 或通知；特殊环境可切换为 `drop_before_save`。

### 国际媒体免费监测层 Phase I

- Reuters 与 FT Alphaville collector 已接入现有 Article、SQLite、freshness、importance、Word 与通知链；生产 `config/sources.yaml` 默认保持关闭，由人工决定启用。
- WSJ RSS 已冻结且条目为 PAID，Bloomberg robots 限制 Python/feed 聚合器；两者 Phase I 仅保留本地 NewsletterAdapter，不抓订阅正文、不接邮箱 OAuth。
- 国际稿全部可按 URL 入库，但只有新鲜且与台湾/两岸/涉台国际政策相关的文章进入简报。跨媒体近似事件只在显示层合并，数据库不删除各媒体 URL。
- `metadata_only`、`newsletter` 以及其他显式国际 access level 不进入正文或 meta-description 网络抓取。
- 独立真实验收见 `validation/international_phase1/`，不会使用生产数据库、Scheduler 或飞书。

### 民调与地下赌盘专题（`category: poll` / `betting`）

- 来源：`config/sources.yaml` 中 `poll_*`（台湾民意基金会、美丽岛电子报国政民调、TVBS、台湾指标、远见、联合报、选举民调、最新民调）与 `betting_*`（地下赌盘、选举赌盘、签赌查缉、博弈网站查缉）共 12 个 Google 新闻 zh-TW 检索 feed（近 7 天）。走与其他新闻相同的采集、URL/identity 去重、入库、时效、重要度与交付链路。
- 相关性门禁（`app/special_topics/` + `config/special_topics.yaml`）：检索 feed 难免混入无关结果，统一在去重后判定；不相关条目照常入库但 `delivery_eligible=0`、`filter_reason=topic_relevance`，不进入摘要/Word/飞书。采集器本身不做关键词过滤。
- 结构化标注写入 `news_topics`（`topic=poll` / `betting`，`metadata_json`）：民调为发布机构、关键百分比、样本数、抽样误差、调查期间；赌盘为类型（选举赌盘/运动签赌/线上博弈/查缉执法/洗钱金流）与是否涉及选举。只复述原文，不推断。
- 展示：飞书文本摘要与 Word 各有「民调」「地下赌盘」小节，每条附一行标注（如 `民调机构：台灣民意基金會｜关键数据：賴清德聲望42.3%…`）。这两类始终留在自己的小节，不会被改路由到「九合一」或「国际媒体」。

### KOL 社群动态（`app/social_monitor/`）

- 名单：`config/kol_social_sources.yaml` 收录 20 位台湾本地/涉台 KOL（时事解说、两岸与国际评论、各光谱政治评论员），与 `config/political_social_sources.yaml` 共用社群监测架构（账号、帖子去重与修订、跨平台合并、原始快照），以 `person_category=kol` 区分。
- **账号须先验证才会采集**（沿用政治人物社群的安全规则）。名单中的账号标识是候选值，需在联网的部署机执行：

  ```
  python -m app.social_monitor.cli verify --account-id kol_mindi_youtube --url https://www.youtube.com/@<handle>
  python -m app.social_monitor.cli verify --category kol        # 批量验证所有尚未验证的账号（缺候选标识的只报告原因，不写入）
  python -m app.social_monitor.cli list --category kol          # 查看验证状态
  ```

  YouTube：解析频道 ID → 读取公开频道 feed → 标题须与名单姓名/别名匹配；有 `feed_url` 的账号（Podcast、Blog/Substack，或运营方自建 RSS 桥接的 Facebook/Threads/X）：feed 标题或作者须匹配。结果写入 `data/social_monitor/account_verifications.json`，不匹配一律不记为已验证。Facebook/Threads/X 官方适配器未实现，没有 `feed_url` 时无法采集。
- 采集：常规新闻任务（`python -m app.main`）每轮按 `config/social_monitor.yaml` 的 `news_run` 采集到期的已验证 KOL 账号（分级间隔，单轮上限 30 个），失败不影响新闻交付；也可手动 `python -m app.social_monitor.cli collect --category kol`。
- 展示：Word 新增「KOL 社群动态」小节（与「政治人物社群动态」分开），飞书文本摘要追加「KOL动态」小节；只取最近 24 小时、通过相关性筛选的帖子（`kol_delivery`：窗口、Word 上限 30、飞书上限 10）。

### 去重机制（`article_identity.py`）

- **可点击 URL 清理**：scheme+host 小写、保留 path 大小写，去 tracking 参数、fragment 与尾部斜杠；身份键单独负责查重规范化。
- **身份键（identity key）**：独立提取来源、日期与标题指纹，用于捕获同一文章的别名 URL（如 UDN 重复路径），不会改写用户看到的链接。
- 三轮去重：本轮内 URL 去重 → 本轮内 identity 去重 → 与历史库 URL+identity 比对。

### 正文存储（`article_text.py`，表 `article_texts`）

- 摘要器 hybrid 模式已下载的文章页，其正文抽取结果（trafilatura，不含 meta 描述）同时存入 `article_texts`，不增加任何请求。
- 带 `access_level` 的国际媒体条目永不抓取正文（与摘要器相同的边界）。
- 保留约 24 个月（`ARTICLE_TEXT_RETENTION_DAYS`，默认 730），过期只删正文，文章行保留；每次运行自动清理。
- 试点范围内的分类（`ARTICLE_TEXT_INSCOPE_CATEGORIES`，默认 `military`）每次运行额外抓取最多 `ARTICLE_TEXT_INSCOPE_FETCH_MAX`（默认 20）篇尚无正文的新文章。
- 其他分类由 `ARTICLE_TEXT_FETCH_MAX`（默认 0 = 不额外请求）控制。

### 全文检索（`app/search.py`）

- SQLite FTS5 索引标题、摘要与已存正文；每个汉字单独成词，查询词按相邻字短语匹配（`國防部` 不会匹配分散的三个字）。
- 简体输入统一折为繁体，`国防部` 与 `國防部` 结果相同；多个词以空格分隔，须全部命中；标题命中排在前面。
- 索引表懒创建（不在 `Database.connect()` 中），无 FTS5 的环境只会让检索不可用，不影响采集；每次检索前增量更新（首次在大库上需数十秒）。

```
python -m app.search 國防部 共機 --since 2026-11-01 --limit 20
python -m app.search 民調 --category poll
python -m app.search --rebuild
```

### AI 调用记账与月度上限（`app/ai_usage.py`）

- 所有模型调用都经 `DeepSeekClient.analyze`：发送前检查本月预估花费，发送后写入台账 `data/ai_usage.db`（任务、端点、模型、token、预估费用、耗时、错误）。
- 预估偏高以保证不超支：按峰时价计价；未知模型按最贵已知价；网关不回报 token（本地 Command Code 网关恒为 0）时按文字长度估算（每个汉字 1 token，其余每 3 字符 1 token）。失败调用只计端点回报的 token。
- 本月（UTC 自然月）预估花费达到 `AI_MONTHLY_BUDGET_USD`（默认 15 美元）后拒绝调用，摘要按现有降级路径回退（导语/meta），新闻照常交付。台账读写失败不阻断调用。
- 查看：`python -m app.ai_usage`（本月）或 `python -m app.ai_usage --month 2026-10`。
- 相同请求（模型+提示词完全一致）30 天内直接复用已存答案，记为 `cache_hit`、费用 0（`AI_CACHE_ENABLED=0` 关闭，`AI_CACHE_DAYS` 调整）。
- 两档模型（`app/ai_client.py`）：`fast` 用于摘要/翻译/抽取（`AI_MODEL_FAST`，默认沿用 `SUMMARIZER_MODEL`），`strong` 用于周度研判草稿（`AI_MODEL_STRONG`，默认 `deepseek/deepseek-v4-pro`），走同一网关。

### 国防部共军动态日报（`app/military/pla_activity.py`，表 `pla_activity_daily`）

- 解析国防部每日「中共解放軍臺海周邊海、空域動態」：共机架次、逾越中线/进入空域架次、共舰、公务船、空飘气球，每个报告日一行（06:00–06:00 窗口的结束日）；国防部改稿时更新。
- 解析基于报告文字而非网页样式；报告未提及的数字记为空（未知），只有写明「未偵獲」才记 0。
- **尚未对线上网页验证**（开发环境无法访问 mnd.gov.tw），默认关闭。在能访问的机器上先检查：

```
python -m app.military.pla_activity --check
```

  结果正确后回填历史（约每秒一个请求，两年约 15 分钟），并列出缺报日期：

```
python -m app.military.pla_activity --backfill-days 730
```

  然后在 `.env` 设 `PLA_ACTIVITY_ENABLED=true`，常规任务每天最多抓取到当日报告为止；失败不影响新闻交付。查看已存数据：`python -m app.military.pla_activity --show 14`。

### 共军动态基线与异常（`app/military/pla_baseline.py`）

- 纯统计、无模型调用：每个指标（共机、逾越中线、共舰、公务船、气球）与前 90 天比较，给出 z 分数；z≥2 且不低于 P90 记「异常偏高」，z≤-1.5 且不高于 P10 记「异常偏低」；前 90 天有数据少于 30 天时不出旗标（基线不足）。
- 周视图：本周合计/日均/单日最高、本周对基线倍数、高于 P90 天数、90 日最小二乘趋势，以及缺报日期。`python -m app.military.pla_baseline --week-ending 2026-11-14`。

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

## 三、选情候选事实监控（`app/election_candidates/`）

- 生成：`python -m app.election_candidates.build_candidate_queue --since-last-success`（计划任务「Tainan / New Taipei Election Candidate Monitor」每 30 分钟运行；新北加 `--config config/election_candidate_pipeline.new_taipei.yaml`）。读取 `news.db` 与 `election_watch.db`，聚类、断言分类、来源解析、评分与路由后写入候选库 `data/election_candidates/<选举>/candidate_fact_pipeline.db`。
- 查重：只读打开历史正式事实库（`paths.formal_db`）比对疑似重复；从不写入正式库或种子（`guardrails`）。
- 审核：`list_candidates` / `show_candidate` / `export_review_template` / `review_candidate`（追加式审核记录）/ `complete_review`（逐日连续标记“已审核完成”）。操作步骤见 `docs/FACT_MAINTENANCE_OPERATOR_GUIDE.md`。

---

## 四、选情新闻扫描（实时扫描）

- `election_watch.py`：扫描 `news.db` 新文章，`ElectionClassifier`（`config/election_watch.yaml` 关键词规则）识别涉台南选举文章，打分 relevance，写入 `election_fact_store`（事实库）。
- `election_classifier.py`：城市/人物/政党/议题匹配 + 选举上下文判定。
- `election_fact_store.py`：扫描状态与匹配事实存储（增量扫描、幂等）。
- `deepseek_analysis.py`：OpenAI 兼容的 DeepSeek 客户端（供文章梗概 `summarizer.py` 使用）。
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
│   ├── election_classifier.py election_watch.py election_fact_store.py
│   ├── election_event_merge.py election_utils.py deepseek_analysis.py
│   ├── election2026/            # 九合一选举新闻识别（Word 专栏）
│   ├── election_candidates/     # 选情候选事实监控（生成 + 人工审核，不发布）
│   ├── special_topics/          # 民调 / 地下赌盘相关性门禁与标注
│   └── social_monitor/          # 政治人物与 KOL 社群监测
├── config/                      # sources.yaml / election_watch.yaml / importance_rules.yaml 等
├── data/
│   ├── news.db                  # 新闻库（勿删！去重状态在此）
│   ├── election_candidates/          # 候选库与审核模板（候选监控运行数据）
│   ├── election_context.db           # 历史正式事实库（只读查重，不再写入）
│   ├── election_seed/ research/ backup_rtXX_pre_import/   # 历史正式种子与研究资料（归档）
│   ├── reports/tainan_2026/          # 历史研判报告归档（功能已移除，仅保留数据）
│   └── diagnostics/                  # 采集诊断输出
├── prompts/                     # summarize.md 文章梗概提示词
├── tests/                       # 离线测试（采集、去重、Word、通知、候选监控等）
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
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 旧版 .env 兼容：未设置 `SUMMARIZER_*` 时文章梗概回退使用；本地 Command Code 网关（`app.cmd_gateway`）的上游端点 |
| `SUMMARIZER_MODE` / `SUMMARIZER_MAX_LENGTH` / `SUMMARIZER_BATCH_SIZE` | 文章梗概：模式（rss/llm/meta/hybrid/none）、长度上限、LLM 批量大小 |
| `SUMMARIZER_CONTENT_CHARS` / `SUMMARIZER_RETRY_HOURS` | 正文截取字符数（默认800）、抓取失败重试间隔（小时） |
| `SUMMARIZER_BASE_URL` / `SUMMARIZER_MODEL` | 摘要 LLM 端点（默认本地 Command Code 网关 `http://127.0.0.1:8765/v1` + `deepseek/deepseek-v4-flash`；未设置时回退 `DEEPSEEK_*`） |
| `NEWS_DB_PATH` / `SOURCES_CONFIG_PATH` / `DATABASE_PATH` | 路径覆盖 |
| `NEWS_CATCHUP_ENABLED` / `NEWS_CATCHUP_MAX_MINUTES` | 补发开关与窗口 |
| `DISABLE_FEISHU_SEND` | 停用飞书发送 |
| `PLA_ACTIVITY_ENABLED` / `PLA_ACTIVITY_LIST_URL` | 国防部共军动态日报采集开关（默认关）、列表页地址（默认 `https://www.mnd.gov.tw/news/plaactlist`） |
| `AI_MODEL_FAST` / `AI_MODEL_STRONG` / `AI_CACHE_ENABLED` / `AI_CACHE_DAYS` | 两档模型、AI 回答缓存开关与天数（默认开、30 天） |
| `AI_MONTHLY_BUDGET_USD` / `AI_USAGE_DB_PATH` | AI 月度花费上限（默认 15 美元）、调用台账路径（默认 `data/ai_usage.db`） |
| `ARTICLE_TEXT_STORE` / `ARTICLE_TEXT_RETENTION_DAYS` / `ARTICLE_TEXT_FETCH_MAX` | 正文存储开关（默认开）、保留天数（默认 730）、每次额外抓取上限（默认 0 = 不额外请求） |
| `ARTICLE_TEXT_INSCOPE_CATEGORIES` / `ARTICLE_TEXT_INSCOPE_FETCH_MAX` | 试点分类（默认 `military`）及其每次额外抓取上限（默认 20） |

## 七、测试

```bash
python -m pytest -q
```

测试覆盖：采集器解析（含国际媒体真实结构离线 fixture）、去重/身份键、时效/重要度、国际相关性与跨媒体显示去重、Word 生成、通知渠道、配置校验、民调 / 赌盘 / KOL 来源，以及候选事实生成与审核流程。

## 八、维护注意事项

1. **不要删除 `data/news.db`**：包含文章历史与去重状态。
2. **不要删除候选库** `data/election_candidates/*/candidate_fact_pipeline.db`：包含审核记录与扫描游标。
3. **历史正式数据只读**：`election_context.db`、`election_seed/`、`research/` 为归档数据，代码不再写入，也不要手工修改（候选查重会读取）。

## License

MIT
