# 国际媒体免费监测层 Phase I — 调研结论与基础改造记录

- 日期：2026-08-13
- 范围：Reuters / Financial Times / Wall Street Journal / Bloomberg 四家国际媒体的免费机器可读入口调研，及 Phase I 基础改造（模型 / 数据库 / 配置 / 文档）。
- 相关任务：后续任务将实现 collector（`reuters` / `ft_alphaville` / `wsj_rss`）、注册 `COLLECTOR_MAP`、接入 main 流程并读取 `config/international_media.yaml`。

## 1. 调研结论（冻结，原样采用）

| 媒体 | 实际免费入口 | 类型 | 是否官方 | 是否免费 | 是否需登录 | 是否含摘要 | 是否含正文 | 稳定性 | 是否启用 |
|---|---|---|---|---|---|---|---|---|---|
| Reuters | 官方 Google News sitemap：index=`https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml`；每页=`https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml`（`&from=N` 分页，每页约100条）。条目含 `<loc>`（规范化文章 URL）、`<news:title>`、`<news:publication_date>`（ISO8601 Z）、`<lastmod>`，无 summary | sitemap XML | 是 | 是 | 否 | 否（仅标题/日期/URL） | 否 | 2026-08-14 实测 200 且持续更新 | 生产停用；隔离验收启用 |
| Financial Times | FT Alphaville 官方 RSS：当前 URL=`https://www.ft.com/alphaville?format=rss`；旧 `https://ftalphaville.ft.com/feed/` 于 2026-08-14 实测 301 到当前 URL。提供 title / description(简短摘要) / link / pubDate / guid，无全文 | RSS 2.0 | 是 | 是 | 否 | 是（简短摘要） | 否 | curl/TLS 路径失败，但项目 httpx collector 隔离验收连续两轮各解析 20 条 | 生产停用；由人工决定启用 |
| Wall Street Journal | 官方 Dow Jones RSS `https://feeds.a.dj.com/rss/RSSWorldNews.xml` 技术上可访问，但已冻结（lastBuildDate≈2025-01-27）且所有条目 `<category domain="AccessClassName">PAID</category>` | RSS 2.0 | 是 | 否（全部 PAID） | — | 仅元数据 | 否 | 已冻结（lastBuildDate≈2025-01-27），不可靠 | 停用（`enabled: false`），仅保留 collector 与 adapter 占位 |
| Bloomberg | 无公开 RSS；robots.txt 显式 `Disallow: /` 针对 python-requests / Python-http-client 及 Feedly/MWFeedParser 等聚合器 | 无 | — | — | — | — | — | — | 停用，仅 NewsletterAdapter 占位 |

### 补充说明（条目级细节）

1. **Reuters**：官方 RSS 已停用（arc/outboundfeeds 各 rss 端点 404）。存在非英语语言路径前缀 `/pt/ /es/ /fr/ /de/ /it/ /jp/ /latam/`。栏目可从 URL path 派生（`/world/china/` `/business/` `/markets/` `/technology/` `/sports/` `/legal/` `/commentary/` 等），Phase I 不依赖固定 section，后续 collector 从 URL path 提取。
2. **FT Alphaville**：栏目固定 Alphaville（sources.yaml 条目 `section: alphaville`）。
3. **WSJ**：判定不可靠 → Phase I 停用，仅保留占位（sources.yaml 条目 `enabled: false`）。
4. **Bloomberg**：无合法公开机器入口（robots.txt 显式禁止聚合器）→ Phase I 停用，仅 NewsletterAdapter 占位，不建 sources.yaml 条目。

## 2. "宁缺毋滥"结论

- 只接入**官方**、**免费**、**无需登录**、**机器可读**的入口；宁可少一个源，不接不可靠或越权入口。
- WSJ（冻结 + 全 PAID）与 Bloomberg（robots.txt 禁止聚合器）在 Phase I 停用，仅保留占位，不浪费采集与去重资源。
- 四家在 Phase I 均只使用元数据级信息（标题/URL/时间，FT 多一个 feed 简短摘要），**不抓取文章页正文**。
- `access_level` 语义：`public`=公开 feed 条目（FT）；`metadata_only`=只使用机器入口元数据（Reuters / WSJ）；`newsletter`=经用户合法取得的 Newsletter 渠道解析。

## 3. robots / 版权边界说明

- Bloomberg：robots.txt 显式 `Disallow: /` 针对 python-requests / Python-http-client 及 Feedly/MWFeedParser 等聚合器 → 不尝试绕过，Phase I 不接入。
- Reuters / FT：均使用**官方** sitemap / RSS 端点（Google News sitemap 为 Google 与媒体合作产物，RSS 为官方发布），属官方向公众提供的机器可读出口，可接入。
- 版权边界：仅收集标题 / URL / 发布时间 / 简短摘要等元数据并做本地索引与去重；不存储正文、不转载全文、不规避付费墙、不绕开登录限制。WSJ 条目虽在 RSS 中出现，但因全部 PAID，仅保留元数据（`metadata_only`）。

## 4. 基础改造内容

### 4.1 Article 模型（`app/models.py`）

新增三个可空字段（默认 `None`，保持 `@dataclass(slots=True)`，现有构造点不破坏）：

- `section: str | None = None`
- `language: str | None = None`
- `access_level: str | None = None`（合法值：`public` / `metadata_only` / `newsletter`）

明确不新增：`publisher` 字段（`source_name` 复用为 canonical publisher，如 "Reuters"）、`content_type`、paywall 相关字段。

### 4.2 数据库（`app/database.py`）

- `articles` 表追加三列 `section TEXT` / `language TEXT` / `access_level TEXT`（均可空）。
- 复用现有 `_migrate_article_columns` 追加式迁移（无 schema 版本表，沿用现状）；旧库（已有数据）迁移后旧行三列均为 `NULL`，仍可正常读写。
- `save_article` / `save_articles` 的 INSERT 与 `get_articles_since` 的 SELECT 覆盖新列；可空字段为 `None` 时写入 `NULL`。
- 未改动的读取路径（如 `app/main.py` 中 backfill 的 SELECT）构造 Article 时新字段走默认 `None`，行为不变。

### 4.3 配置

**`config/sources.yaml`** 新增三条国际源条目（遵守现有 schema：id/name/category/type/url/enabled + 可选 language/section/access_level；category 一律 `international`；name 用英文 canonical）：

| id | name | type | language | section | access_level | enabled |
|---|---|---|---|---|---|---|
| `reuters_international` | Reuters | `reuters` | `en` | （由 URL path 派生） | `metadata_only` | false |
| `ft_alphaville` | Financial Times | `ft_alphaville` | `en` | `alphaville` | `public` | false |
| `wsj_international` | Wall Street Journal | `wsj_rss` | `en` | `world` | `metadata_only` | false |

**Bloomberg 不建 sources.yaml 条目**（adapter-only，见上）。

**`config/international_media.yaml`**（新建）承载国际媒体层逻辑配置：

- `display_names`：Reuters→路透社 / Financial Times→金融时报 / Wall Street Journal→华尔街日报 / Bloomberg→彭博社
- `tier1_international_media`：四家英文 canonical 名列表
- `relevance_keywords`：三层 `taiwan_direct` / `china_related` / `us_international`（当前为占位初稿，**需按任务书第十一章核对**）
- `dedup`：`similarity_threshold`（相似度阈值，默认 0.92）、`window_hours`（时间窗口小时数，默认 24）
- `source_bonus`：`tier1`（加分值，默认 3；硬约束不得超过 official 的 5）

## 5. 当前实现状态（2026-08-14）

- `reuters` / `ft_alphaville` / `wsj_rss` 已实现并注册 `COLLECTOR_MAP`。
- 国际相关性、显示层跨媒体去重、有限来源加分、Word 国际媒体栏目已接入；生产来源保持关闭。
- WSJ/Bloomberg NewsletterAdapter 只处理本地 HTML/EML/text，不接真实邮箱。
- 剩余验收工作以设计文档 `docs/superpowers/specs/2026-08-14-international-media-phase-i-design.md` 为准。

## 6. 验收记录（2026-08-13）

- `python -m compileall -q app tests`：通过。
- 新增测试：`tests/test_models.py`（Article 构造兼容旧参数、新字段默认 None）、`tests/test_database.py` 追加（新列可空、旧库迁移后旧行为 NULL、save/read 往返保真）。
- `pytest tests/test_database.py tests/test_config_validation.py tests/test_models.py -q`：通过。
- 全量 `pytest tests -q`：基线 2415 passed / 4 skipped / 0 failed；改造后除下述预期项外全绿。
- **当日中间态（已解决）**：2026-08-13 曾因新 type 尚未注册而出现配置校验失败；2026-08-14 三类 collector 已注册，完整测试恢复为 0 failed。

## 7. Phase I 最终隔离验收（2026-08-14）

- Reuters：第一轮 fetched/parsed/inserted/fresh=`20/20/20/20`，本轮 relevant/important=`0/0`；第二轮 inserted=`0`。
- FT Alphaville：第一轮 `20/20/20/0`，最新一条为 18:52（验收于 20:26，超过 90 分钟窗口），因此 relevant/important=`0/0`；第二轮 inserted=`0`。
- 两源 errors 均为 0；数据库、报告目录与结果 JSON 均位于 `validation/international_phase1/`，未调用 notifier，飞书显式关闭。
- fixture Word 将 Reuters/FT/Bloomberg 同一军演事件的 3 个标题合并为 1 个 canonical，coverage=3；政治、军武、宗教与国际媒体标题层级均通过结构检查。
- 全量门禁：`2564 passed / 4 skipped / 0 failed`。
