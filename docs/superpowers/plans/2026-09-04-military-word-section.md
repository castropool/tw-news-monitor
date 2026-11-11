# Military Word Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five military-specialist sources, conservative military noise filtering, multi-topic SQLite persistence, and a dynamically numbered Word “军武动态” section without changing importance or notification behavior.

**Architecture:** Existing collectors continue to emit `Article`. Source configuration identifies military candidates; a dedicated pure filter runs before article insertion, and `(url, military)` is persisted independently of article category. Word receives persisted military URLs and routes topical articles before general categories.

**Tech Stack:** Python 3.12, httpx, BeautifulSoup4, feedparser, SQLite, PyYAML, python-docx, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-military-word-section-design.md`

## Global Constraints

- Only the five approved core sources are in scope.
- Do not fetch article bodies.
- Do not scan all political news for military keywords.
- Reuse `UDNCollector` and `RSSCollector`.
- Each configured source returns at most 20 articles.
- A single source failure must not stop the monitor.
- No Playwright or Selenium.
- Preserve importance scoring, election classification, deduplication, official sources, Feishu behavior, and scheduled tasks.
- All new unit tests run offline.
- Production smoke uses `DISABLE_FEISHU_SEND=1`.

---

### Task 1: Military Configuration and Conservative Filter

**Files:**
- Create: `app/military.py`
- Create: `config/military.yaml`
- Create: `tests/fixtures/military/filter_golden.json`
- Create: `tests/test_military_filter.py`

**Interfaces:**
- Consumes: `Article.title`, `Article.summary`, source dictionaries containing `topic` and `military_source_type`.
- Produces: `load_military_config(path: str | Path | None = None) -> dict`, `is_military_source(source: dict) -> bool`, `filter_military_articles(articles: list[Article], config: dict | None) -> tuple[list[Article], list[Article]]`.

- [ ] **Step 1: Write failing configuration and filter tests**

```python
def test_keep_keyword_overrides_noise_phrase():
    article = make_article("部长视导飞弹战备慰问活动")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == [article]
    assert blocked == []

def test_plain_condolence_is_dropped():
    article = make_article("地方首长慰问官兵")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == []
    assert blocked == [article]
```

- [ ] **Step 2: Run the focused test and verify missing-module failure**

Run: `python -m pytest tests/test_military_filter.py -q`

Expected: FAIL because `app.military` does not exist.

- [ ] **Step 3: Implement fail-closed config loading and keep-first filtering**

```python
def filter_military_articles(articles, config=None):
    if not config or not config.get("enabled", False):
        return list(articles), []
    kept, blocked = [], []
    for article in articles:
        text = f"{article.title} {article.summary or ''}".lower()
        protected = any(word.lower() in text for word in config["keep_keywords"])
        noisy = any(word.lower() in text for word in config["drop_phrases"])
        (blocked if noisy and not protected else kept).append(article)
    return kept, blocked
```

- [ ] **Step 4: Add 50 positive and 20 negative golden cases and assert metrics**

```python
assert kept_positive / positive_total >= 0.98
assert dropped_noise / noise_total >= 0.95
```

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_military_filter.py -q`

Expected: PASS with both metric gates satisfied.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/military.py config/military.yaml tests/fixtures/military/filter_golden.json tests/test_military_filter.py
git commit -m "feat: add conservative military content filter"
```

### Task 2: Five-Source Collection

**Files:**
- Create: `app/collectors/military.py`
- Modify: `app/collectors/__init__.py`
- Modify: `app/main.py`
- Modify: `app/diagnose.py`
- Modify: `config/sources.yaml`
- Create: `tests/fixtures/military/ltn_list.html`
- Create: `tests/fixtures/military/nownews_base.html`
- Create: `tests/fixtures/military/nownews_page_1.html`
- Create: `tests/fixtures/military/mna_overview.html`
- Create: `tests/fixtures/military/ydn_defense.xml`
- Create: `tests/fixtures/military/ydn_weapons.xml`
- Create: `tests/fixtures/military/ydn_world.xml`
- Create: `tests/test_military_collectors.py`

**Interfaces:**
- Consumes: `BaseCollector.get_with_retry`, `BaseCollector.normalize_url`, `Article`, existing `UDNCollector`, existing `RSSCollector`.
- Produces: `LtnMilitaryCollector`, `NownewsMilitaryCollector`, `MNAMilitaryCollector`, `parse_roc_date(value: str) -> datetime | None`; collector map keys `ltn_military`, `nownews_military`, `mna_military`.

- [ ] **Step 1: Add offline parser fixtures and failing tests**

```python
def test_roc_year_conversion():
    assert parse_roc_date("民国115年09月04日").year == 2026

def test_nownews_stops_after_old_page(fake_pages):
    collector = NownewsMilitaryCollector(source(max_pages=3, stop_after_hours=72))
    collector._client = fake_pages
    articles = collector.collect()
    assert [a.title for a in articles] == ["新军情一", "新军情二"]
    assert fake_pages.calls == 2
```

- [ ] **Step 2: Run collector tests and verify missing-class failures**

Run: `python -m pytest tests/test_military_collectors.py -q`

Expected: FAIL because military collector classes do not exist.

- [ ] **Step 3: Implement LTN and MNA list parsers**

Use URL allowlists, list-page metadata only, `mark_outcome`, and schema failure when HTTP succeeds but no legal list item is found.

- [ ] **Step 4: Implement NOWnews pagination and early-stop logic**

```python
for page_index in range(max_pages):
    page_url = base_url if page_index == 0 else f"{base_url}page/{page_index}/"
    page_articles = parse_page(get_with_retry(page_url))
    for article in page_articles:
        if article.published_at and article.published_at < cutoff:
            reached_old = True
            break
        articles.append(article)
        if len(articles) == MAX_ITEMS:
            return articles
    if reached_old:
        break
```

- [ ] **Step 5: Register only three new HTML collector types and configure seven entries**

The seven entries represent five core sources: LTN, UDN, NOWnews, three YDN RSS feeds, and MNA. UDN uses `type: udn`; YDN uses `type: rss`.

- [ ] **Step 6: Add network and malformed-HTML tests**

Assert raised network errors are isolated by `collect_all`, and direct collector calls report schema failure for valid HTML without legal items.

- [ ] **Step 7: Run collector and configuration tests**

Run: `python -m pytest tests/test_military_collectors.py tests/test_config_validation.py tests/test_collectors.py -q`

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add app/collectors/military.py app/collectors/__init__.py app/main.py app/diagnose.py config/sources.yaml tests/fixtures/military tests/test_military_collectors.py
git commit -m "feat: collect five military news sources"
```

### Task 3: Multi-Topic Database Migration

**Files:**
- Modify: `app/database.py`
- Create: `tests/test_military_database.py`
- Modify: `tests/test_election2026_db.py`

**Interfaces:**
- Consumes: current `news_topics` columns and election wrapper methods.
- Produces: `save_topic(...)`, `save_military_topic(url: str, source_type: str, created_at: datetime | None = None)`, `get_topic_urls(urls: list[str], topic: str) -> set[str]`, composite unique key `(url, topic)`.

- [ ] **Step 1: Write failing fresh-schema, migration, and coexistence tests**

```python
db.save_election_topic(url, scope="local", region="台南市", regions=["台南市"], event_type="campaign", confidence=90)
db.save_military_topic(url, "commercial_military")
rows = db.conn.execute("SELECT topic FROM news_topics WHERE url=? ORDER BY topic", (url,)).fetchall()
assert rows == [("election_2026_local",), ("military",)]
```

- [ ] **Step 2: Run database tests and verify old URL-unique schema failure**

Run: `python -m pytest tests/test_military_database.py tests/test_election2026_db.py -q`

Expected: FAIL because one URL cannot hold two topics.

- [ ] **Step 3: Implement transactional idempotent table rebuild**

Create target schema with `source_type TEXT` and `UNIQUE(url, topic)`, copy existing IDs and data, replace inside one SQLite transaction, and rebuild indexes.

- [ ] **Step 4: Implement generic upsert/read methods and preserve election wrappers**

```sql
INSERT INTO news_topics (...)
VALUES (...)
ON CONFLICT(url, topic) DO UPDATE SET
  source_type=excluded.source_type,
  confidence=excluded.confidence,
  created_at=excluded.created_at
```

All election reads add `AND topic = 'election_2026_local'`.

- [ ] **Step 5: Run database tests three times to prove migration idempotency**

Run: `python -m pytest tests/test_military_database.py tests/test_election2026_db.py -q`

Expected: PASS on repeated execution with historical row counts unchanged.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/database.py tests/test_military_database.py tests/test_election2026_db.py
git commit -m "feat: support multiple topics per news URL"
```

### Task 4: Pipeline Filtering and Topic Persistence

**Files:**
- Modify: `app/main.py`
- Modify: `tests/test_military_collectors.py`
- Create: `tests/test_military_pipeline.py`

**Interfaces:**
- Consumes: `filter_military_articles`, source `topic`/`military_source_type`, `Database.save_military_topic`.
- Produces: optional `military_config` argument on `collect_all` without changing its return tuple; persisted military topic for both inserted and pre-existing URLs.

- [ ] **Step 1: Write failing pipeline tests**

Cover protected military insertion, noise blocked before article save, pre-existing URL receiving military topic, dual-source URL determinism, and one military source failure followed by a successful source.

- [ ] **Step 2: Run pipeline tests and verify missing integration failure**

Run: `python -m pytest tests/test_military_pipeline.py -q`

Expected: FAIL because `collect_all` ignores military configuration.

- [ ] **Step 3: Filter each configured military source after collection**

Track `military_url_types` before run-level dedup so a duplicate retained from another source still receives its topic.

- [ ] **Step 4: Persist military topics after article insertion**

Only persist URLs that exist in `articles`; prefer `official_military` deterministically if both source types report the same URL. Catch topic-write exceptions and log without aborting article collection.

- [ ] **Step 5: Load military config once and pass it through all collection modes**

Update bootstrap, dry-run, standard scheduled run, and catch-up paths without changing notifier logic.

- [ ] **Step 6: Run focused and existing collection tests**

Run: `python -m pytest tests/test_military_pipeline.py tests/test_content_filter.py tests/test_catchup_main.py tests/international/test_isolated_run_guards.py -q`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add app/main.py tests/test_military_pipeline.py tests/test_military_collectors.py
git commit -m "feat: persist military topics in collection pipeline"
```

### Task 5: Dynamic Word Sections

**Files:**
- Modify: `app/word_digest.py`
- Modify: `app/main.py`
- Create: `tests/test_military_word.py`
- Modify: `tests/test_election2026_word.py`
- Modify: `tests/test_word_digest.py`
- Modify: `tests/test_word_official.py`
- Modify: `tests/test_ltn_defense_vot_word_categories.py`

**Interfaces:**
- Consumes: `military_topic_urls: set[str] | None`, existing `importance_results`, election annotations, international configuration.
- Produces: dynamic level-1 sections and no “新闻媒体” wrapper.

- [ ] **Step 1: Write the six required failing Word tests**

```python
assert "军武动态" not in render([politics])
assert headings(render([military], military_urls={military.url})) == ["一、军武动态"]
assert count_title(render([critical_military], importance=critical, military_urls={critical_military.url}), critical_military.title) == 2
```

Also assert election + military numbering is contiguous, normal military is absent from politics/international, and existing official sources remain ahead of dynamic content sections.

- [ ] **Step 2: Run Word tests and verify current hierarchy failures**

Run: `python -m pytest tests/test_military_word.py -q`

Expected: FAIL because `military_topic_urls` and level-1 routing do not exist.

- [ ] **Step 3: Add a single dynamic level-1 heading allocator**

```python
def add_primary_heading(title: str) -> None:
    nonlocal heading_num
    heading_num += 1
    doc.add_heading(f"{_primary_num(heading_num)}、{title}", level=1)
```

- [ ] **Step 4: Route highlights, election, military, and general categories**

Build URL sets first. Remove military/election URLs from general categories. Render non-normal importance articles in “重点提示” without removing them from their topical or general destination.

- [ ] **Step 5: Promote general categories and international media to level 1**

Remove only the “新闻媒体” wrapper. Reuse `_render_media_item` so text styling, summaries, dates, catch-up labels, and hyperlinks remain unchanged.

- [ ] **Step 6: Query persisted military URLs for every Word-producing main path**

Call `db.get_topic_urls([a.url for a in articles], "military")` and pass the result to `build_word_digest`; direct callers that omit it retain legacy no-inference behavior.

- [ ] **Step 7: Update intentional hierarchy assertions and run Word/election suites**

Run: `python -m pytest tests/test_military_word.py tests/test_election2026_word.py tests/test_word_digest.py tests/test_word_official.py tests/test_ltn_defense_vot_word_categories.py -q`

Expected: PASS with 100% military Word and election Word cases.

- [ ] **Step 8: Commit Task 5**

```bash
git add app/word_digest.py app/main.py tests/test_military_word.py tests/test_election2026_word.py tests/test_word_digest.py tests/test_word_official.py tests/test_ltn_defense_vot_word_categories.py
git commit -m "feat: add dynamic military Word section"
```

### Task 6: Full Gates and Production Smoke

**Files:**
- Modify only if a failing gate identifies an in-scope defect.
- Produce runtime artifacts under the existing dry-run/temp output path, not committed source files.

**Interfaces:**
- Consumes: all completed feature paths.
- Produces: evidence for every acceptance gate and final PASS/FAIL decision.

- [ ] **Step 1: Run compile and new-test gates**

Run: `python -m compileall app`

Run: `python -m pytest tests/test_military_filter.py tests/test_military_collectors.py tests/test_military_database.py tests/test_military_pipeline.py tests/test_military_word.py -q`

Expected: PASS.

- [ ] **Step 2: Run database, Word, and election gates separately**

Run: `python -m pytest tests/test_military_database.py tests/test_election2026_db.py -q`

Run: `python -m pytest tests/test_military_word.py -q`

Run: `python -m pytest tests/test_election2026_classifier.py tests/test_election2026_region.py tests/test_election2026_word.py tests/test_election2026_db.py tests/test_election2026_config.py -q`

Expected: PASS for every command.

- [ ] **Step 3: Run the complete existing suite**

Run: `python -m pytest tests -q`

Expected: no failures.

- [ ] **Step 4: Execute five-source real collection without notifications**

Set `DISABLE_FEISHU_SEND=1`; load the seven military source entries; collect into an isolated temporary SQLite database; report each core source as success only when HTTP/RSS structure is valid and at least one legal item is parsed.

- [ ] **Step 5: Verify live database topics and Word artifact independently**

Query `articles` and `news_topics` directly for `topic='military'`; open the generated DOCX with python-docx and assert “军武动态” exists, numbering is contiguous, and blocked noise titles are absent.

- [ ] **Step 6: Run the production command in dry-run/disabled-send mode**

Use the existing CLI's isolated database mechanism with `DISABLE_FEISHU_SEND=1`. Confirm no Feishu call is attempted and the command exits zero.

- [ ] **Step 7: Compare three validation channels**

Require agreement among offline tests, direct SQLite/Word inspection, and real collection logs. Any conflict keeps the relevant gate failed until explained and corrected.

- [ ] **Step 8: Record final gate table**

Mark `FINAL = PASS` only if 5/5 sources, positive retention >=98%, noise accuracy >=95%, Word 100%, database 100%, election 100%, new tests 100%, all old tests with no new failures, and production smoke all pass. Otherwise mark `FINAL = FAIL`.
