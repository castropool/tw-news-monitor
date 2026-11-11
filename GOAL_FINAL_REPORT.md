# GOAL_FINAL_REPORT — 台南选情自动研判系统 V1 完成报告

生产模式：`research_driven`（事实层严格，分析层开放）
完成日期：2026-08-15
最终验收周期：2026-07-16 至 2026-07-31

---

## 1. 最终产品现在怎么工作？

```text
新闻持续采集（既有）
  → 候选事实自动发现（既有）
  → 人工审核 Candidate + complete_review（既有，facts_cutoff）
  → 每月 9 日 / 22 日 09:00 Asia/Taipei 自动触发研判（Tainan Election Assessment）
  → Period Gate（facts_cutoff >= period_end，否则 REPORT_PERIOD_NOT_READY）
  → 自动构建 Assessment Research Pack（只读正式事实）
  → 一次 LLM 调用：变化识别 → 候选判断 → 选主判断 → 因果链 → 权力关系
    → 政治意图 → 趋势推演 → 最终研判文章（analysis_plan 审计 JSON + 文章）
  → Fact Safety Check（严重问题才 HARD_BLOCK，轻微问题只记 review_note）
  → Word 自动生成 → ready_for_review（人工终审入口保留）
```

用户日常只需要：平时不管；有空审核候选事实；9/22 日前完成事实审核；
9/22 日打开 `FINAL_ASSESSMENT_PREVIEW.docx` 阅读。

## 2. 本次修改了什么？

- 新增生产包 `app/assessment/research_driven/`：
  - `research_pack.py` 研究包构建器（周期/本期事件/历史背景/上一期状态/人物阵营/
    民调/治理议题/来源/证据限制，JSON + 独立可读 Markdown）
  - `prompt.py` 重设计研判 Prompt V3（ROLE/TASK/FACT BOUNDARY/ANALYTICAL METHOD/
    ARTICLE STRUCTURE/WRITING STYLE/FACT VS ASSESSMENT/TREND ANALYSIS/
    FORBIDDEN BEHAVIOR/OUTPUT FORMAT）
  - `adapter.py` Assessment LLM Adapter（provider/model/temperature/max_tokens 配置化）
  - `fact_safety.py` 事实安全检查（未来泄漏/虚构民调/人物/日期/数字/标题/章节/旧民调）
  - `word_renderer.py` 文章 Word 渲染（正文即文章，无内部 ID）
  - `generation.py` 生产编排（Period Gate → Pack → LLM → 检查 → Word → 终审）
  - `scheduled.py` / `review.py` / `status.py` 运营 CLI
- 配置 `config/election_assessment.yaml`：`assessment_generation_mode: research_driven`
  + `llm.research_driven`（provider/model/temperature/max_output_tokens 等）；
  旧模式列入 `legacy_assessment_generation_modes`。
- 调度脚本 `scripts/run_assessment_scheduled.ps1` 切换到新入口（计划任务本体不变）。
- `ASSESSMENT_OPERATOR_GUIDE.md` 重写为日常运营手册；`AGENTS.md` 更新生产契约。
- 新增 43 个测试（`tests/assessment/research_driven/`），全量 pytest 0 failed。

## 3. 旧 Claim-centric 生成方式如何处理？

保留不删除、标记 legacy、不继续开发、不进入生产：
`generate_llm_report` / `claim_*` / `report_structure_validator` /
`r2/generation.py` / `two_stage_*` 全部留在原地，旧 run 数据可读可展示；
生产 Scheduler 只走 research_driven 新路径。旧 Claim 体系仅作为后台审计/
调试/历史兼容用途。

## 4. Research Pack 如何生成？

`research_pack.py` 复用 evidence_pack_builder 的只读正式数据层
（load_formal_data + 周期提取 + 背景选择 + 民调读取），从冻结的正式输入
（db + seed 副本）构建，不调用模型、不写正式库。内容包括：
周期与 facts_cutoff/poll_cutoff、本期正式事件（含 actor 规范名、来源与日期）、
历史背景事件、上一期状态基线（快照差异）、上一期正式报告（存在时）、
人物与阵营分组、民调（含前次变化、空窗声明）、治理议题、来源、证据限制、
禁止推断项。同时产出 ASSESSMENT_RESEARCH_PACK.md——不运行内置 LLM、
直接上传 ChatGPT 也足以写出完整研判（人工 Fallback 硬验收项）。

## 5. 系统如何识别“本期新变化”？

三层：机器确定性部分（本期事件按阵营/议题分组、快照状态差异
changed_dimensions、风险变化）先写进研究包；模型在分析层完成 Change
Analysis——与上一期/基线相比的实质变化、变化类型（组织/派系/战略/蓝白/
中央—地方等）、变化方向标签（new/strengthened/weakened/unchanged/uncertain）、
并区分“新闻动作”与“结构性信号”，排序为 3—6 个关键变化写入 analysis_plan。

## 6. 系统如何形成核心判断？

模型先提出 3—5 个候选判断（每个含：核心判断/支持事实/反证与限制/为什么
重要/未来含义），再选择一个最能解释本期变化的主判断（primary_thesis，
含证据强度 HIGH/MEDIUM/LOW 与支持事件 ID）作为全文中心；文章第一节
“核心判断”即该主判断的展开。验收期文章的主判断见 BUSINESS_ACCEPTANCE.md。

## 7. 系统如何形成因果链？

Prompt 要求至少 2 层、按证据决定到 4 层（直接政治原因 → 候选人/派系权力
意图 → 地方政治结构 → 选举制度或长期趋势）；验收期文章形成完整四层，
写入 analysis_plan.causal_chain，正文第三节展开。

## 8. 系统如何分析权力关系？

Prompt 明确要求围绕组织权、提名权、人事权、资源控制、中央支持、议会系统、
地方桩脚、竞选团队、政党机器、跨党合作分析，而非只写支持率与曝光；
验收期文章分析了陈亭妃—赖系/挺宪派（表面团结实质保留）、谢龙介—国民党
地方组织（骨架已成指挥体系未公开）、蓝白合作（全国框架+选区配置但全市
未制度化），并逐项判断“谁获益、谁受约束”。

## 9. 系统如何做趋势预测？

趋势推演分短期（未来半个月）、中期（1—3 个月）、关键转折条件，并必须回答
“最可能发生什么、什么变量最重要、谁更有主动权、哪个风险最大、什么事件可能
改变判断”；禁止“竞争将更激烈/值得关注”类空话。验收期文章给出 5 个可检验
观察指标。上一期判断还会在下一期被验证（validated/partially_validated/
not_observed/reversed，写入 previous_outlook_verification）。

## 10. 如何保证不虚构基本事实？

- 事实层唯一来源是研究包（正式事实底座的投影），Prompt 的 FACT BOUNDARY
  明文禁止虚构；
- 生成后执行 Fact Safety Check：未来事件泄漏（超过 facts_cutoff 且非预测
  语境的日期 → HARD_BLOCK）、虚构民调数字（民调语境出现研究包外数字 →
  HARD_BLOCK）、研究包外日期/疑似人名/旧民调未带日期/缺失章节/无判断标题
  → review_note（人工复核提示）；
- Assessment 层对正式库只读（mode=ro + 冻结副本），不写回正式事实；
- 验收期实测：严重错误 0、泄漏 0、虚构民调 0。

## 11. 最终文章长什么样？

附：`FINAL_ASSESSMENT_PREVIEW.md`（验收期完整文章）。
结构：判断型标题 → 一、核心判断 → 二、本期关键变化（3—5 个事实群）→
三、因果链与权力逻辑 → 四、主要阵营研判 → 五、治理与社会议题 →
六、趋势判断 → 七、风险与证据限制。正文 1855 字，无任何内部 ID。

## 12. 与旧 R1/R2 报告相比，新报告具体好在哪里？

举同一周期的实际内容对比：

- 旧 R2（claim 拼装）：结论摘要式标题“台南蓝白合作进入实质协调但全市制度化
  未完成”，正文按“研判单元：判断→证据(带ID)→推理→反证→置信度→观察指标”
  机器化排布，机器门禁产生 12 条噪音告警（人名解析噪声“陈联合/何新增”、
  复合句、旧民调误报等），从 machine_rejected 被人工重分类才放行。
- 新 V1（research-driven）：标题“陈亭妃转守为攻拼组织控盘，谢龙介借裂痕攻
  绿营软肋”；核心判断直接写出“陈亭妃由个人整合转向组织控盘、赖系与挺宪派
  保留独立运作空间、谢龙介把选举拉高为在野整合对决”；因果链四层（直接原因
  →权力意图→地方结构→选举制度）；阵营研判逐方分析组织权/人事权；趋势给出
  可检验指标。事实安全自动检查 0 阻断、0 噪音提示，一次通过。

## 13. 是否仍像新闻摘要？

不是。文章以判断开路、以事实群证明、以权力与因果解释、以趋势收尾，
没有时间线式新闻罗列，也没有“值得关注/持续观察”类套话。

## 14. 业务质量评分是多少？

核心判断 4.5 / 关键变化识别 4.5 / 事实选择 4.5 / 因果分析 4.5 /
权力关系 4.5 / 政治意图 4 / 趋势判断 4.5 / 信息密度 4.5 / 语言力度 4.5 /
去摘要化 4.5 / 整体可用性 4.5（全部达到 ≥4 的最低生产目标），
严重事实错误 = 0。详见 BUSINESS_ACCEPTANCE.md。

## 15. 当前生产模型是什么？

deepseek-v4-pro（.env 的 DEEPSEEK_MODEL；配置允许 deepseek-v4-flash /
deepseek-v4-pro）。验收期实际调用：输入 40,374 tokens、输出 4,980 tokens、
耗时约 48 秒。

## 16. 未来是否可以切换其他模型？

可以。`llm.research_driven` 配置 provider/model/temperature/max_output_tokens/
timeout，adapter 已支持 deepseek/openai（OpenAI 兼容 Chat Completions）与
mock；未来接 ChatGPT/Claude/Gemini 只需在 adapter 增加对应实现并改配置，
研判架构不写死任何一家模型。

## 17. 9日/22日 Scheduler 是否真正就绪？

就绪。既有计划任务 `Tainan Election Assessment`（每月 9 日、22 日 09:00
Asia/Taipei，已注册、状态 Ready、下次运行 2026-08-22 09:00）→
`run_assessment_scheduled.bat/.ps1` → `python -m app.assessment.research_driven.scheduled`。
非调度日可 `--period-start/--period-end` 手动补跑；同周期已生成不重复调用
模型（幂等），`--force-regenerate` 显式重跑。

## 18. 正式事实系统有没有受到影响？

没有。Assessment 层只读正式事实（SQLite mode=ro + 输入冻结副本），只写
Assessment 运营数据（production 目录下的研究包/文章/Word/run 元数据）。
未覆盖/删除/回退任何正式事件、民调、候选审核历史或 facts_cutoff。
formal_fact_state_unchanged=true。

## 19. 完整 pytest 结果？

```text
2868 passed, 4 skipped, 0 failed
```

（4 skipped 为既有测试自身的条件跳过，与本次改动无关；无 xfail/skip 新增。）

## 20. 用户今后实际需要做什么？

```text
平时：不用做什么（系统自动采集 + 自动整理候选事实）
有空：审核候选事实（approve/reject/context_only/hold），完成后 complete_review
9日/22日前：确保事实审核已覆盖到报告周期结束日
9日/22日：系统自动出研判，进入人工终审
之后：打开 data/election_assessment/tainan_2026/production/FINAL_ASSESSMENT_PREVIEW.docx 阅读
```

终审命令：`python -m app.assessment.research_driven.review list/show/approve/reject`。
完整操作见 `ASSESSMENT_OPERATOR_GUIDE.md`（已简化为日常语言，无开发术语）。

---

## 完成判定

```text
TAINAN_ELECTION_ASSESSMENT_GOAL_COMPLETE=true
```

系统进入运营模式（台南选情智能研判系统 V1）。不再继续 R3/RC/Two-stage/
Prompt 无限调优；未来仅按真实运营中出现的稳定、重复、明显业务问题做修复。
