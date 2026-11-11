# 台湾军武新闻事件 MVP 验收报告

验收日期：2026-09-05。主目录：`D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple`。

## 1. 最终结论

**PASS：工程实现、业务测试、兼容性回归和样本排版验收通过。**

军武识别、七类分类、事件聚类、事件重要度、代表报道选择、JSON/Markdown CLI 和现有 Word 栏目接入均已实现。Word 事件模式保持默认关闭，独立 CLI 可直接使用。

明确的业务边界：**美国相关报道仅展示事实及来源，`importance=null`、`level=factual`，按时间排列，不进行政策评分或排名。** 其余适用事件输出 0–100 分。因此不能把本版本理解为“所有条目都一定有数值分数”。

没有新增采集器、网络抓取、外部 LLM 调用、数据库表或迁移，也没有修改计划任务、飞书机制、原新闻重要度规则。没有改动归档项目目录。

## 2. 架构

```text
news.db 的 articles 表（SQLite mode=ro + query_only）
→ Article + URL 对应的原始数据库 ID
→ 标题为主的 military classifier
→ 七类主类别与结构化特征
→ 时间、实体、动作、地点和标题相似度共同聚类
→ 事件重要度与来源去重
→ 代表报道和规范化事件标题
→ JSON / Markdown / 现有 Word 军武动态栏目
```

独立 CLI 使用发布时间筛选最近 N 小时；缺失发布时间时回退到抓取时间。无时区时间按 Asia/Taipei 解释，未来新闻不进入窗口，非法时间记录跳过并写日志。没有调用会触发迁移的 `Database.connect()` 读取生产库。

Word 使用传入的同一批 Article，在内存中运行同一引擎；不写回数据库专题，不改变原入库、重点提示和通知判定。

## 3. 新增文件

| 文件 | 职责 |
|---|---|
| `app/military/config.py` | 加载并校验规则、分类、词表、评分和聚类参数；CLI 对错误配置明确报错 |
| `app/military/classifier.py` | 标题规范化、繁简和型号归一、上下文识别、七类分类及特征提取 |
| `app/military/clustering.py` | 可解释的两两比较、时间窗口和完整成员约束聚类 |
| `app/military/events.py` | 事件对象、代表报道、媒体别名去重、分数和原因 |
| `app/military/output.py` | Markdown 和 Word 事件渲染，不向 Word 写调试字段 |
| `app/military/cli.py` | 只读真库、时间筛选、JSON/Markdown 输出和可选 Word 导出 |
| `config/military_rules.yaml` | 实体、动作、范围、负向上下文、类别、媒体和权重等业务配置 |
| `tests/test_military_events.py` | 正负样本、类别、歧义、聚类、分数、代表报道和配置测试 |
| `tests/test_military_events_output.py` | 真实 schema 临时库、CLI 子进程、时间和 Word/Markdown 集成测试 |
| `docs/superpowers/plans/2026-09-05-military-events.md` | 审计、实施和验收进度记录 |
| `docs/MILITARY_EVENTS_MVP_ACCEPTANCE.md` | 本报告及运行说明 |

验证日志、临时数据库、隔离测试依赖和样本位于已被 Git 忽略的 `validation/military_mvp/`，不进入生产依赖或调度。

## 4. 修改文件

| 文件 | 修改目的 |
|---|---|
| `app/military.py` → `app/military/__init__.py` | 转为同名包，支持 `python -m app.military.cli`；全部旧过滤 API 保留，仅将默认配置路径调整到新的包层级 |
| `app/word_digest.py` | 增加可选 `military_event_config`，在原军武栏目渲染事件；默认关闭和异常回退保持旧行为 |

原过滤模块已与 Git HEAD 内容比较：除默认配置根路径外完全一致。实际生产修改只涉及原 military 模块迁移和 Word 文件。最终包共 7 个 Python 文件（包括迁入的旧模块），669 行；规则 1 个文件，测试 2 个文件，没有新增项目依赖。

## 5. 核心算法

**识别。** 使用军事组织、具体型号、装备类别和命名演习等实体，加上军事动作和涉台上下文共同判定。明确的对台军售等强信号可作为实体不足时的补充证据。游戏、农业、影视、股票、历史回顾等标题上下文抑制误报；卫星、无人机、雷达等军民两用技术需要额外军事上下文。摘要仅辅助本身有军事实体的标题，不能靠摘要顺带提到台湾把海外新闻拉入。

**分类。** 七类中文名称、动作映射集中在配置中。通过标题主要行动方区分台军装备与解放军活动；不会因为台军装备报道末尾提到共军就直接归入解放军类。预算讨论和非演习的修法预告不会归为演训。

**聚类。** 标题做 NFKC、媒体前缀清理、繁简和 F16V 型号归一；使用中文双字 token、拉丁型号 token 的 Jaccard 与 SequenceMatcher 相似度。实体、动作、地点、时间共同贡献比较分数。默认窗口 72 小时，时间越远扣分越多，缺少发布时间时缩至 12 小时。

防止误聚类的拒绝条件包括：类别不同、动作不同、肯定/否定状态不同、具体地点冲突、型号或已知飞弹型号冲突、命名事件冲突、时间超窗、标题证据不足。同一机构本身不能成为合并理由；无具体型号时，必须有更强标题与动作证据。逐日台海通报跨日期拆分，汉光等命名演习允许跨日合并。

一条候选新闻要与簇内**每一个**成员都满足合并条件，阻止“A 像 B、B 像 C”造成 A/C 被间接误合并。比较函数返回 `matched_entities`、`matched_actions`、`matched_places`、`title_similarity`、`token_jaccard`、`time_distance`、`cluster_score` 和拒绝原因；JSON 保存已合并成员的解释。

**重要度。** 按事件类别基准、关键装备、首次、实弹、规模、突发、正式交付及不同主流媒体报道等维度计分，每个维度只加一次。修饰词必须与军事动作、实体同处一个标题句段；否定表达不获得对应加分。媒体别名归一后计数，多来源不等于已独立核实。适用事件分为重大（80–100）、重要（60–79）、一般（40–59）和低值（0–39）；美国相关条目另作事实记录。

**代表报道。** 综合配置中的媒体优先级、现有官方来源标识、标题信息量、核心实体和动作、已有摘要长度与发布时间。不会机械选最早报道，也不会下载正文。事件标题仅对代表标题作少量规范化，不生成新事实。

事件保留代表新闻、全部原始 ID/URL、来源、成员数、首末报道时间、实体、分数理由和聚类解释。相同输入顺序变化不会影响结果；同 URL 重复输入只保留一篇。

## 6. 测试结果

| 测试范围 | 数量 | 最终通过 | 未解决失败 | 跳过 |
|---|---:|---:|---:|---:|
| 新增业务及集成测试 | 103 | 103 | 0 | 0 |
| 原有测试 | 3,113 | 3,109 | 0 | 4 |
| 总计 | **3,216** | **3,212** | **0** | **4** |

执行过程和计数口径：

- 全量 `tests` 原始结果为 **3,207 passed / 5 failed / 4 skipped**。5 个失败全部来自 Windows 沙箱对原计划任务服务的访问限制。
- 审核对应代码后，使用相同测试的 `DryRun`/XML 导出路径解除沙箱限制重跑：**5 passed**。没有注册、删除或修改任何真实计划任务，未修改相关脚本或测试。
- 最后调整事件分页保持规则后，重跑全部新增用例及相关旧军武、Word、选举 Word 测试：**154 passed**。该复测不重复计入上表。
- 4 项原有跳过：1 项缺少历史 diagnosis 样本，3 项缺少 dev DB；不是本次新增的跳过。
- 初次运行的深目录复制失败已通过缩短临时目录解决；OAuth 的临时凭据路径测试改用项目外临时目录后通过。没有为这些环境问题改业务代码。
- 沙箱默认 Python 无法启动，因此本次使用 Codex 自带 Python，并在 `validation/military_mvp/deps` 安装项目已声明依赖。没有修改系统 Python 或 requirements.txt。初始改动前完整基线未成功启动，不声称拥有该基线。

证据：`validation/military_mvp/final.xml`、`scheduler.xml`、`final-targeted.xml` 及同名日志。`git diff --check` 通过。

## 7. 实际运行样例

**真实数据库：** 2026-09-05 10:33:05（Asia/Taipei）回放最近 72 小时，读取 **3,434 篇**，识别 **57 篇**，形成 **50 个事件**，其中 **7 个多报道事件簇**。运行前后 articles 均为 **14,681 条**，schema 不变，SQLite `quick_check=ok`。较早一次回放读取 3,412 篇；期间原计划任务继续正常入库，计数变化未被当成数据损坏。

文件：`validation/military_mvp/real-events.json`、`real-events.md`、`evidence.json`。这些是已有数据库内容的处理结果，没有联网复核新闻真实性，也没有据此声称线上准确率达到某个百分比。

以下为实际执行过的**合成 fixture**，仅验证算法，不代表新闻真的发生：

| 事件标题 | 类别 | 分数 | 相关新闻 |
|---|---|---:|---:|
| 海鲲号潜舰进行潜航测试 | 台军装备 | 52 | 1 篇 |
| F-16V进行新型飞弹挂载测试 | 台军装备 | 52 | 2 篇 |
| 汉光演习今日展开 | 台军演训 | 40 | 1 篇 |

F-16V 事件实际合并了“F-16V进行新型飞弹挂载测试”与“空军测试F16V新型导弹”。海鲲预算质疑不会与潜航测试合并；F-16 零件军售不会与飞弹试验合并。

常规运行（在项目根目录，使用已配置好的项目 Python）：

```powershell
python -m app.military.cli --hours 24
python -m app.military.cli --hours 24 --format markdown --output data/military/events.md
python -m app.military.cli --hours 24 --output data/military/events.json --word-dir data/military/word
```

离线回放可指定 `--now 2026-09-05T09:00:00+08:00`，并通过 `--db`、`--config` 指定输入。CLI 不指定输出文件时写到终端；非法参数、缺失数据库、无效规则返回退出码 2。输出路径不能覆盖数据库、规则或 Word 文件。

本会话可复用的验证环境命令：

```powershell
$env:PYTHONPATH = (Resolve-Path 'validation/military_mvp/deps').Path
$env:PYTHONUTF8 = '1'
$militaryPython = 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $militaryPython -m app.military.cli --hours 24 --format markdown
```

## 8. Word 接入结果

复用原“军武动态”一级栏目，位于九合一之后、普通政治/经济/国际栏目之前；保持现有字体和动态编号。事件显示标题、中文类别、重要度、代表来源、为何重要、相关新闻数量和可点击的代表报道链接，不显示相似度等调试参数。

识别为事件的成员不再逐条重复出现在普通栏目。旧 military topic 中未被新引擎识别的稿件继续按原方式显示，避免接入时丢稿。重点提示继续使用原 importance 结果，相关专题的既有行为保留。

启用方式：将 `config/military_rules.yaml` 的 `word_enabled` 改为 `true`，现有 Word 调用即使用事件模式；也可以由调用方显式传入 `military_event_config`，或通过 `MILITARY_RULES_PATH` 指定规则文件。本次交付配置仍为 **false**。独立 CLI 的 `--word-dir` 会仅在本次导出调用中启用事件模式，不修改配置。

验收样本 `validation/military_mvp/military-fixture.docx` 明确标注为合成数据，包含三条事件、四篇成员报道。使用本机 WPS 只读导出 PDF 后逐页检查，最终为 **1 页 A4**，文字、字段、事件块和三个超链接完整，无裁切或跨页断裂。标准 `render_docx.py` 因本机没有 LibreOffice/soffice 未成功，已使用真实 WPS 排版结果完成替代验证；不是仅靠 XML 判断排版。

## 9. 已知限制

- 确定性中文词表无法覆盖全部新型号、地点和表达；短标题、复合主题仍可能漏识别或保守拆簇。不存在全量人工标注真库，因此不报告未经验证的准确率。
- 事件只在当前输入批次内聚类，不持久化跨批次事件状态；成员变化会改变事件 ID。72 小时窗口外内容不合并，缺时间条目采用更短窗口。
- 没有正文，摘要长度只是代表报道完整度的近似；多媒体报道也可能来自同一上游来源，不能视作独立事实核验。
- CLI 为正确处理混合时区逐行解析 articles 时间；当前真库规模已验证，尚未对大规模历史库做性能承诺。Word 导出范围取决于调用方传入的新闻集合。

## 10. 后续建议

1. 按实际误报、漏报和误聚类样本持续维护词表与回归 fixture。
2. 建立少量人工标注的真实跨媒体事件样本，评估识别和聚类质量后再调整阈值。
3. 仅在数据量明显增长时评估时间查询性能和可选的事件审阅工具。

以上建议未在本轮扩展开发。
