# 军武事件 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.
> 本次按用户明确要求在当前会话连续实施，不等待分阶段批准；不另建任务。

**Goal:** 将现有 news.db 中涉台军武报道识别、聚类并生成可解释的事件简报。

**Architecture:** 保留原 military 来源过滤接口，将同名模块转为包。只读 SQLite，复用 Article、繁简转换和时区，确定性特征匹配与保守 complete-link 聚类；原 Word 栏目可选事件模式，默认关闭。

**Tech Stack:** Python 标准库、现有 PyYAML / python-docx / pytest；不增加项目依赖。

**Spec:** 用户附件 `C:/Users/User/.codex/attachments/b893e1eb-be84-4687-8da2-8cfe0764a4de/pasted-text.txt`。

## Global Constraints

- 只修改当前主目录，不使用附件旧路径。
- 不改采集器、计划任务、飞书、原 importance，不调用外部 LLM，不迁移或写入数据库。
- 事件模式默认关闭；CLI 独立调用，Word 出错退回原栏目。
- 美国相关报道保留事实及来源、importance=null，不做政策评分或排名。
- 原代码有未跟踪数字文件；不覆盖、不删除。

## 审计（已完成）

- Article 无数据库 ID、无正文；CLI 通过 URL→ID 映射保留真实 ID；Word 内存调用以 URL 为稳定键。
- Database.connect 会迁移，故 CLI 单独 sqlite3 mode=ro + query_only。
- app/military.py 是来源过滤工具；原公共 API 保留于 military/__init__.py，仅调整默认配置根路径。
- word_digest 已有军武栏，位于九合一之后、普通新闻之前，沿用动态编号与重点提示。
- 没有全局中文媒体质量排名；复用 is_official_source，并配置少量媒体别名和代表报道优先级。
- 系统默认 Python 无法启动；用 bundled Python，依赖隔离于 validation/military_mvp/deps。

## Task 1: 配置、识别与特征（已完成）

Files: `app/military/__init__.py`, `config.py`, `classifier.py`, `config/military_rules.yaml`; tests: `tests/test_military_events.py`。

- [x] 审计生产结构、模型、时区、Word 和旧接口。
- [x] 测试正负及七类样本：`assert classify(article('空军测试F16V新型导弹'), rules).category == 'taiwan_equipment'`。
- [x] 实现 `load_rules(path=None) -> dict`, `classify(article, rules) -> Features`；规范化繁简、型号、媒体前缀；实体＋动作＋台湾上下文，标题负向抑制，摘要仅在标题有军事实体时辅助。
- [x] 执行 `python -m pytest tests/test_military_events.py -q`，验证规则与别名覆盖。

## Task 2: 聚类与事件（已完成）

Files: `app/military/clustering.py`, `events.py`; tests: `tests/test_military_events.py`。

- [x] 用三组边界断言定义契约：F16V试验同义合并；试验/军售拆分；海鲲测试/预算拆分。
- [x] 实现 `compare(a, b, rules) -> dict`，输出实体、动作、地点、token、时间与分数；动作/地点/具体型号冲突拒绝，时间上限与距离惩罚，禁止只按机构合并。
- [x] 实现 `build_events(articles, rules, article_ids=None) -> list[dict]`；确定性排序，候选与簇内每个成员均须满足门槛，阻止链式误合并；哈希成员键生成批次稳定 ID。
- [x] 事件评分按维度取一次加分；代表报道按来源、信息量、摘要、时间综合选择；来源别名去重；输出全部成员和解释。
- [x] 执行新增测试，验证跨日、过期、输入顺序不变和重要度排序。

## Task 3: CLI 与 Word（已完成）

Files: `app/military/cli.py`, `output.py`; modify `app/word_digest.py`; tests: `tests/test_military_events_output.py`。

- [x] 真实 schema 临时库测试 `read_articles(db, hours, now)`；发布时间优先，缺失回退抓取时间；朴素时间按台北解释，拒绝未来数据。
- [x] CLI 支持 `--hours --db --config --format json|markdown --output --now --word-dir`，只读连接和显式错误退出；输出文件不得覆盖输入数据库或配置。
- [x] Word 新参数 `military_event_config` 默认 None；显式字典或配置 `word_enabled: true` 启用；识别成功成员从普通栏目摘出，未识别旧专题稿继续旧显示，避免丢稿；异常记录并回退。
- [x] 实测 Word：事件标题、类别、重要度、代表来源、为何重要、篇数、来源超链接；无调试字段。

## Task 4: 验收（已完成）

- [x] 新增至少 25–40 项有意义测试，执行新旧军武/Word/选举专项。
- [x] 全部 `tests` 回归，区分环境失败和代码失败；任何未通过门禁不报 PASS。初始基线因 Python 环境未能启动，不声称拥有改动前完整基线。
- [x] 真库只读 CLI 生成 JSON/Markdown/Word，验证 schema/quick_check 和样本；仅公开已存新闻，不联网核实事实。
- [x] 记录命令、测试总数、至少三组结果，输出 `docs/MILITARY_EVENTS_MVP_ACCEPTANCE.md` 和运行说明。

### 验证进展（2026-09-05 10:02）

- 新增用例 103 项；最终全量 3216 项：3207 通过、5 项计划任务权限失败、4 跳过。
- 相同 5 项已以 DryRun/XML 导出在授权环境全部通过；无脚本改动。
- 真实库只读 3412 篇→57 篇军武→50 事件，7 个多报道簇；schema 不变，quick_check=ok。
- WPS 初次导出两页 Word 样本；最终样例调整后已导出并目视检查一页 A4，排版通过。标准 LibreOffice 渲染器因本机无 soffice 改用 WPS。

### 最终核对

- [x] 核心实现、103 个新增测试、3113 个原有测试均已完成验证。
- [x] 合并权限复测结果：3212 通过、0 未解决失败、4 原有环境缺失跳过；最后影响范围复测 154 通过。
- [x] 最新真库回放：3434 篇→57 篇→50 事件，7 个多报道簇；14681 条记录前后不变，schema/quick_check 通过。
- [x] 验收报告与运行命令已写入 docs/MILITARY_EVENTS_MVP_ACCEPTANCE.md。
- [x] Word 默认关闭，采集/推送/计划任务/数据库 schema 均未修改，未继续开发后续建议。
