# 重要度分级 v2 设计文档

- 日期：2026-08-04
- 状态：已获用户口头批准（待用户审阅本文件）
- 目标版本：tw-news-monitor-simple（当前主程序）

## 1. 背景与目标

现有重要度分级采用“关键词规则 + 六维分值加总 + 阈值”的算法，用户反馈无法准确识别其关注的重点新闻。本次改造的目标：

1. 覆盖两类用户关注内容：
   - **A. 选情轨道（election）**：台南市长选情相关新闻（候选人、初选提名徵召、民调、派系整合、蓝白合、组织盘等）；
   - **B. 政经安全轨道（politics_security）**：全台高层政治、两岸、涉外与安全大事（含总统对外表态、邦交互动、台美/台日防卫合作、重大司法案件等）。
2. 每次推送 critical + important 合计 **不超过 5 条**（少而精）。
3. 用户明确关注的重点新闻**必须能进入重点候选**（以回归测试保证）。

## 2. 非目标

- 不使用 LLM（DeepSeek）参与分级（方案二/三留待后续演进）。
- 不修改采集、去重、内容过滤、推送主流程。
- 不把分级结果持久化到 `news.db`（本次只做运行时分级）。
- 不做历史新闻重打分。

## 3. 现状问题（已核实）

1. 规则覆盖缺失：现有 7 条规则只覆盖高层政治/两岸/涉外/海防/边界/非例行外交，没有任何选举/选情规则。
2. 评分机制失效：每条规则六维分值合计约 130～145，critical 阈值 80，导致“命中即 critical”，无法体现轻重梯度。
3. `source_name` 与 `category` 传入后未参与打分。
4. 负面词一刀切：`top_leadership` 的 negative 包含“接见”，导致“总统接见邦交国总理”这类重要新闻被压成 normal。
5. 用户校准案例（必须修复）：
   - 華爾街日報：台美防衛合作趨公開，向中國展現關係穩固；
   - 總統接見史瓦帝尼王國總理，感謝史國對臺灣的堅定支持；
   - 賴清德：台日最大威脅是中國，唯有團結合作才能守護民主；
   - 柯文哲涉京華城案等，二審9/8首開庭。

## 4. 方案总览

规则引擎重构（方案一）：

- 规则结构改为 v2：每条规则声明 `track`、`base_score`、`level_cap`、`boosts`；
- 评分算法改为“基础分 + 加分项”，阈值恢复真实区分能力；
- 新增 `finalize_importance()` 名额分配：critical + important 总量 ≤5，选情轨道保底 ≥1；
- 用户 4 条校准案例固化为回归测试；
- 纯配置 + `app/importance.py` + `app/main.py` 少量改动，离线可测。

## 5. 规则结构 v2

```yaml
rules:
  - id: tainan_election_poll
    track: election                     # election | politics_security
    description: 台南市长选举民调
    base_score: 72                      # 命中基础分（0-100）
    level_cap: important                # normal | important | critical
    subjects: [民调, 支持度, 领先, 落后]
    actions: [公布, 发布, 显示]
    scenes: [台南, 市长选举, 2026]
    negative: []
    boosts:
      - keywords: [民进党初选, 国民党徵召, 蓝白合]
        add: 18
```

字段规则：

- `track`：必填，仅允许 `election` 或 `politics_security`；
- `base_score`：必填，整数 0～100；
- `level_cap`：必填，仅允许 `normal` / `important` / `critical`；
- `subjects` / `actions` / `scenes`：至少一组非空，沿用现有“主体+动作 或 场景”的命中逻辑；
- `negative`：可选，命中任一负面词则整条规则不参与（保持现有行为）；
- `boosts`：可选，命中任一关键词组则加 `add` 分；
- 旧 `dimensions` 字段不再使用，配置校验时拒绝出现（防止混淆）。

## 6. 评分与分级

对每篇文章、每条命中规则：

```
规则得分 = base_score
         + 命中 boost 的 add 之和
         + 官方来源加分（默认 +5，来源名单见配置 scoring.official_sources）
         + 分类加分（默认 politics +3，见配置 scoring.category_bonus）
         + 多规则印证加分（≥2 条不同规则命中时 +5）
最终得分 = min(100, 规则得分)
```

每篇文章取其所有命中规则中的**最高级别**；同级别取最高分。命中规则对应 `track` 记录到结果中，供名额分配使用。

分级阈值（配置可调）：

- critical：score ≥ 85；
- important：85 > score ≥ 65；
- normal：score < 65。

`level_cap` 对级别做上限约束：例如 `level_cap: important` 的规则无论得分多高，最多只能判为 important。

官方来源默认名单（名称匹配，可配置）：總統府、行政院、國防部、外交部、陸委會、中央社。

`ImportanceResult` 保留现有字段（score/level/matched_rules/reasons），新增：

- `track`：该文章主要命中轨道（得分最高的规则所属轨道）；
- `matched_tracks`：命中的轨道集合；
- `capped`：是否为名额分配后降级的候选（默认 False）。

## 7. 名额分配（finalize_importance）

输入：`classify_articles()` 的完整结果 + 规则配置。

算法：

1. 候选集 = level 为 critical 或 important 的文章；
2. 候选集排序：critical 优先 → score 降序 → published_at 降序（沿用现有 `select_highlights` 排序）；
3. 若存在 `track=election` 的候选，强制选中其中排名最高的 `lanes.election.min_slots` 条（默认 1，选情保底）；
4. 从全部候选中按全局排名继续填充，直到选满 `total_cap`（默认 5）或候选耗尽；
5. 未选中的候选：level 降为 normal（保留 score/reasons/matched_rules，`capped=True`）；
6. 返回修正后的完整结果列表。

由此保证：

- 每轮 critical + important 总数 ≤5；
- 有选情候选时，选情轨道至少占 `lanes.election.min_slots` 个名额（默认 1），政经轨道最多占（5 − min_slots）个；
- 无选情候选时，名额全部让给政经轨道；
- Word 简报、飞书卡片、日志统计消费同一份修正后结果，口径一致。

## 8. 配置变更（config/importance_rules.yaml）

```yaml
enabled: true
thresholds:
  critical: 85
  important: 65
  normal: 0
display:
  max_highlights: 5
total_cap: 5
lanes:
  election:
    min_slots: 1
scoring:
  official_source_bonus: 5
  multi_rule_bonus: 5
  category_bonus:
    politics: 3
    economy: 0
    international: 0
  official_sources: [總統府, 行政院, 國防部, 外交部, 陸委會, 中央社]
feishu_highlight_card:
  enabled: true
  title: "本期重点新闻提示"
  show_summary: false
  show_source: false
  show_published_at: false
  use_importance_max_highlights: true
rules: [...]
```

初始规则集（实现时完善关键词明细）：

| id | track | base_score | level_cap | 覆盖内容 |
|---|---|---|---|---|
| tainan_election_candidates | election | 75 | critical | 台南市长候选人动态：表態/參選/登記/提名/徵召/退選 |
| tainan_election_polls | election | 72 | important | 台南市长选举民调、支持度 |
| tainan_election_alliance | election | 70 | important | 蓝白合/在野整合/禮讓/合作（台南） |
| tainan_election_faction | election | 68 | important | 派系/正國會/湧言會/賴系/組織盤/樁腳（台南） |
| top_leadership（改版） | politics_security | 78 | critical | 总统正式讲话/政策/出访；负面词移除“接见” |
| president_diplomatic_meeting（新增） | politics_security | 74 | important | 总统/副总统接见邦交国元首/总理/外长/大使 |
| president_international_statement（新增） | politics_security | 74 | important | 总统对国际局势表态（台日/台美/威胁/守护民主/区域安全） |
| party_power（改版） | politics_security | 72 | important | 政党人事/路线/高层外访 |
| cross_strait_policy（改版） | politics_security | 78 | critical | 大陆涉台法律/惠台措施/台湾官方回应 |
| taiwan_allied_security（原台美关系扩版） | politics_security | 78 | critical | 台美/台日防卫合作、军售、联合声明、安全信号 |
| maritime_security（改版） | politics_security | 76 | critical | 渔船碰撞/海缆受损/敏感水域执法 |
| border_penetration（改版） | politics_security | 80 | critical | 快艇抵岸/人员登陆/防线穿透 |
| irregular_diplomacy（改版） | politics_security | 76 | critical | 非例行出访/秘密过境 |
| judicial_political_cases（新增） | politics_security | 70 | important | 政治人物涉司法案件（偵辦/起訴/一審/二審/羈押/判決） |
| legislative_candidacy_rules（新增） | politics_security | 70 | important | 影响参选资格的重大立法（三讀/不得參選/排黑條款） |
| local_election_moves（新增） | politics_security | 68 | important | 全国县市长选情动态（競總/派系插旗/大團結/藍白合/提名徵召） |

移除配置中未使用的 `confidence_levels` 字段。

## 9. 代码变更

### app/importance.py

- `score_article()`：实现 v2 评分（base_score + boosts + 官方来源 + 多规则印证），应用 level_cap，记录 track；
- `classify_articles()`：签名保持兼容，透传规则配置；
- 新增 `finalize_importance(importance_results, rules_config)`：实现第 7 节名额分配；
- `validate_rules_config()`：改为校验 v2 结构（track/base_score/level_cap/boosts/阈值/display）；
- `select_highlights()`：保持现有行为，消费 finalize 后的结果（无需大改）。

### app/main.py

- 加载规则后立即调用 `validate_rules_config()`，结构错误则打印原因并退出（与 sources 配置校验一致）；
- `classify_articles()` 之后调用 `finalize_importance()`，并记录分配前后数量到日志；
- 其余逻辑（Word、飞书卡片、digest）不改。

### app/word_digest.py / app/feishu.py

不改。消费的就是 finalize 后的结果。

## 10. 测试计划

更新 `tests/test_importance.py`，并新增校准用例：

1. 用户 4 条校准案例 → 断言 level ∈ {important, critical}；
2. “接见”区分测试：接见邦交国总理 → 重点；接见地方团体 → normal；
3. 名额测试：构造 10 条候选 → 最终 critical+important ≤5；有选情候选时选情 ≥1；无选情候选时政经可占满 5 个；
4. level_cap 测试：`level_cap: important` 的规则即使得分 ≥85 也不得判为 critical；
5. boost 测试：命中 boost 后分数与级别按预期变化；
6. 官方来源加分测试；
7. 配置校验测试：非法 track、超范围 base_score、非法 level_cap、缺失字段均报错；
8. 既有测试迁移到 v2 样例配置后全部通过。

校准用例的定位：保证“用户关注的新闻至少能进入重点候选”，不代表每轮实际必进前 5（真实批次由名额算法决定）。

## 11. 验证与上线

1. 运行 `python -m pytest tests/test_importance.py -v` 全绿；
2. 运行完整测试 `python -m pytest -q`，确认无回归；
3. 运行 `python -m app.main --dry-run`，观察日志中的 importance summary（critical + important ≤5）与 reasons；
4. 观察至少 2～3 个真实推送周期，确认卡片与 Word 标记符合预期；
5. 需要时继续补充关键词，规则改动只需改 YAML + 跑测试。

注：当前 Codex 沙箱 PATH 中未找到 Python，测试需由用户提供 Python 路径执行，或由用户在本机命令行执行上述命令。

## 12. 风险与缓解

- 关键词规则对新话题不敏感：通过校准用例 + 定期补词缓解；后续可叠加 LLM 精排（方案三）。
- 名额算法可能把高分选情新闻压掉：选情保底 1 个名额已保证最低覆盖；若实际运行中选情重要新闻经常被挤出，可将 `lanes.election.min_slots` 调至 2。
- 阈值/基础分需要微调：全部集中在 YAML，改后跑测试即可，不需要改代码。
