# Military / Election Word Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute the steps below in order with a failing-test-first loop.

**Goal:** Prevent 2026 local-election articles from appearing in the Word digest's “军武动态” section while preserving topic records and prominent rendering of important election articles.

**Architecture:** Keep `news_topics` unchanged and enforce exclusivity only in `build_word_digest`. Resolve election membership before military event construction, remove election URLs from the military presentation set, and exclude them from military event input so neither the legacy topic route nor the event route can re-add them.

**Tech Stack:** Python 3, pytest, python-docx

**Spec:** User report on 2026-09-05: “军武板块中混入了不少九合一选举新闻”.

## Global Constraints

- Do not change database persistence: one URL may retain both topics.
- Do not change the election classifier or source collectors.
- Important/critical election articles remain eligible for “重点提示”.
- Ordinary military articles remain excluded from general politics/international sections.
- Preserve current in-progress `app/military/` package and military-event work.

---

### Task 1: Lock the routing contract with regression tests

**Files:**
- Modify: `tests/test_military_word.py`

**Interfaces:**
- Consumes: `build_word_digest(..., election_config, election_entities, military_topic_urls, military_event_config)`
- Produces: regression coverage for legacy topic overlap, highlighted overlap, and event-derived overlap

- [ ] Change the existing overlap assertion so an ordinary election/military URL appears only in “九合一选举”.
- [ ] Add a critical overlap case proving it appears in “重点提示 + 九合一选举”, not “军武动态”.
- [ ] Add an event-enabled overlap case proving military event recognition cannot re-add the election article.
- [ ] Run the three tests and confirm the old implementation fails.

### Task 2: Make Word topic routing election-first

**Files:**
- Modify: `app/word_digest.py`

**Interfaces:**
- Consumes: `_is_election_article(...) -> bool`
- Produces: mutually exclusive `election_articles` and `military_articles`; `build_events` receives non-election input only

- [ ] Determine media election candidates independently of military presentation URLs.
- [ ] Build `election_urls` before military event construction.
- [ ] Remove `election_urls` from the military presentation URL set.
- [ ] Pass only non-election articles to `build_events`.
- [ ] Recompute official, domestic, and military article collections from the final presentation sets.
- [ ] Run the targeted regression tests and confirm they pass.

### Task 3: Regression verification

**Files:**
- Test: `tests/test_military_word.py`
- Test: `tests/test_election2026_word.py`
- Test: `tests/test_military_events_output.py`

**Interfaces:**
- Consumes: completed Word routing behavior
- Produces: test evidence for election layout, military layout, and event rendering

- [ ] Run the three focused test modules.
- [ ] Run `tests/test_military_*.py` and the Word-related suite if time permits.
- [ ] Report exact passes/failures and any unrelated concurrent-work limitation.
