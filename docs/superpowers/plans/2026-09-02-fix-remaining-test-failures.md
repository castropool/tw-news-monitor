# Fix Remaining Test Failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan task-by-task with a test cycle after each task.

**Goal:** 将当前完整测试套件中剩余的 24 项失败修复到通过，同时不削弱候选人监测、国际来源配置、发布清单和新闻简报的既有契约。

**Architecture:** 先从当前测试输出建立精确失败清单，再按共享根因分别修复生产代码、测试基线或生成物。每组修复都先用最小范围测试确认，再运行完整套件；涉及运行时日期的测试改为使用稳定的显式时间边界，而不是依赖系统当天日期。

**Tech Stack:** Python 3, pytest, SQLite/JSON/YAML 配置, PowerShell。

**Spec:** 当前用户请求“请完成剩余24项测试，不用使用codex with chatgpt”；无单独规格文件。

## Global Constraints

- 只修改主项目目录 `D:\\WXWorkLocal\\TW News-Monitor111\\tw-news-monitor-simple`。
- 不使用 Codex with ChatGPT 或外部规划/审查流程。
- 保留已有用户改动，禁止破坏性 Git 操作。
- 生产代码与测试必须保持现有项目的 Python/pytest 风格。

---

### Task 1: 建立失败清单并确认共享根因

**Files:**
- Read: `tests/`, `app/`, `config/`, `scripts/`
- Create: none
- Modify: none

**Interfaces:**
- Consumes: 当前工作树和完整 pytest 输出。
- Produces: 按候选游标、国际配置/清单、日期基线和其他独立根因分组的可复现失败列表。

- [ ] **Step 1: Run the full suite with the repository import path configured**

Run: `$env:PYTHONPATH=(Get-Location).Path; pytest -q`

Expected: 记录每个失败节点、断言期望值/实际值和是否涉及当前改动。

- [ ] **Step 2: Read each failing test and its production call path**

Use `rg -n` to locate each test name and the implementation symbols it exercises; group failures only when they share the same assertion contract.

- [ ] **Step 3: Run one representative test from each group in isolation**

Expected: representative failures reproduce without unrelated suite state.

### Task 2: 修复候选人监测管线的游标契约

**Files:**
- Modify: the candidate pipeline implementation identified by Task 1
- Test: the failing candidate pipeline tests identified by Task 1

**Interfaces:**
- Consumes: existing candidate collection/cursor API and its fixture data.
- Produces: stable cursor semantics matching the tests for first page, subsequent page, empty page, and deduplicated candidates.

- [ ] **Step 1: Add or adjust a focused regression assertion for the observed contract**

The focused test must assert the exact cursor/page result that the production API promises, including the empty-page boundary.

- [ ] **Step 2: Run the focused test and confirm the current failure**

Run the smallest candidate test node; expected: it fails with the recorded cursor mismatch.

- [ ] **Step 3: Implement the minimal cursor fix**

Preserve ordering, deduplication, and backward compatibility; do not change unrelated election scoring or database behavior.

- [ ] **Step 4: Run the complete candidate test group**

Expected: all candidate pipeline cursor tests pass.

### Task 3: 修复国际配置与发布清单基线

**Files:**
- Modify: international source/config or manifest generation code identified by Task 1
- Test: failing international source/config and SHA256 manifest tests

**Interfaces:**
- Consumes: `config/sources.yaml`, international source settings, Git-aware manifest behavior.
- Produces: configuration assertions and source manifests that agree with the intended production state and remain deterministic in a Git checkout.

- [ ] **Step 1: Confirm whether each expected value is stale test data or a production regression**

Compare the failing assertion with the current config and the code that consumes it; do not blindly change a test to match an accidental value.

- [ ] **Step 2: Add the smallest regression coverage for the intended state**

Cover the enabled/disabled source contract and the Git metadata policy separately.

- [ ] **Step 3: Update implementation/config/fixture as appropriate**

Keep the manifest hash calculation deterministic and ensure tests explicitly select the intended repository state rather than relying on an implicit dirty checkout.

- [ ] **Step 4: Run the international/configuration test group**

Expected: all related source-config and manifest tests pass.

### Task 4: 修复新闻简报的日期基线

**Files:**
- Modify: failing newsletter/digest tests and only the production date handling if Task 1 proves it is incorrect
- Test: date-pinned newsletter and digest tests

**Interfaces:**
- Consumes: digest builders, configured reference dates, and current system date.
- Produces: deterministic tests with explicit dates, while production continues to use the real current date when no override is supplied.

- [ ] **Step 1: Reproduce the date failures with the pinned test date**

Run the failing newsletter nodes with the same date fixture and capture the boundary mismatch.

- [ ] **Step 2: Make the test fixture date explicit and stable**

Pass a fixed date/clock into the code path where the test already implies a historical date, or update the stale fixture only if the expected product behavior changed intentionally.

- [ ] **Step 3: Run all date-sensitive digest tests**

Expected: no test depends on the host calendar date, and all digest assertions pass.

### Task 5: 全量回归与多通道验收

**Files:**
- Read: all changed files and test output
- Modify: only if a regression is found

**Interfaces:**
- Consumes: all fixes from Tasks 2–4.
- Produces: a clean full-suite result and an evidence summary with remaining risks, if any.

- [ ] **Step 1: Run the full suite**

Run: `$env:PYTHONPATH=(Get-Location).Path; pytest -q`

Expected: `0 failed`.

- [ ] **Step 2: Run an independent syntax and diff check**

Run: `python -m compileall -q app scripts` and `git diff --check`.

Expected: both commands succeed.

- [ ] **Step 3: Perform direct behavior spot checks**

Use the public functions/CLI fixtures to verify one candidate cursor flow, one international config/manifest path, and one fixed-date digest path outside the aggregate full-suite command.

- [ ] **Step 4: Review the final diff and working-tree status**

Confirm only task-related files changed and no Codex with ChatGPT calls were made.
