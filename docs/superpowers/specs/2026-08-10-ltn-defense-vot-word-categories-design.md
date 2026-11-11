# 自由时报军武、西藏之声与 Word“军武/宗教”板块实施记录

日期：2026-08-10  
状态：按用户最终附件任务实施；VOT 按内容分类，不整源归入宗教

## 1. 目标

在不改变现有新鲜度、去重、重要性、数据库、飞书配置和计划任务的前提下：

1. 启用自由时报军武 RSS 与西藏之声中文 RSS；
2. 在 Word“新闻媒体”区域增加 `military`（军武）与 `religion`（宗教）栏目；
3. 保持 `source ≠ category`：西藏之声是综合来源，文章按内容进入政治、国际、军武、宗教等既有栏目。

## 2. 修改前基线

- `python -m compileall -q app`：通过；
- `python -m pytest -q`：2279 passed、0 failed、4 skipped；
- 生产配置：25 个 source，其中 11 个启用；
- 生产数据库：2075 条文章，`PRAGMA integrity_check=ok`；
- LTN 军武 RSS 连续两次：HTTP 200、RSS 2.0、40 entries、字段完整；
- VOT `/feed/` 连续两次：HTTP 429、HTML 响应、0 entries、无 `Retry-After`。

VOT 当前网络结果标记为 `production_network_validation_pending=true`，不得伪造 Live 成功，也不得开发绕过访问控制的 HTML 抓取器。

## 3. 范围与非目标

### 3.1 本轮范围

- 生产 `config/sources.yaml` 启用 `ltn_defense` 与 `voice_of_tibet_cn`；
- 正式增加 `military`、`religion` 两个 category；
- 新增无固定分类来源的内容分类模块，VOT 按标题/摘要进行高精度确定性分类；
- 扩展文本 digest 与 Word 的 category 显示、顺序和动态编号；
- 增加离线 fixture、单元测试、隔离 Live 验收和 Word 视觉验收。

### 3.2 明确不做

- 不新增 LLM 分类器；
- 不改 importance 规则、阈值或算法；
- 不改 freshness 或 dedupe；
- 不改数据库 Schema；
- 不改飞书配置、Webhook、计划任务或网络代理；
- 不增加 VOT HTML fallback；
- 不新增第三个 category；
- 不抓正文、图片、音视频或历史归档。

## 4. Source 配置

### 4.1 自由时报军武

```yaml
- id: ltn_defense
  name: 自由时报·军武
  type: ltn_rss
  default_category: military
  url: https://news.ltn.com.tw/rss/def.xml
  enabled: true
```

垂直频道使用 `default_category=military`，继续复用 `LtnRSSCollector`，不新增第二套 LTN collector。

### 4.2 西藏之声

```yaml
- id: voice_of_tibet_cn
  name: 西藏之声
  type: rss
  url: https://cn.vot.org/feed/
  enabled: true
```

没有 `category` 或 `default_category`，因此进入内容分类逻辑。来源本身不决定栏目。

## 5. Category 设计

`app/category_classifier.py` 提供确定性、高精度分类：

- 宗教：藏传佛教、佛教、寺院、僧侣、法会、宗教自由、宗教政策、宗教迫害等；
- 军武：解放军、军事、国防、边境、部署、演练、武器、装备等；
- 国际：美国、联合国、欧洲议会、印度、国会、议员、法案等；
- 经济：经济、经贸、贸易、市场、投资等；
- 政治：政治、政策、政府、治理、法规、流亡等；
- 默认：politics。

“达赖喇嘛”“班禅”“西藏”“藏人”等词不单独触发宗教。例如“达赖喇嘛主持佛教法会”进入宗教，而“达赖喇嘛会见美国议员讨论西藏政策”进入国际。

`military` 与 `religion` 是正式通用 category；Word 和 digest 可以渲染任何 source 产生的这两类 Article。

## 6. RSS 与错误处理

### 6.1 LTN

沿用 Host 白名单、2 MiB 响应限制、RSS 摘要清洗、永久 URL 归一化和真实 pubDate → Asia/Taipei 转换。新频道只通过 source 配置进入现有 collector。

### 6.2 通用 RSS / VOT

- HTTP 响应先执行 `raise_for_status()`；
- feed 格式异常且没有有效 entry 时抛出明确解析错误；
- 不把抓取时间当发布时间；
- 不为 VOT 建立独立 HTTP client；
- 不无限重试；当前网络层没有统一 retry，HTTP 429 直接作为本 source 失败，由 `collect_all()` 记录并隔离；
- 不打印响应正文、Cookie、Token、Webhook 或代理凭证。

## 7. Digest 与 Word

正式 category 集合：

```text
politics, economy, military, international, religion
```

媒体生产顺序：

```text
政治新闻 → 经济新闻 → 军武 → 国际新闻 → 宗教
```

Word 继续复用现有逻辑：

- 先按 `is_official_source(source_id)` 拆分官方信源和新闻媒体；
- 官方信源结构、来源排序和样式不变；
- 媒体栏目为空时隐藏；
- 栏目编号按实际出现顺序动态连续；
- 栏目内按 `published_at` 倒序，position 作为次级排序；
- 军武和宗教复用同一段渲染代码，不新增独立 renderer；
- category 不产生重要性加分，也不强制进入 Word。

## 8. 数据与兼容性

- 数据库继续使用现有 `url` 唯一键和 article identity；
- LTN 不改为 `(source_id, url)` 唯一键，同一永久 URL 跨频道只能保存一次；
- VOT 同一 URL 连续采集只能保存一次；
- Article、数据库表和历史数据不做 migration；
- `app/importance.py` 与 `config/importance_rules.yaml` 不修改；
- `app/freshness.py`、`app/article_identity.py` 和去重实现不修改。

## 9. 测试设计

### 9.1 Fixtures

- `tests/fixtures/ltn_defense_feed.xml`：正常条目、HTML 摘要、真实带时区 pubDate、跨频道同 URL 场景；
- `tests/fixtures/vot_cn_feed.xml`：简繁中文、藏文字符、英文人名、不同主题样例、CDATA/HTML entity；
- malformed VOT feed 使用最小内联 fixture，不保存大量历史内容。

### 9.2 自动化门禁

测试至少证明：

- LTN 字段、清洗、时区、category、Host 白名单和跨频道去重正确；
- VOT 字段、CDATA、entity、Unicode、时间和两轮幂等正确；
- malformed RSS、timeout、HTTP error、429 只使当前 source 失败；
- 同一 `voice_of_tibet_cn` 的不同文章可进入 religion、politics、international、military；
- “达赖喇嘛”政治新闻不误判宗教；
- “美国涉藏法案”不误判宗教；
- 寺院/法会新闻进入宗教；
- 军事新闻进入军武；
- 新 category 不改变 importance 结果；
- Word 两个栏目存在、与旧栏目同层、动态编号连续、空栏目规则一致、时间倒序、样式一致；
- 官方信源仍在官方区域，官方军事类文章不移动。

## 10. Live 与生产验收

### 10.1 隔离环境

使用临时 validation sources YAML、临时 SQLite 和临时报告目录，只启用 `ltn_defense` 与 `voice_of_tibet_cn`。生产数据库不写入。

连续运行两轮并记录 fetched、fresh、inserted、duplicate、错误和时间范围。LTN 应完成真实幂等验证；VOT 若仍返回 429，则记录：

```text
vot_network_reachable=false
fixture_tests_passed=true
error_isolated=true
other_sources_unaffected=true
production_network_validation_pending=true
```

不得把 429 伪造成 fetched=0 的成功采集，也不得启用 HTML 绕过。

### 10.2 Word 视觉验收

用 fixture 生成真实 DOCX，检查标题、字号、加粗、段距、缩进、行距、编号、来源、时间和链接。若 Live VOT 没有宗教文章，使用 fixture 证明宗教栏目渲染，不伪造 Live 新闻。

### 10.3 全量回归

- 生产配置程序化验证：启用 source 数量为 11；
- `python -m compileall -q app` 通过；
- 完整 pytest：failed=0；
- 生产数据库只读执行 `PRAGMA integrity_check`，结果为 `ok`；
- 不向真实飞书群发送测试消息；
- Windows Task Scheduler 不修改，下一次自然运行读取同一生产 `config/sources.yaml`。

## 11. 完成判定

- LTN 军武正式启用、Live RSS 可解析、默认分类为 military；
- VOT 正式配置为 `/feed/` 并启用，按内容分类且与来源解耦；
- VOT Live 若受 429 阻塞，明确标记网络待验收且不影响其他来源；
- military/religion 的 digest 与 Word 支持通过；
- 动态编号、样式和官方信源保护通过；
- freshness、dedupe、importance 文件和语义不变；
- 隔离幂等、数据库完整性和完整测试通过；
- 未修改飞书正式配置与计划任务。

## 12. 已知风险

- VOT 当前出口 IP 持续收到 HTTP 429，正式网络可用性尚未证明；
- VOT RSS 或站点访问策略可能变化；
- 内容分类是高精度关键词策略，极端标题/摘要表述可能误判，需要后续人工观察；
- LTN/VOT feed 字段或编码未来变化时需要通过 source 健康日志发现；
- 当前工程目录没有 Git 元数据，无法为设计文档创建 commit。
