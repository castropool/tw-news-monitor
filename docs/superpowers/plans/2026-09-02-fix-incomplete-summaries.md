# 修复新闻梗概不完整句子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 防止 RSS、模型或元描述中的硬截断文本直接出现在新闻简报中，使已保存和新生成的梗概在可用时以完整句子结束。

**Architecture:** 在 `app/summarizer.py` 集中识别并清理 RSS/模型摘要的尾部截断标记，统一判定需要重写的摘要；在摘要生成失败时保留可读的无省略号输入摘要，并在 Word 写入前做最后一道轻量完整性清洗。保持现有 Article、数据库和 Word 接口不变。

**Tech Stack:** Python 3、BeautifulSoup、pytest、python-docx、SQLite。

**Spec:** 用户要求修复部分新闻梗概以不完整句子结束的问题。

## Global Constraints

- 遵守项目规则：摘要失败不得阻塞简报生成。
- 不在 collector 中增加关键词过滤；修复集中在摘要清洗/生成链路。
- 不改动当前工作区中与本问题无关的未提交文件。
- 不伪造 RSS 或文章正文没有提供的事实。

---

### Task 1: 固化不完整摘要的回归行为

**Files:**
- Modify: `tests/test_summarizer.py`
- Modify: `tests/test_word_digest.py`

**Interfaces:**
- Consumes: 当前 `clean_rss_summary()`、`summary_needs_rewrite()` 和 `build_word_digest()` 接口。
- Produces: 可验证的尾部标记清洗、模型摘要完整性和 Word 输出回归约束。

- [x] **Step 1: Write the failing tests**

  在 `tests/test_summarizer.py` 增加以下行为断言：带有 `...`、`…`、连续句点或 feed 截断尾巴的 RSS 文本不能保留截断标记；完整句保留；模型响应中的截断摘要会被拒绝或标记为需要重写；在没有句末标点的超长文本上不得声称已经得到完整句。

  在 `tests/test_word_digest.py` 增加断言：传给 Word 的摘要经过最终清洗后，不输出以省略号或半个词结尾的梗概。

- [x] **Step 2: Run tests to verify they fail**

  Run: `python -m pytest tests/test_summarizer.py tests/test_word_digest.py -q`

  Expected: 新增断言失败，现有行为会保留 RSS 或模型返回的硬截断结尾。

### Task 2: 实现集中式摘要完整性修复

**Files:**
- Modify: `app/summarizer.py`
- Modify: `app/word_digest.py`

**Interfaces:**
- Consumes: Task 1 的失败测试和现有摘要来源字段。
- Produces: 可复用的摘要清洗/完整性判定函数；RSS 和模型摘要共用相同的尾部规则；Word 输出前的安全兜底。

- [x] **Step 1: Implement the minimal cleanup**

  增加尾部硬截断标记识别，清理 `...`、Unicode 省略号、重复句点及其前后的空白；只在有真实句末标点时截到最后一个完整句，不补写输入中不存在的事实。

  扩展 `summary_needs_rewrite()` 覆盖所有已识别的截断形式，并让 `parse_summaries_response()` 不接受带硬截断标记的模型结果。

- [x] **Step 2: Add the generation fallback**

  当 RSS 摘要需要重写但远程摘要不可用或失败时，保留清洗后的完整句部分；若没有任何完整句，则使用标题构成明确的保守句，不添加日期、人物、地点或数字等外部事实。

- [x] **Step 3: Add the Word boundary guard**

  在 `app/word_digest.py` 写入 `梗概` 前调用同一清洗函数，防止历史数据库中的旧截断摘要绕过摘要重写流程直接进入 Word；保留国际新闻的英文元数据展示规则。

### Task 3: 验证并检查变更边界

**Files:**
- No new files.

**Interfaces:**
- Consumes: Task 2 的实现。
- Produces: 针对性测试、全量测试和未提交差异检查结果。

- [x] **Step 1: Run focused tests**

  Run: `python -m pytest tests/test_summarizer.py tests/test_word_digest.py -q`

  Expected: 全部通过。

- [x] **Step 2: Run the full suite**

  Run: `python -m pytest -q`

  Expected: 全部通过，且不改变现有测试保护的新闻来源、国际新闻和选举管线行为。

- [x] **Step 3: Review the final diff**

  Run: `git diff -- app/summarizer.py app/word_digest.py tests/test_summarizer.py tests/test_word_digest.py`

  Expected: 只包含本次摘要完整性修复及其测试，不覆盖用户已有的其他修改。
