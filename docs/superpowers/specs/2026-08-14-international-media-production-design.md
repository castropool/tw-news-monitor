# 国际主流媒体免费监测层生产化设计

日期：2026-08-14  
状态：已获批准，作为 Phase I 的兼容扩展层设计

## 目标与成功条件

把现有 Reuters、Financial Times（FT Alphaville）、WSJ、Bloomberg 的免费公开监测能力推进到可长期运行的生产候选版本，同时保持台湾新闻、军武、宗教、Word、飞书和 Scheduler 的既有行为不变。最终数据流为：

```text
官方 RSS / Sitemap                 Gmail readonly Newsletter
        │                                   │
        └────── Article 标准化入口 ─────────┘
                         │
                    现有 SQLite
                         │
       freshness → relevance → 内存 EventCluster/coverage
                         │
                 importance / topic
                         │
             现有 LLM 中文标题与摘要能力
                         │
            Word 国际媒体栏目 / 事件级飞书候选
```

成功条件不是“能抓到更多标题”，而是：来源合法、源间故障隔离、相关性可解释、同一事件不重复刷屏、metadata-only 不触发正文抓取、中文交付不伪造事实，并通过离线测试、隔离真实网络验收、幂等、首次启用保护和台湾回归门禁。

## 不可变边界

- 继续使用现有 `Article` dataclass、`articles` SQLite 表、URL UNIQUE 去重和主采集/摘要/通知流水线；不建立 `InternationalArticle`、events、coverage 或 snapshots 表。
- 允许为 `Article` 做向后兼容的可空字段扩展，但本轮优先复用 `section`、`language`、`access_level`；确有需要时才追加可迁移字段，并先有迁移与回归测试。
- Reuters 使用官方 news sitemap，`access_level=metadata_only`；FT 使用官方免费 RSS，`access_level=public`；WSJ/Bloomberg 只通过合法取得的官方 Newsletter，`access_level=newsletter`。所有模式都禁止抓付费正文。
- `metadata_only` 或 `newsletter` Article 不得由 summarizer、翻译器、fallback 或 URL resolver 访问文章页；Newsletter 的 teaser 只能使用邮件本身内容。
- 不绕过 paywall、登录、robots、验证码、Cloudflare 或订阅限制；不使用盗版镜像、12ft、缓存绕过、共享 Cookie、代理池或未经授权 API。
- 默认关闭 Reuters、FT、WSJ Newsletter、Bloomberg Newsletter 四个生产源；不修改 Scheduler、不自动发送真实飞书、不在生产 SQLite 上做验收。所有真实验收使用独立 config、SQLite、reports 和 notifier dry-run。
- 未完成 RC 门禁前，不得把任何源切为生产启用。通过 RC 后，Reuters/FT 仍需显式发布授权；WSJ/Bloomberg 还需要 Gmail OAuth 和真实邮件验收。

## 组件与责任

### 现有兼容入口

`app/newsletter.py` 保留原有 `parse_newsletter`、`NewsletterParser` 和适配器名称，作为兼容 facade，内部可委托新包。现有 `app/international.py` 保留配置加载、分类和显示层去重的兼容 API；扩展应以纯函数/小对象接入，不把邮箱或网络状态塞入该模块。

### `app/newsletter_ingestion/`

新增包按单一职责拆分：

- `models.py`：不可变或明确生命周期的 `NewsletterMessage`、`NewsletterItem`、抓取结果和来源标识；不携带密码、token、完整 Cookie。
- `mailbox.py`：`MailboxClient` 协议（按 label/folder、时间窗和 sender allowlist 读取），只返回标准消息，不暴露 Gmail 细节给 parser。
- `gmail_client.py`：使用 Google 官方 Python client（`google-api-python-client`、`google-auth`、`google-auth-httplib2`、`google-auth-oauthlib`），不改用自写 Gmail HTTP 协议；限定 `InternationalNews` label 与允许 sender/domain，默认不 mark-as-read、不删除、不移动、不回复、不转发。
- `oauth.py`：一次性本地授权/凭据加载，并定义不可变 `AuthContext`：`credentials_path: Path | None`、`token_path: Path | None`、`authorized: bool`、`reason: str`、`scope: str | None`、`scope_provenance: str | None`。`scope` 只能是 `GMAIL_READONLY_SCOPE` 常量（其值为官方 `https://www.googleapis.com/auth/gmail.readonly`）或未授权时的 `None`；`scope_provenance` 只能是 `None` 或批准枚举 `authorized_user_file`。AuthContext 不得包含 token、client secret、secret 值、完整凭据或 Gmail service object；token 文件在项目外或受保护路径，拒绝写入 Git、YAML、SQLite、日志和报告。
- `policy.py`：sender/domain allowlist、label、最大邮件大小、时间窗、媒体识别和拒绝原因。
- `parser.py`：HTML、plain text、multipart、EML fixture 解析；一封多文、缺时间、HTML 变化和编码错误都安全降级。
- `url_policy.py`：去除常见 tracking 参数；默认不跟踪 redirect；若明确配置允许，只能 HTTPS、allowlist 域名、有限跳转深度/超时，并拒绝 localhost、内网、非 HTTP(S) 和凭据注入。
- `collector.py`：把通过 policy 的消息交给媒体 adapter，输出现有 `Article[]`，设定 `newsletter`、英文语言、publisher、section、fetched_at 和稳定 source_id。
- `verify_sources.py`：只负责来源可用性证据的生成与只读合并，不负责启用 source。该文件及 `tests/test_newsletter_source_verification.py` 由 Wave 2 Mailbox owner 唯一维护；`--mode public` 和 `--mode gmail` 必须分别输出独立 JSON；`--mode summary` 读取两份既有证据并创建第三份摘要，禁止覆盖任何输入或已有输出。

Gmail 官方库由 Wave 2 Mailbox owner 写入 `requirements.txt`：采用有上限的兼容版本窗口（`google-api-python-client>=2.170,<3`、`google-auth>=2.35,<3`、`google-auth-httplib2>=0.2,<1`、`google-auth-oauthlib>=1.2,<2`），并在 `validation/international_media/dependency_versions.json` 记录 clean install 的解析版本；不得无上限升级或把 Gmail 依赖写入运行时动态安装。Wave 2 必须完成 clean-install/import 测试：新建干净 venv，`python -m pip install -r requirements.txt`，再执行 `python -c "import googleapiclient.discovery, google.oauth2.credentials, google_auth_httplib2, google_auth_oauthlib.flow; import app.newsletter_ingestion.gmail_client, app.newsletter_ingestion.verify_sources"` 和 `python -m pytest -q tests/test_newsletter_source_verification.py`；没有 OAuth 也必须能完成 import/fixture 测试。

WSJ/Bloomberg adapter 必须只识别当前实际可用的官方 Newsletter 名称和 sender；名称或 sender 失效时报告 `disabled/stale`，不得硬编码已失效 Newsletter 为“可用”。未来增加 AP/BBC 等媒体只需新 adapter/config，不改 mailbox 协议。

### 四家媒体的入口契约

| 媒体 | 合法入口 | Article 模式 | 生产条件 |
| --- | --- | --- | --- |
| Reuters | 官方 news sitemap index 与有限分页 sitemap | `metadata_only`，只保留 title、canonical URL、publication time、section | sitemap 可达、结构校验通过、时间解析和增量/首次 baseline 门禁通过 |
| Financial Times | FT Alphaville 官方 RSS | `public`，只使用 RSS 的 title、description、link、pubDate | RSS 可达且稳定；不得访问 FT 文章页补正文 |
| Wall Street Journal | 当前实际有效且允许免费订阅的官方 Newsletter | `newsletter`，只使用合法邮件内容 | sender、Newsletter 名称和邮件 fixture/真实验收均通过；旧冻结 RSS 不得冒充生产源 |
| Bloomberg | 当前实际有效且允许免费订阅的官方 Newsletter | `newsletter`，只使用合法邮件内容 | sender、Newsletter 名称和邮件 fixture/真实验收均通过；不使用 robots 受限的普通 Python collector |

Reuters collector 需要有限分页、增量时间判断、UTC/GMT/RFC3339 等时间解析和 sitemap 结构变化告警，但不因缺少摘要而失败。FT collector 不因缺少 description 而抓网页。WSJ/Bloomberg 若实际 Newsletter 名称或 sender 变化，先标记 `stale/degraded` 并要求更新 allowlist/fixture，不能把失效入口硬编码为成功。

### `sources.yaml` 列表 schema 与稳定 ID

`config/sources.yaml` 的顶层必须是 `sources: [mapping, ...]`。每个 source mapping 至少包含 `id`（非空且唯一）、`name`、`type`、`category`、`url` 和布尔 `enabled`；可选 `access_level`、`language`、`section`、`collector` 等字段必须保持现有校验兼容。四个生产候选的 exact ID 和入口如下：

```yaml
sources:
  - id: reuters_international
    name: Reuters
    type: reuters
    category: international
    url: https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml
    enabled: false
    language: en
    access_level: metadata_only
  - id: ft_alphaville
    name: Financial Times
    type: ft_alphaville
    category: international
    url: https://www.ft.com/alphaville?format=rss
    enabled: false
    language: en
    section: alphaville
    access_level: public
  - id: wsj_newsletter
    name: Wall Street Journal
    type: wsj_newsletter
    category: international
    url: https://www.wsj.com/newsletters
    enabled: false
    language: en
    access_level: newsletter
  - id: bloomberg_newsletter
    name: Bloomberg
    type: bloomberg_newsletter
    category: international
    url: https://www.bloomberg.com/newsletters
    enabled: false
    language: en
    access_level: newsletter
```

`url` 对 Newsletter source 是官方 Newsletter 目录/订阅页，不是正文抓取地址；真正的邮件查询只允许 Gmail label + sender allowlist。旧 `id: wsj_international`、`type: wsj_rss` 必须保留为冻结兼容条目且永久 `enabled: false`，不能重命名、复用、alias 到 `wsj_newsletter`，也不能把冻结 RSS 当作 WSJ 生产证据。四个 exact ID 各自独立开关，缺失、重复、拼写变体或字符串形式的 `enabled` 均使配置门禁失败。

### WSJ/Bloomberg live availability 责任与证据

WSJ/Bloomberg Newsletter 的“存在目录页”与“邮箱中收到可解析邮件”是两个不同门禁。Release/独立复核代理负责执行公开页面核验，操作员负责执行 Gmail OAuth 后的真实邮件核验；任何子代理不得代替操作员登录、保存凭据或声称已验收。两项核验都必须写入以下证据文件（日期为实际执行日；本 RC 文档基线日为 `2026-08-14`）：

```text
validation/international_media/newsletter_availability/wsj_newsletter_public_2026-08-14.json
validation/international_media/newsletter_availability/wsj_newsletter_gmail_2026-08-14.json
validation/international_media/newsletter_availability/wsj_newsletter_summary_2026-08-14.json
validation/international_media/newsletter_availability/bloomberg_newsletter_public_2026-08-14.json
validation/international_media/newsletter_availability/bloomberg_newsletter_gmail_2026-08-14.json
validation/international_media/newsletter_availability/bloomberg_newsletter_summary_2026-08-14.json
validation/international_media/newsletter_availability/newsletter_live_verification_manifest.json
```

命令契约固定为：

```powershell
python -m app.newsletter_ingestion.verify_sources --mode public --source wsj_newsletter --public-page https://www.wsj.com/newsletters --as-of 2026-08-14 --output validation/international_media/newsletter_availability/wsj_newsletter_public_2026-08-14.json
python -m app.newsletter_ingestion.verify_sources --mode public --source bloomberg_newsletter --public-page https://www.bloomberg.com/newsletters --as-of 2026-08-14 --output validation/international_media/newsletter_availability/bloomberg_newsletter_public_2026-08-14.json
python -m app.newsletter_ingestion.verify_sources --mode gmail --mailbox gmail --label InternationalNews --source wsj_newsletter --since 30d --output validation/international_media/newsletter_availability/wsj_newsletter_gmail_2026-08-14.json
python -m app.newsletter_ingestion.verify_sources --mode gmail --mailbox gmail --label InternationalNews --source bloomberg_newsletter --since 30d --output validation/international_media/newsletter_availability/bloomberg_newsletter_gmail_2026-08-14.json
python -m app.newsletter_ingestion.verify_sources --mode summary --source wsj_newsletter --public-evidence validation/international_media/newsletter_availability/wsj_newsletter_public_2026-08-14.json --gmail-evidence validation/international_media/newsletter_availability/wsj_newsletter_gmail_2026-08-14.json --output validation/international_media/newsletter_availability/wsj_newsletter_summary_2026-08-14.json
python -m app.newsletter_ingestion.verify_sources --mode summary --source bloomberg_newsletter --public-evidence validation/international_media/newsletter_availability/bloomberg_newsletter_public_2026-08-14.json --gmail-evidence validation/international_media/newsletter_availability/bloomberg_newsletter_gmail_2026-08-14.json --output validation/international_media/newsletter_availability/bloomberg_newsletter_summary_2026-08-14.json
```

`--mode public` 只能写 `*_public_YYYY-MM-DD.json`，`--mode gmail` 只能写 `*_gmail_YYYY-MM-DD.json`；两者不得共用路径，路径已存在时命令必须失败而不是覆盖。`--mode summary` 只读两份原始证据，输出新 `*_summary_YYYY-MM-DD.json`，输入或输出已存在时同样失败；summary 必须保存输入文件 SHA-256、合并规则和状态，不得修改原始证据。`tests/test_newsletter_source_verification.py` 必须覆盖三种模式、路径冲突和禁止覆盖。

每份 evidence 至少记录 `source_id`、`verification_date`、公开页 URL（public）、观察到的 Newsletter 名称与 sender/domain、邮件数量/解析数量（gmail）、去重后的 message-id hash、allowlist 命中、auth state、错误、verifier 和 evidence SHA-256。没有 OAuth 或没有真实邮件时 Gmail evidence 必须写 `status: operator_action_required`、`reason: MAILBOX_AUTH_REQUIRED`，不能写 `verified`；summary 只有在 public 与 Gmail 都 verified 时才可为 verified。即使公开目录页可达，source 仍保持 disabled。只有 summary verified，且 fixture/live parser、sender policy 和隔离两轮通过，才允许操作员在 RC 后单独授权该源。

### 相关性与重要性

`RelevanceDecision` 为运行时值对象，至少包含 `relevant`、`tier`、`topics`、命中实体/议题、`reason`、规则版本和输入摘要 hash。判定输入为标题、邮件 teaser/RSS description、section 和 source metadata，不读取付费正文。

判定顺序：

1. 台湾/台北/台海/TSMC/台湾政治等直接实体或事件命中，进入 `taiwan_direct`。
2. China/Beijing/CCP 等涉华命中，必须同时有台湾、美国印太、军事、外交、贸易、制裁或半导体上下文；单纯中国社会、餐饮、娱乐新闻排除。
3. Washington/Pentagon/Japan/Philippines/semiconductor 等国际词，必须与台湾或中国上下文组合；普通 Washington 地方新闻、普通 Japan 国内政治和一般 semiconductor 公司新闻排除。
4. 规则输出可解释理由；不以单个关键词或媒体身份自动纳入。

Importance 仍由现有 importance 规则决定。Reuters/FT/WSJ/Bloomberg 仅有小幅 tier-1 bonus，且不得超过既有官方源 bonus；没有军事、外交、政策、重大产业/半导体、冲突或其他事件证据时，普通 Reuters 不能因来源名称变成 important。

### 内存 EventCluster 与 coverage

在 digest/Word/通知前建立短生命周期的 `EventCluster`：包含 canonical Article、members、coverage、事件 fingerprint、时间范围、topics 和聚合后的 source names。它只存在于当前 run 的内存，不写新表，不改变数据库 URL 记录。

聚类至少结合 URL/canonical URL、标题归一化 token、实体、事件词、时间窗（默认 24 小时）和相似度；相似度不足、时间缺失/越窗、跨天重大后续或只有宽泛 `China`/`Taiwan` 词时宁可不合并。canonical 选择规则固定且可解释（优先较早事件标题，再按 Reuters、FT、WSJ、Bloomberg source priority）。同一事件保留一个主条目，在 Word/飞书显示“另据……报道”，但不同 URL 仍各自入库。

事件后续若出现新的动作、结果或显著时间跨度，应形成新 cluster，不能因为同一实体强行吞入旧事件。单源聚类失败只收缩 coverage，不阻塞主流程。

### 中文交付

新增或复用可替换的 `InternationalNewsTranslator`/现有 summarizer abstraction。输出至少包含中文标题、100—250 字中文摘要（可按现有 Word 风格调整）、英文原标题、source display name、发布时间和原文 URL。标题/摘要只能基于 Article 可合法取得的标题与 teaser；缺乏足够事实时保留英文/元数据并标记限制，禁止编造摘要。

摘要必须区分“媒体报道事实”和“系统判断”；人名首次出现采用“中文名（英文名）”，机构使用通用中文译法，不改变数字、日期、因果或归因。LLM 失败时保留原始 Article，不把模型错误变成通知错误；不为重试而访问受限正文。

翻译器必须可注入 `FakeTranslator`。测试用 fake translator 返回确定性中文结果，另测 translator 抛错/返回空值时的 fallback：保留英文原标题、合法 teaser/元数据，写入 `translation_status=fallback` 和限制原因；测试用 monkeypatch 将任何文章页/正文 HTTP 调用设为立即失败，证明 metadata-only 与 newsletter 全路径 `body_fetch_count=0`。

### Source Health 旁路状态

每个国际源维护独立旁路状态文件（建议 `data/source_health/international.json`，路径可配置），原子写入、可删除重建，不改生产 `articles` schema。状态字段：`source_id`、`status`（`healthy/degraded/stale/broken/disabled`）、`last_success`、`last_item_at`、`items_fetched`、`parse_errors`、`consecutive_failures`、`last_error_code`、`updated_at`。

状态规则必须区分“合法 0 条”与“源坏掉”：HTTP/解析错误、连续失败和结构校验失败进入 degraded/broken；连续成功但长时间无条目进入 stale；配置关闭为 disabled。状态记录不得包含 OAuth secret、token、完整邮件或正文。单源健康状态不得让台湾采集、Word 整体生成或 Feishu notifier 失败。

真实 smoke 的空源/结构变化判定固定为：HTTP 200、XML/HTML/RSS schema 校验通过且确实解析出 0 条，记为本轮成功（`healthy`、`items_fetched=0`），不得伪报 broken；连续 3 次成功但 0 条或距 `last_item_at` 超过 48 小时才转 `stale`。缺少必需节点、响应非预期格式、非空响应无法解析出任何合法 item，记为 `degraded` 并增加 `parse_errors`；连续 3 次结构/HTTP 失败转 `broken`。403/401、TLS/超时按同一失败计数处理。证据必须保留原始响应 hash、HTTP 状态、解析器版本、必需字段检查结果和 `health_transition`，不得保存受限正文。

## 配置与安全

生产配置需要独立、可审计的四个源开关，示意如下；实际字段须服从当前 `config/sources.yaml` 的 schema：

```yaml
international_media:
  reuters:
    enabled: false
  ft_alphaville:
    enabled: false
  wsj_newsletter:
    enabled: false
  bloomberg_newsletter:
    enabled: false
```

Newsletter 邮箱还必须有独立的 label/folder、sender/domain allowlist、readonly scope、最大邮件大小、时间窗、mark-as-read（默认 false）和 redirect policy。逻辑配置 `international_media.yaml` 的缺失/损坏必须 fail closed；它不能绕过单源 `enabled=false`。测试 config 与生产 config 分离，测试不得读取真实凭据。

日志只记录 source、时间、抓取/解析/相关/重要数量、错误类别和健康状态，不记录邮件完整内容、token、密码、Cookie 或 Authorization。OAuth 需要用户一次性操作时，自动流程停止在 `OPERATOR_ACTION_REQUIRED`，但继续完成所有 fixture、离线和不依赖邮箱的门禁。

## 错误、重试和首次启用

- HTTP 客户端必须有 connect/read timeout、有限重试、指数或等价 backoff、最大响应大小和明确 User-Agent；不得无限等待或无限重试。
- Sitemap/RSS/邮件解析失败只影响该源；主流程继续台湾和其他国际源，异常写日志与 Source Health。
- 同一邮件/URL 的重复通过 normalized URL、message id（若有）和文章 identity 去重；重复运行不重复入库、不重复覆盖 Word/通知。
- 新源首次启用先取得 baseline；历史 catch-up 可以入库但不得作为新鲜新闻推送。旧闻时间统一为 UTC-aware，Word 以 Asia/Taipei 显示。
- metadata-only 与 newsletter 始终禁止正文抓取；tracking URL 清理失败时拒收或保留原合法 URL并记录原因，不追踪可疑跳转。

## Word、digest 与飞书

保留现有一级栏目“新闻媒体”，国际媒体使用 Heading 2，不破坏政治、军武、宗教及其他栏目。国际媒体条目按重大/重要/关注和 `published_at DESC` 排序，每项显示重要性、中文标题、来源、英文原标题、摘要、时间、原文链接。EventCluster 的 coverage 只渲染一次主事件及其他来源名称，不渲染四篇重复全文。

重大国际事件进入现有重要新闻候选集合，采用事件级一次提醒并标注 Reuters/FT 等 coverage；普通相关国际新闻只进 Word。具体路径固定为：`app/notification_candidates.py::build_notification_candidates(clusters, importance_results, freshness_state, now)` 负责生成候选，`app/notification_candidates.py::deduplicate_notification_candidates(candidates)` 负责按 `event_id/dedup_key` 收敛，`app/main.py::main()` 只负责把结果传给 notifier，`app/notifier.py::Notifier.send_event_candidates(candidates)` 负责消费。不能把 Article 列表直接交给 notifier。每个 candidate 至少包含 `event_id`、canonical URL、中文标题、importance level/score、relevance reason、freshness/baseline 状态、coverage source names、coverage URLs、dedup key 和 `notifiable`。

默认规则固定为：`relevant=true`、非旧闻/非 baseline catch-up、最终 importance `score >= 65` 且 level 为 `important` 或 `critical` 才可进入 candidates；`critical` 的现有阈值为 85。普通 relevant（score < 65 或 level=normal）只能进 Word。每个 EventCluster 最多生成一个 candidate；同一 `event_id` 在一次 run 及重试中只能提醒一次，重复 Article/coverage 不能产生第二个 candidate。候选不足或聚类异常时安全收缩为零候选，不放宽到普通相关新闻。

`app/notifier.py` 必须继续支持现有 `send(text)` 兼容接口，但事件通知只接受 `notification_candidates`；`RecordingNotifier`/`NullNotifier` 用于所有自动化和子代理测试。真实 Feishu adapter 不得在自动化路径被调用；需要真实发送时只能由 RC 之后的操作员显式动作触发，并有独立审计记录。本设计不修改 Scheduler、不重建 Feishu。

## 测试与验收门禁

### 离线测试

fixture 至少覆盖：四家媒体真实格式的正常邮件、多文章、multipart、tracking URL、重复 URL、无摘要、无时间、HTML 结构变化、编码错误；相关性/重要性黄金样本覆盖军演、军售、芯片限制、TSMC 投资、中国外交、普通 China/Washington/semiconductor、餐馆、地方事件、Pentagon 人员和 Japan 内政；跨源样本必须验证 `1 canonical + coverage`，且后续重大进展不误合并。

每条黄金样本是 JSONL mapping，必须逐条写出：`case_id`、`title`、`summary`/teaser、`source_id`、`published_at`、`expected_relevant`、`expected_tier`、`expected_topics`、`expected_entities`、`expected_importance_level`、`expected_min_score`（或 exact score）、`expected_notification`、`expected_cluster_id`（负例为 null）、`expected_is_canonical`、`expected_coverage_source_ids`、`expected_reason_contains`、`body_fetch_forbidden=true`。预测结果保存为同结构的 `actual_*` 字段，不以人工阅读替代断言。

最小黄金规模固定为：相关性/重要性 32 条（16 条正例、16 条负例）。16 条正例至少包含军演/导弹/军售 4、外交/对台政策/制裁 4、TSMC/芯片限制/贸易 4、中国—美国—印太重大政治安全 4；16 条负例至少包含 China 餐馆/社会新闻 3、Washington 地方新闻 3、`Taiwan Semiconductor` 名称歧义 3、普通 semiconductor 公司 3、普通 Pentagon 人员故事 2、日本国内政治 2。另建事件 pair corpus 至少 12 对：6 对同事件正例（其中至少 1 对四家媒体 coverage）、4 对相似但不同事件负例、2 对跨日重大后续负例；Newsletter parser fixtures 为 4 家媒体各 8 类（正常、multi-article、无摘要、tracking、重复 URL、multipart、缺时间、HTML 结构变化）共至少 32 个 payload。少于任一类别最小量不得生成 RC。

相关性指标按逐条标签计算：`TP = expected_relevant=true && actual_relevant=true`，`FP = expected_relevant=false && actual_relevant=true`，`FN = expected_relevant=true && actual_relevant=false`；`precision=TP/(TP+FP)`、`recall=TP/(TP+FN)`，分母为 0 时测试失败而非视为通过。RC 阈值为 precision≥0.95、recall≥0.90，hard-negative 子集 FP=0；tier/topic/entity 期望字段 exact match≥0.90。重要性要求 level exact accuracy≥0.90、important/critical precision≥0.90，且“普通 Reuters + tier-1 bonus alone”必须保持 normal（现有 important=65、critical=85）。

事件正例必须逐对断言 `expected_pair_merge=true`、相同 `expected_cluster_id`、唯一 canonical、coverage source IDs 完全相等；事件负例必须逐对断言 `expected_pair_merge=false`、cluster IDs 不相等且各自 canonical。事件 pair precision、recall 均须 1.00；不得用“少合并”掩盖漏合并。notification 测试必须断言普通 relevant 的 candidates 为空、一个重要 cluster 恰好一个 candidate、同 cluster 四篇 coverage 重试仍只有一个 candidate。

所有单元/集成测试离线运行，禁止 pytest 依赖当天 Reuters/FT、真实 Gmail 或真实 Feishu。必须保留并回归现有台湾、Word、数据库、digest、通知和配置测试，不得删除、skip、降低断言。

### 隔离真实网络验收

使用独立 config、独立 SQLite、独立 reports 和关闭真实 Feishu，至少两轮逐源记录：`fetched`、`parsed`、`inserted`、`fresh`、`relevant`、`important`、`errors`。第二轮应证明幂等（新增为 0 或可解释的新增）及没有重复 Word/通知。Newsletter 未授权时必须诚实记录 `MAILBOX_AUTH_REQUIRED`，不得声称 WSJ/Bloomberg 已真实验收。

### RC 门禁

建立 `INTERNATIONAL_MEDIA_RELEASE_CANDIDATE` manifest，包含代码/config/fixture/prompt/状态 hash、测试命令、隔离运行记录和真实操作边界。最低门禁：

```text
python -m compileall app tests
python -m pytest -q
Reuters live + FT live（独立环境）
Newsletter fixture/EML integration
relevance / importance / false-positive
cross-source dedup + coverage
freshness / first-run baseline / idempotency
failure isolation + Taiwan regression
Word structure/OOXML；若可用再做 LibreOffice/PDF 像素检查
security scan（无 token/password/Cookie/Authorization）
```

安全门禁必须实际执行而不是只搜索文档文字。实现 `validation/international_media/security_scan.py` 后，RC 命令为：

```powershell
python validation/international_media/security_scan.py --paths app config tests docs validation --exclude '*.pyc' --exclude '*.db' --exclude 'data/**' --manifest validation/international_media/security_scan.json
```

命令发现真实 token、password、client secret、refresh/access token、`Authorization: Bearer`、完整 Cookie 或 Feishu secret 时返回非零；文档中的字段名称、明确的 fake 值和 schema key 必须通过结构化 allowlist 区分，不能用简单“出现 password 单词”冒充安全通过。`security_scan.json` 记录扫描路径、规则版本、命中数量（必须为 0）、排除项和时间。

任一门禁失败则状态为 `INTERNATIONAL_MEDIA_NOT_READY`，保持所有生产开关 false。只有全部自动门禁通过，且仅剩 Gmail OAuth 或 Word 像素级人工检查时，才可报告 `INTERNATIONAL_MEDIA_COMPLETE_WITH_OPERATOR_ACTION`。

## 发布、操作与回滚

发布前仅在独立目录完成 config/DB/reports 备份、记录当前 Scheduler 与 git 状态；不自动安装/修改 Scheduler，不执行真实群测试。默认发布顺序是先 Reuters、FT；WSJ/Bloomberg 需各自真实 Newsletter 与 OAuth 验收后才能单独开启。

停用单源只需把该源开关设为 false，并保留其他源；若出现源结构变化、重复刷屏或误报，先关闭问题源、保留 SQLite 历史和健康文件，再用 RC manifest 定位。回滚不删除 `articles`、不回滚共享数据库、不重写台湾配置；代码/config 恢复到发布前备份或上一 RC，旁路 health 文件可安全重建。OAuth 撤销由操作员在 Google 端完成，应用只删除其项目外 token 文件（如获授权）。

## 最终报告契约（严格 A–Q）

最终报告不得只写“完成”，必须按以下固定顺序逐项给出证据；没有适用数据也要写 `not_run`/`operator_action_required` 及理由：

| 项目 | 必须报告 |
| --- | --- |
| A | 最终架构与完整数据流 |
| B | 新增、修改、删除文件及职责、所有权、hash |
| C | Reuters/FT/WSJ/Bloomberg 技术入口、access level、正文能力、enabled、health、证据 |
| D | 邮箱接入方式、label/sender policy、adapter、tracking、去重、真实邮件验收/`MAILBOX_AUTH_REQUIRED` |
| E | 黄金样本数量、TP/FP/FN、precision/recall、阈值与误报/漏报 |
| F | 重要性样本、score/level accuracy、tier-1 bonus 与阈值验证 |
| G | 跨媒体 canonical、coverage、正负例和事件级提醒去重 |
| H | Article/SQLite schema、迁移、URL/canonical 去重和“不新增事件表”证据 |
| I | Word 国际媒体栏目、中文标题/摘要、英文原标题、排序、coverage、渲染证据 |
| J | readonly、sender allowlist、无密码/token/Cookie/secret 证据 |
| K | compileall、pytest 总数/passed/failed/skipped、专门门禁 |
| L | Reuters/FT/Newsletter 隔离真实验收逐源统计和证据日期 |
| M | 第一/第二轮幂等、新增数、重复 Word/notification_candidates 对比 |
| N | 台湾源、军武、宗教、Word、Feishu dry-run、Scheduler、DB 回归 |
| O | 四源生产 enabled/disabled/operator action 状态及发布授权 |
| P | 真正剩余限制（包括无 OAuth、无 PDF 渲染工具等），不得把可解决问题伪装成限制 |
| Q | 三种最终状态之一及逐条结论：`INTERNATIONAL_MEDIA_COMPLETE`、`INTERNATIONAL_MEDIA_COMPLETE_WITH_OPERATOR_ACTION`、`INTERNATIONAL_MEDIA_NOT_READY` |

没有 Git 时，B 项以 SHA-256 manifest 代替 commit/status 证据。基线 manifest 必须覆盖实现、配置、所有 `prompts/`（包括 Newsletter/翻译 prompt）、fixture、测试和操作文档；最终 validation manifest 还必须覆盖 `validation/international_media/` 下的最终 RC manifest、summary/live evidence、security scan、隔离两轮结果、Word OOXML/PDF（如生成）和最终 A–Q 报告。两个 manifest 都要记录生成命令、时间、排除项、相对路径、文件大小和 SHA-256；manifest 自身只能由另一个 manifest 的 exclude 规则排除，不能以“没有 Git”省略 prompts 或最终 validation 产物。

## 非目标

不实现付费 API、全文付费墙抓取、复杂事件数据库、消息队列、搜索集群、Kubernetes、Web Dashboard、浏览器自动化绕过、代理池、商业翻译锁定、Scheduler 重建或真实飞书测试发送。
