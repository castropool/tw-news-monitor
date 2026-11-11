# Word 简版“军武动态”设计

## 目标与范围

在现有台湾新闻监测主链路中加入五个军武专业来源，复用既有文章模型、URL/身份去重、SQLite 和 Word 生成能力。新增流程为：专业频道采集、去重、军武轻量噪声过滤、`military` 专题写入、Word“军武动态”栏目输出。

本轮只接入自由军武、联合新闻网军事、NOWnews 军武战情、青年日报、国防部军事新闻通讯社。不接入其他媒体、影音、知识图谱、武器实体识别、风险评分、态势研判或军武子栏目。

## 总体方案

采用独立专题层，不给 `Article` 增加 topics 字段，也不从全量政治新闻中反向识别军武内容。来源配置明确标记军武专业频道和来源类型，采集器继续返回普通 `Article`。主流程仅对这些来源执行军武过滤，并以 URL 为键写入 `news_topics`。

数据流如下：

```text
军武专业来源采集
→ 运行内 URL/身份去重
→ military 轻量噪声过滤
→ articles 正常入库或确认历史已存在
→ news_topics 写入 topic=military
→ Word 按专题集合路由
```

专题路由与普通 `category` 分离。同一 URL 可以同时拥有 `election_2026_local` 和 `military`，而普通政治、经济、国际分类仍保持现有含义。

## 五个来源

### 自由时报“自由军武”

- 来源类型：`commercial_military`
- 入口：`https://def.ltn.com.tw/breakingnewslist`
- 方式：新增 HTTP + BeautifulSoup 列表采集器。
- 只读取列表页中的标题、文章 URL 与时间，不访问正文。
- 文章链接限定为 `def.ltn.com.tw/article/breakingnews/<id>`。
- 列表中的仅时分日期以台北当前日期补齐；完整日期按页面值解析。

### 联合新闻网“军事”

- 来源类型：`commercial_military`
- 入口：`https://udn.com/news/cate/2/6638sub_122173`
- 方式：直接复用现有 `UDNCollector`，仅新增来源配置。
- 不复制或分叉 UDN 解析逻辑。
- 继续使用现有 UDN URL 身份去重规则处理追踪参数和别名。

### NOWnews“军武战情”

- 来源类型：`commercial_military`
- 入口：`https://www.nownews.com/cat/news-summary/military/`
- 方式：新增 HTTP + BeautifulSoup 分页采集器。
- 首屏解析 `ul#ulNewsList li.item`；后续页使用 `/page/{n}/`。
- 默认 `max_pages=3`、`stop_after_hours=72`，并遵守每个来源最多 20 条的全局约束。
- 页面按时间顺序扫描；遇到早于截止时间的条目后停止当前页并不再请求下一页。置顶内容不参与提前停止判断。
- 标题、URL、`time[datetime]` 均来自列表页，不访问正文。

### 青年日报

- 来源类型：`official_military`
- 方式：复用现有 `RSSCollector` 和 feedparser。
- 首批 Feed：
  - 国防焦点：`https://www.ydn.com.tw/tw/Home/RSS.aspx?CID=9`
  - 武备巡礼：`https://www.ydn.com.tw/tw/Home/RSS.aspx?CID=11`
  - 军视界：`https://www.ydn.com.tw/tw/Home/RSS.aspx?CID=26`
- 三个 Feed 作为同一核心来源的三个配置入口，运行内继续按规范化 URL 合并。
- Feed 失败彼此隔离；核心来源验收以至少一个 Feed 成功且三个配置入口均被实际探测为基础。

### 国防部军事新闻通讯社

- 来源类型：`official_military`
- 稳定入口：`https://mna.mnd.gov.tw/news/overview/`
- 方式：新增 HTTP + BeautifulSoup 列表采集器。
- 旧 `mna.gpwb.gov.tw` 不进入生产配置。
- 文章链接限定为 `/news/detail/?UserKey=<uuid>`。
- 公共日期函数解析“民国115年09月04日”等形式，转换规则为西元年 = 民国年 + 1911。
- 只读取列表页元数据，不访问正文。

五源均使用现有超时、User-Agent、重试和单源故障隔离边界，不使用 Playwright 或 Selenium。

## 军武轻量过滤

新增 `config/military.yaml` 与纯函数过滤模块。模块只接受已由来源配置认定为军武候选的文章，不扫描其他来源。

判定顺序：

1. 合并标题与已有 RSS 摘要，不抓正文。
2. 命中任一保护词时保留。
3. 未命中保护词且命中任一明确噪声短语时删除。
4. 其他内容默认保留。

保护词覆盖军购、军售、军演、共军、军机、军舰、飞弹、战机、潜舰、无人机、雷达、防空、战车、火炮、国防预算、军事投资、国防产业、战备、兵推、汉光演习、军事科技等。噪声短语覆盖军人节优惠、敬军餐会、军眷活动、摄影比赛、文艺活动、运动会、慰问活动、官兵家庭日等。

保护词优先可保证“视导飞弹战备”即使同时含活动性措辞仍被保留；“单纯慰问官兵”没有保护词时删除。目标是宁可保留少量边界噪声，不误删真实重大军情。

## 数据库设计与迁移

`news_topics` 从 `url TEXT NOT NULL UNIQUE` 迁移为表级 `UNIQUE(url, topic)`，并增加可空的 `source_type` 字段，合法军武值为 `commercial_military` 或 `official_military`。

迁移在 `Database.connect()` 中幂等执行：

1. 检查表是否存在、列集合和唯一索引形态。
2. 已是目标结构时只补缺失索引，不重建。
3. 旧结构在单个事务中创建新表。
4. 按原 ID 复制全部历史行，旧 election 行的 `source_type` 为 NULL。
5. 原表改名/替换后重建 URL 和 topic 索引。
6. 事务成功后提交，失败则回滚并保留旧表。

新增通用专题 upsert/read 接口，以 `(url, topic)` 为冲突键。现有 `save_election_topic`、`get_election_topic`、`get_election_topics_by_urls` 保留签名，并显式限定 `topic='election_2026_local'`，避免同 URL 的 military 行改变旧调用结果。

军武专题写入不依赖文章是否本轮新插入：只要 URL 来自已通过过滤的军武专业频道，文章已存在时也补写 `military` topic。重复运行更新同一 `(url, military)` 行，不产生重复记录。

## Word 输出

保留现有字体、颜色、标题、摘要、来源、时间、补发标记与超链接样式，只调整栏目路由和层级。

动态一级栏目顺序：

1. 官方信源（现有非军武官方稿，有内容才显示）
2. 重点提示（现有 importance 结果为 `important` 或 `critical`，有内容才显示）
3. 九合一选举（有内容才显示，县市分组继续作为二级栏目）
4. 军武动态（有内容才显示，不设子栏目）
5. 政治新闻
6. 经济新闻
7. 国际及两岸新闻
8. 其他现有非空栏目
9. 国际媒体（启用且有内容时）

所有一级栏目使用同一个动态编号函数，空栏目不占号。“新闻媒体”容器移除，原分类小节提升为一级栏目。

路由规则：

- 青年日报与军闻社的军武稿只进入“军武动态”，不再进入“官方信源”。
- 普通军武稿不进入政治或国际栏目。
- `important`/`critical` 军武稿进入“重点提示”和“军武动态”。
- 同时属于九合一与军武的文章可以进入两个专题栏目，但不进入普通政治或国际栏目。
- 重点提示复用 importance 结果，不改 importance 打分、阈值或飞书卡片逻辑。
- 未传 military 专题集合时保持向后兼容，不凭标题自行识别。

生产主流程传入本轮预计算或数据库回读的 military URL 集合；历史 `--export-word` 路径从 `news_topics` 回读同一集合，保证结果一致。

## 错误处理

- 单个军武来源网络异常、非 2xx 或结构异常只标记该来源失败，其他来源继续。
- HTML 页面存在但找不到任何合法条目时记录 schema failure，而不是伪装成成功空源。
- military 配置缺失或无效时 fail closed：禁用专题过滤和标记，不影响原有主链路。
- 数据库专题写入为 best effort；异常记录日志并保留文章入库结果。
- 生产冒烟强制 `DISABLE_FEISHU_SEND=1`，不发送飞书测试消息。

## 测试与验收

所有新增单元测试使用本地 HTML/RSS fixture，不依赖实时网站。

采集器测试覆盖：自由军武、UDN 军事配置与解析、NOWnews 多页/旧闻停止、青年日报三个 RSS、军闻社、民国纪年、网络异常、空条目与 HTML 结构异常。

过滤测试覆盖指定五个例子，并使用至少 50 个明确军武正样本和至少 20 个明确噪声样本计算指标：正样本保留率不低于 98%，噪声过滤准确率不低于 95%。

数据库测试覆盖 military 写入、重复写入幂等、同 URL 双 topic、旧库迁移不丢 election 数据、重复迁移无变化。

Word 测试覆盖空栏隐藏、有军武显示、九合一与军武编号连续、重大军武双显、普通军武不进政治/国际、交叉专题、多种空栏组合。

验证顺序：新增专项、数据库专项、Word 专项、九合一专项、全部旧测试、五源真实采集、禁发生产冒烟。任一门禁失败则最终结论为 `FINAL = FAIL`；全部通过才为 `FINAL = PASS`。

## 已知边界

- 专业频道仍可能出现军纪、人事、教育或军民活动，V1 只过滤明确噪声。
- 标题与摘要同时缺少保护词时，含噪声短语的真实军情可能被删除；保护词表通过离线样本持续维护。
- NOWnews、自由军武和军闻社属于 HTML 契约，网站改版会触发 schema failure，需要更新 fixture 与选择器。
- 青年日报三个 Feed 的更新频率不同，单次可能没有新稿；真实采集门禁以请求成功、Feed 结构有效和可解析条目为准。
- 同一事件不同 URL 不做标题相似去重，继续遵守本项目 V1 的 URL/身份去重边界。
