# 国际媒体免费监测层 Phase I 设计

日期：2026-08-14  
状态：已批准（方案 A）

## 目标与边界

在现有台湾新闻流水线内接入国际主流媒体的合法免费公开元数据，用于发现涉台重大报道，不以取得付费正文为目标。Phase I 自动采集范围仅包括 Reuters 官方新闻 sitemap 与经真实网络验收可用的 FT Alphaville 官方 RSS；WSJ 与 Bloomberg 只保留本地 Newsletter 解析适配器。不得绕过付费墙、登录、robots、反自动化、验证码或订阅限制。

生产 `config/sources.yaml` 中 Reuters、FT、WSJ 均保持 `enabled: false`。真实验收使用独立来源配置、独立 SQLite、独立报告目录，并关闭飞书。Scheduler、现有飞书协议、台湾来源行为和台湾评分规则不变。

## 数据流

```text
国际官方公开入口
  -> 现有 BaseCollector / NewsletterAdapter
  -> 现有 Article（section/language/access_level 为可空兼容字段）
  -> URL + identity 去重
  -> SQLite 入库
  -> freshness（首次启用历史保护）
  -> international relevance
  -> 国际显示层事件聚类去重
  -> importance（有限来源加分）
  -> digest / Word“新闻媒体 -> 国际媒体” / 通知
```

所有合法采集到的国际文章均可按 URL 入库；只有新鲜且相关的文章进入评分、简报和通知。三日前旧闻可以入库但不得推送。首次启用来源的 catch-up 文章不得补发；来源基线必须在本轮插入前取得。

## 来源设计

### Reuters

- 使用 `config/sources.yaml` 提供的官方 `news-sitemap-index` URL，collector 不硬编码入口。
- 解析 title、canonical URL、publication date、section；无摘要不是失败。
- 不访问文章页，不抓正文，`language=en`、`access_level=metadata_only`。
- 最多返回 20 条；单页不足时有限分页，单源异常由主流程隔离。

### Financial Times

- 使用 FT Alphaville 官方 RSS，只读取 feed 中的 title、description、link、pubDate。
- 不访问文章页补正文；缺 summary 或时间时保守保留元数据。
- `source_name=Financial Times`、`section=Alphaville`、`language=en`、`access_level=public`。
- 最终 URL 以真实网络验收为准；若当前环境无法稳定读取，保持生产禁用并在结果中列为限制。

### Wall Street Journal

- 官方 Dow Jones RSS 已冻结且条目为 PAID，生产禁用。
- 保留 `WSJRSSCollector` 作为未来重新核验入口，但 Phase I 不纳入真实采集。
- `WSJNewsletterAdapter` 只解析用户合法取得的本地 HTML/EML/text，不接 IMAP/Gmail OAuth。

### Bloomberg

- robots 明确限制 Python HTTP 客户端与 feed 聚合器，Phase I 不实现网络 collector。
- 仅保留 `BloombergNewsletterAdapter`，解析合法取得的本地 newsletter fixture。

## 数据模型与数据库

继续使用单一 `Article`。`source_name` 保存英文 canonical publisher；新增字段仅为兼容扩展：

- `section: str | None`
- `language: str | None`
- `access_level: public | metadata_only | newsletter | None`

数据库仍为单一 `articles` 表，通过可空列追加迁移兼容旧库。URL UNIQUE 约束不变，不建立 events、coverage 等新表。跨媒体 coverage 仅在本轮显示层内存中维护。

## Newsletter 契约

低层解析器支持 HTML、EML、plain text，处理一封多篇、tracking URL 清理、规范化后重复 URL 去重和缺失时间。适配器负责把解析结果转换为现有 `Article[]`，补齐 source_id、source_name、category、position、fetched_at、language、section 与 `access_level=newsletter`。不连接真实邮箱。

## 相关性、分类与重要性

相关性按三层上下文规则：台湾直接相关直接纳入；中国大陆新闻只有同时涉及台湾、美国、军事、外交、半导体、制裁或贸易政策时纳入；美国及国际议题必须同时关联台湾或中国。普通欧洲体育和一般 China 社会新闻不进入简报。

内部 topic 为轻量运行时分类，不新增数据库 schema。Reuters、FT、WSJ、Bloomberg 只获得有限 tier-1 source bonus，且 bonus 不得超过现有官方来源 bonus；无事件规则命中的文章仍为 normal，不能仅凭来源成为重要新闻。

## 跨媒体去重

数据库继续保留不同 URL。Word/digest 对新鲜且相关的国际文章按时间窗、归一化标题相似度和共享核心概念聚类，保留一个 canonical，并在 coverage 中列出其他媒体。非国际来源不参与该聚类。宁可少合并，不因单一宽泛关键词误删报道。

## 摘要与访问安全

- Reuters `metadata_only`、所有 `newsletter` 文章不得进入正文或 meta-description 网络抓取。
- FT 只使用 RSS 自带摘要，不因缺摘要访问文章页。
- 国际媒体若缺摘要，可用现有仅基于标题/已有 teaser 的 LLM 能力，但不得传入抓取正文。
- 台湾来源沿用现有摘要行为。

## 错误处理

每个 collector 独立 try/except/finally；单源失败记录 warning/error 并继续其他来源。国际配置缺失或损坏时安全禁用国际逻辑。相关性或显示去重异常时不得中断入库；通知集合优先安全收缩，不能把未过滤国际文章作为回退推送。

## 测试与验收

测试样本存放于独立 fixture 文件，不以线上访问作为唯一依据：

- Reuters：正常、无摘要、时间格式变化、重复、旧闻。
- FT：正常 RSS、缺 summary、HTML entity、旧闻。
- Newsletter：一封多篇、重复 URL、tracking URL、无时间。
- 集成：台湾相关新闻入库并进入 freshness；体育新闻入库但不进简报；FT TSMC 进入相关性；Reuters/FT/Bloomberg 同事件只显示一次；国际源失败不影响台湾源；三日前文章入库不推送；首次来源 catch-up 不补发；国际 metadata/newsletter 不抓正文。

质量门禁：`python -m compileall app tests`、全量 `pytest` 必须 0 failed，不删除或降低原断言。

真实验收使用 `validation/international_phase1/` 下的独立配置和脚本/记录：第一轮记录 fetched、parsed、inserted、fresh、relevant、important、errors；立即第二轮证明 inserted=0 且无重复 Word/通知；生成独立 Word 并检查原栏目、国际媒体栏目、中文/英文、URL 与排序。若 FT 网络不可达，只验收 Reuters 并将 FT 标为未通过网络门禁，生产仍禁用。

## 非目标

不实现付费正文、商业 API、Gmail OAuth/IMAP、浏览器自动化、代理池、Cloudflare 绕过、全文翻译、事件数据库、Scheduler 修改或生产自动启用。

## 回滚

生产来源默认关闭，因此功能回滚只需保持 `enabled: false` 并停用 `international_media.yaml`。新增数据库列均可空且旧代码可忽略，不需要破坏性 schema 回滚。代码回滚按本阶段文件清单逐项恢复，不删除实时数据库或历史文章。
