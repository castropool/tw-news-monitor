# 国际主流媒体免费监测层生产化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏台湾新闻现有行为的前提下，把 Reuters、Financial Times、WSJ Newsletter、Bloomberg Newsletter 的合法免费监测能力推进到可审计的 Release Candidate，并只在门禁通过后由操作员决定生产启用。

**Architecture:** 复用现有 `Article`、`articles` SQLite 表和主流水线；通过 `app/newsletter_ingestion/` 把 Gmail readonly 邮件转成 Article，通过 `RelevanceDecision` 进行可解释过滤，再以短生命周期内存 `EventCluster`/coverage 合并跨媒体报道。中文交付复用可替换 translator，事件级 `notification_candidates` 只把达到重要性门槛的 cluster 交给 notifier。

**Tech Stack:** Python 3、现有 dataclass/SQLite、PyYAML、httpx、BeautifulSoup、feedparser、pytest、python-docx；Gmail 使用 Google 官方 `google-api-python-client`、`google-auth`、`google-auth-httplib2`、`google-auth-oauthlib`；Windows PowerShell 隔离脚本；LibreOffice/Poppler 仅在环境可用时用于 Word/PDF 检查。

**Spec:** [docs/superpowers/specs/2026-08-14-international-media-production-design.md](../specs/2026-08-14-international-media-production-design.md)

## Global Constraints

- 四个 exact source ID 固定为 `reuters_international`、`ft_alphaville`、`wsj_newsletter`、`bloomberg_newsletter`；生产 `enabled` 必须全部为布尔 `false`，旧 `wsj_international`/`wsj_rss` 永久冻结为 disabled，不得作为生产证据。
- 继续使用 `Article`、单表 SQLite、现有 URL/identity 去重和主流水线；不创建 `InternationalArticle`、events、coverage 或 snapshots 表。
- Reuters 只读官方 sitemap，`access_level=metadata_only`；FT 只读 FT Alphaville 官方 RSS，`access_level=public`；WSJ/Bloomberg 只读合法 Newsletter，`access_level=newsletter`。
- `metadata_only` 和 `newsletter` 全路径不得访问文章正文、付费正文、受限 HTML、付费 API 或正文 redirect；不得绕过 paywall、robots、登录、Cloudflare、验证码、共享 Cookie、代理池或未经授权 API。
- Gmail 使用 readonly scope、`InternationalNews` label 和 sender/domain allowlist；默认不 mark-as-read、不删除、不移动、不回复、不转发；token/client secret 不进入 Git、YAML、SQLite、日志、fixture 或报告。
- Gmail 依赖由 Wave 2 owner 写入 `requirements.txt`：`google-api-python-client>=2.170,<3`、`google-auth>=2.35,<3`、`google-auth-httplib2>=0.2,<1`、`google-auth-oauthlib>=1.2,<2`；clean install 解析版本记录到 `validation/international_media/dependency_versions.json`。
- 单源失败必须隔离；HTTP 必须有 connect/read timeout、有限 retry/backoff、最大响应大小和明确 User-Agent；HTTP 200 且 schema 有效但 0 条是成功空源，连续 3 次有效空源或 48 小时无 item 才转 `stale`；结构/HTTP 连续 3 次失败转 `broken`。
- `RelevanceDecision` 必须输出 `relevant/tier/topics/entities/reason/rule_version/input_hash`；普通 China、Washington、semiconductor、Pentagon、Japan 关键词不能单独纳入。
- 现有 importance 阈值固定为 score `65` → `important`、score `85` → `critical`；Tier-1 bonus 为 3，不能使普通 Reuters 仅凭媒体身份进入 important。
- `EventCluster` 只在当前 run 内存中存在，默认 24 小时窗口；同事件只保留一个 canonical 和 coverage；跨日重大后续不得强行合并。
- Wave 4 只负责 `golden_events.jsonl` 的 12 对标签和四篇输入 coverage 样本，不实现 EventCluster、不使用 fallback、不宣称生产 event precision/recall；`golden_metrics.py` 在 `app.international_events` 不可用或未显式注入时必须返回 `status="pending_wave5"`、`counted=false`，该状态不得计入 RC 通过。Wave 5 实现真实 EventCluster 后接管并扩展该 runner，只有 `status="pass"` 且 pair precision/recall、cluster_id、canonical、coverage 均 exact 时才计入 RC。
- `app/notification_candidates.py::build_notification_candidates` 只允许把 `relevant=true`、非旧闻/非 baseline catch-up、score≥65、level 为 important/critical 的每个 cluster 生成一个候选；普通 relevant 只进 Word。
- `app/notifier.py::Notifier.send_event_candidates` 在自动化、子代理和 RC runner 中只能接收 Recording/Null notifier；自动化永远不得发真实飞书。RC 后真实发送仍只能由操作员显式执行。
- Scheduler、`app/feishu.py`、真实 Feishu 凭据/端点和生产 DB 不由任何 Wave 修改或用于验收；隔离验收使用独立 config、SQLite、reports 和 dry-run notifier。
- 隔离运行唯一 Python owner 是 `validation/international_media/run_isolated.py`；它提供 `validate_isolation_config`、`run_isolated_collection` 和 `load_runs`。`run_isolated.ps1` 只能做参数校验、调用该 Python module 并转发退出码，不得复制运行逻辑；所有 Task 和验收命令都调用同一组 Python 接口。
- 相关性黄金 corpus 最少 32 条：16 正例、16 负例；事件 pair 最少 12 对；Newsletter parser fixture 最少 32 个 payload。少于任一类别最小量不得生成 RC。
- 相关性门禁为 precision≥0.95、recall≥0.90、hard-negative FP=0；tier/topic/entity exact match≥0.90。Importance level exact accuracy≥0.90，important/critical precision≥0.90；事件仅在 Wave 5 `status="pass"` 后计入，pair precision/recall、cluster ID、canonical、coverage exact 必须分别为 1.00/true；Wave 4 `pending_wave5` 不计 RC 通过。
- 全量门禁必须包含 `python -m compileall app tests`、`python -m pytest -q`、两轮 Reuters/FT 隔离 smoke、Newsletter fixture/live 条件验收、relevance/importance、false-positive、cross-source dedup、Word、freshness、baseline、idempotency、failure isolation、台湾回归和安全扫描。
- 当前无 Git；不得初始化 Git、不得声称存在 Git 提交。每个 Task 的最后一步都必须生成 handoff SHA-256 manifest，并记录 `git=absent`。
- 最终报告严格按 A–Q：A 架构；B 文件/owner/hash；C 来源状态；D Newsletter；E Relevance；F Importance；G Dedup/notification；H DB；I Word；J 安全；K 测试；L live；M 幂等；N 回归；O 生产状态；P 限制；Q 三态结论。

## Ownership and Handoff Map

同一文件不得并行修改。下一 owner 只有在上一 owner 的专门测试、manifest 和 `git=absent` handoff 记录完成后才能写入。

| 文件范围 | 唯一 owner | 冻结/交接 |
| --- | --- | --- |
| 既有 `app/models.py::Article` | 既有主流水线 owner（本计划无实现 owner） | 所有 Wave 只读复用，禁止创建替代 Article 类型 |
| `validation/international_media/build_sha256_manifest.py`、`validation/international_media/baseline_manifest.json`、`validation/international_media/sha256_manifest_no_git.json`、`tests/international/test_sha256_manifest.py` | Wave 0 Release owner | Wave 0 后冻结；只生成 handoff manifest，不改其它 Wave 产物 |
| `validation/international_media/run_isolated.py`、`validation/international_media/run_isolated.ps1`、`validation/international_media/isolated_run_schema.json`、`tests/international/test_isolated_run_guards.py` | Wave 0 Isolation owner | Python module 先通过专门测试；PS1 仅薄封装；Wave 0 后冻结；`load_runs` 只在此 owner 实现 |
| `app/newsletter.py`、`app/newsletter_ingestion/{models,policy,parser,url_policy,collector}.py` | Wave 1 Newsletter owner | Wave 1 后 parser 契约冻结 |
| `requirements.txt`、`validation/international_media/dependency_versions.json`、`app/newsletter_ingestion/{mailbox,gmail_client,oauth,verify_sources}.py`、`tests/test_newsletter_source_verification.py` | Wave 2 Mailbox owner | Wave 2 handoff 后冻结；Wave 6 只能调用/读取，不得重写 |
| `validation/international_media/newsletter_availability/{reuters_international,ft_alphaville,wsj_newsletter,bloomberg_newsletter}_{public,gmail,summary}_YYYY-MM-DD.json`、`validation/international_media/newsletter_availability/newsletter_live_verification_manifest.json`、`docs/INTERNATIONAL_NEWSLETTER_OPERATOR_GUIDE.md`、`tests/newsletter_ingestion/test_operator_guide_contract.py` | Wave 2 Mailbox owner | Wave 2 产生不可覆盖的 raw/public、raw/gmail 和 summary 证据；Wave 6 只能读取，不得重写或重新验证 |
| `app/collectors/base.py`、`app/collectors/reuters.py`、`app/collectors/ft_alphaville.py`、`app/collectors/wsj_newsletter.py`、`app/collectors/bloomberg_newsletter.py`、`app/source_health.py`、`app/main.py` 采集接线、`config/sources.yaml`、`config/international_media.yaml` | Wave 3 Collection owner | Wave 3 manifest 通过后冻结；Wave 5 仅按 handoff 修改 main 的 delivery 接线 |
| `app/international.py`、`tests/test_international.py`、`tests/test_international_relevance_golden.py`、`tests/fixtures/international/golden_relevance.jsonl` | Wave 4 Relevance owner | Wave 4 后冻结；Wave 5 只读，不回写这些文件 |
| `app/importance.py`、`config/importance_rules.yaml`、`tests/test_importance_tier1.py`、`tests/test_international_importance_golden.py`、`tests/fixtures/international/golden_importance.jsonl` | Wave 4 Importance owner | Wave 4 后冻结；后续 Wave 只读并复用其接口 |
| `app/international_events.py`、`app/international_translation.py`、`app/notification_candidates.py`、`app/notifier.py`、Word/digest 接线 | Wave 5 Delivery owner | Wave 5 后冻结 |
| `tests/test_international_events.py`、`tests/test_international_event_metrics_integration.py`、`tests/test_international_translation.py`、`tests/test_notification_candidates.py`、`tests/test_notify.py`、`tests/test_word_digest.py`、`tests/assessment/test_word_report_renderer.py` | Wave 5 Delivery owner | Word renderer 为只读回归依赖；Wave 5 handoff 后冻结 |
| `validation/international_media/rc_manifest.py`、`validation/international_media/rc_manifest.json`、`validation/international_media/INTERNATIONAL_MEDIA_RELEASE_CANDIDATE.md`、`validation/international_media/security_scan.py`、`validation/international_media/security_scan.json`、`validation/international_media/final_validation_sha256_manifest.json`、`validation/international_media/isolated_run_1.json`、`validation/international_media/isolated_run_2.json`、`validation/international_media/word_structure_report.json`、`docs/INTERNATIONAL_MEDIA_OPERATOR_GUIDE.md` | Wave 6 Release owner | Wave 6 只读 Wave 2 Newsletter 证据；Wave 7 只读复核 |
| `tests/test_international_security_scan.py`、`tests/test_international_release_gates.py`、`tests/test_international_release_manifest.py` | Wave 6 Release owner | Wave 6 handoff 后冻结，Wave 7 只读 |
| `validation/international_media/independent_review.py`、`validation/international_media/independent_review.json`、`validation/international_media/operator_release.py`、`validation/international_media/operator_release_decision.json`、`tests/test_international_independent_review.py`、`tests/test_international_operator_gate.py` | Wave 7 Independent Review owner | Wave 7 handoff 后冻结；Wave 7 只读 Wave 6 RC/security/final report |
| `tests/newsletter_ingestion/test_models_policy.py`、`tests/newsletter_ingestion/test_parser.py`、`tests/newsletter_ingestion/test_url_policy.py`、`tests/newsletter_ingestion/test_collector.py`、`tests/test_newsletter.py`、`tests/fixtures/international/newsletters/` | Wave 1 Newsletter owner | Wave 1 handoff 后冻结 |
| `tests/newsletter_ingestion/test_dependency_contract.py`、`tests/newsletter_ingestion/test_mailbox_contract.py`、`tests/newsletter_ingestion/test_gmail_client.py`、`tests/newsletter_ingestion/test_oauth.py` | Wave 2 Mailbox owner | Wave 2 handoff 后冻结 |
| `tests/test_international_collection_wiring.py`、`tests/test_international_config.py`、`tests/test_config_validation.py`、`tests/test_source_health.py`、`tests/test_reuters.py`、`tests/test_ft_alphaville.py`、`tests/test_wsj_newsletter.py`、`tests/test_bloomberg_newsletter.py`、`tests/test_catchup_main.py` | Wave 3 Collection owner | Wave 3 handoff 后冻结；后续 Wave 只读 |
| `tests/test_international_golden_metrics.py`、`tests/fixtures/international/golden_events.jsonl`、`validation/international_media/golden_metrics.py`、`validation/international_media/golden_metrics.json` | Wave 4 Metrics owner；`golden_metrics.py` 在 `handoff_task_4_3_sha256.json` 后转移给 Wave 5 Delivery owner | Wave 4 只交付语料和 `pending_wave5` runner；Wave 5 以新 handoff SHA-256 接管真实 EventCluster 指标，Wave 6 只读最终报告 |
| `app/feishu.py`、Scheduler、生产 DB、真实凭据 | 无实现 owner | 全程冻结，只有 RC 后操作员可执行真实发送/启用 |

## Wave 0 — Baseline and Isolated Harness

### Task 0.1: Freeze no-Git baseline and SHA-256 tooling

**Files:**

- Create: `validation/international_media/build_sha256_manifest.py`
- Create: `validation/international_media/baseline_manifest.json`
- Create: `validation/international_media/sha256_manifest_no_git.json`
- Test: `tests/international/test_sha256_manifest.py`

**Interfaces:**

- Consumes: include roots `app config tests docs prompts validation/international_media`; excludes generated `**/sha256_manifest*.json`.
- Produces: `build_sha256_manifest.py::build_manifest(include_roots, excludes, output_path) -> dict` with `path,size,sha256`; output records `git_state="absent"`.

- [ ] **Step 1: Write the failing test**

```python
def test_manifest_includes_prompts_and_validation_and_records_gitless_state(tmp_path):
    result = build_manifest(["app", "config", "tests", "docs", "prompts", "validation/international_media"], ["**/sha256_manifest*.json"], tmp_path / "handoff.json")
    assert result["git_state"] == "absent"
    assert "prompts" in result["included_roots"]
    assert "validation/international_media" in result["included_roots"]
    assert all("sha256_manifest" not in item["path"] for item in result["files"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/international/test_sha256_manifest.py::test_manifest_includes_prompts_and_validation_and_records_gitless_state`

Expected: FAIL because `build_manifest` and the manifest module are not defined.

- [ ] **Step 3: Write minimal implementation**

Implement deterministic sorted traversal, SHA-256 hashing, missing-root recording, exclusion matching, output refusal when the output already exists, and explicit `git_state="absent"`; do not create `.git`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/international/test_sha256_manifest.py`

Expected: PASS with the manifest containing source files, every `prompts/` file, and final validation inputs when present.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app config tests docs prompts validation/international_media --exclude '**/sha256_manifest*.json' --output validation/international_media/sha256_manifest_no_git.json
Set-Content validation/international_media/handoff_task_0_1.txt 'git=absent'
```

### Task 0.2: Create the isolated run contract

**Files:**

- Create: `validation/international_media/run_isolated.ps1`
- Create: `validation/international_media/run_isolated.py`
- Create: `validation/international_media/isolated_run_schema.json`
- Test: `tests/international/test_isolated_run_guards.py`

**Interfaces:**

- Consumes: source config path, SQLite path, reports path, `DISABLE_FEISHU_SEND=true`.
- Produces: `validation.international_media.run_isolated::validate_isolation_config(config_path, db_path, reports_path, dry_run) -> IsolationValidation`; `validation.international_media.run_isolated::run_isolated_collection(config, db, reports_path, dry_run=True) -> IsolatedCollectionResult`; `validation.international_media.run_isolated::load_runs(first_path, second_path) -> tuple[RunResult,RunResult]`; and per-source result objects with `fetched,parsed,inserted,fresh,relevant,important,errors`; never writes production DB or calls real Feishu. Wave 0 defines `IsolationValidation(ok: bool, real_feishu_send: bool, reason: str)`, `IsolatedCollectionResult(taiwan_sources_completed: bool, failed_sources: list[str], notification_candidates: list[dict], per_source: dict[str,dict])`, and `RunResult(run_id: str, inserted: int, duplicate_word_items: int, real_feishu_calls: int, per_source: dict[str,dict])`. `run_isolated.ps1` only forwards these arguments to the Python module and returns its exit code.

- [ ] **Step 1: Write the failing test**

```python
def test_isolated_runner_requires_nonproduction_paths_and_dry_run():
    result = validate_isolation_config("validation/international_media/config.yaml", "validation/international_media/news.db", "validation/international_media/reports", True)
    assert result.ok is True
    assert result.real_feishu_send is False

def test_load_runs_is_owned_by_wave0_python_module(tmp_path):
    from validation.international_media.run_isolated import load_runs
    first_path = tmp_path / "run_1.json"
    second_path = tmp_path / "run_2.json"
    first_path.write_text('{"run_id": "one"}')
    second_path.write_text('{"run_id": "two"}')
    first, second = load_runs(first_path, second_path)
    assert first.run_id != second.run_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/international/test_isolated_run_guards.py::test_isolated_runner_requires_nonproduction_paths_and_dry_run`

Expected: FAIL because the isolation validator, `load_runs`, and schema do not exist.

- [ ] **Step 3: Write minimal implementation**

Implement path containment checks under `validation/international_media/`, a required dry-run flag, the result schema, `validate_isolation_config`, `run_isolated_collection`, and `load_runs` in `run_isolated.py`; make PowerShell a thin argument-forwarding wrapper with no duplicate collection logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/international/test_isolated_run_guards.py`

Expected: PASS; the Python module exposes `load_runs`, a production DB path, missing dry-run, Scheduler install, or real Feishu flag is rejected.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include validation/international_media/run_isolated.py validation/international_media/run_isolated.ps1 validation/international_media/isolated_run_schema.json tests/international/test_isolated_run_guards.py --output validation/international_media/handoff_task_0_2_sha256.json
Set-Content validation/international_media/handoff_task_0_2.txt 'git=absent'
```

## Wave 1 — Newsletter Core

### Task 1.1: Define Newsletter models and source policy

**Files:**

- Create: `app/newsletter_ingestion/__init__.py`
- Create: `app/newsletter_ingestion/models.py`
- Create: `app/newsletter_ingestion/policy.py`
- Test: `tests/newsletter_ingestion/test_models_policy.py`

**Interfaces:**

- Consumes: raw message metadata, sender/domain, label, media source config.
- Produces: Wave 1 defines `NewsletterMessage(message_id: str, sender: str, received_at: datetime | None, subject: str, html: str | None, text: str | None, label: str)`, `NewsletterItem(item_id: str, source_id: str, title: str, url: str, summary: str | None, published_at: datetime | None)`, `SourcePolicy(label: str, allowed_domains: set[str], source_id: str | None = None)`, and `PolicyDecision(accepted: bool, reason: str, source_id: str | None)`; `SourcePolicy.check(label, sender) -> PolicyDecision`.

- [ ] **Step 1: Write the failing test**

```python
def test_policy_accepts_label_and_sender_allowlist_only():
    decision = SourcePolicy(label="InternationalNews", allowed_domains={"reuters.com"}).check("InternationalNews", "news@reuters.com")
    assert decision.accepted is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/newsletter_ingestion/test_models_policy.py`

Expected: FAIL because the new package and policy types do not exist.

- [ ] **Step 3: Write minimal implementation**

Add slots dataclasses with explicit nullable publish time, stable source/message identifiers, and policy rejection reasons; do not store secrets or body fetch instructions.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/newsletter_ingestion/test_models_policy.py tests/test_newsletter.py`

Expected: PASS with old newsletter tests unchanged.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/newsletter_ingestion/__init__.py app/newsletter_ingestion/models.py app/newsletter_ingestion/policy.py tests/newsletter_ingestion/test_models_policy.py --output validation/international_media/handoff_task_1_1_sha256.json
Set-Content validation/international_media/handoff_task_1_1.txt 'git=absent'
```

### Task 1.2: Implement offline HTML, text, EML, multipart and URL parsing

**Files:**

- Create: `app/newsletter_ingestion/parser.py`
- Create: `app/newsletter_ingestion/url_policy.py`
- Test: `tests/newsletter_ingestion/test_parser.py`
- Test: `tests/newsletter_ingestion/test_url_policy.py`
- Create: `tests/fixtures/international/newsletters/` with 32 minimum payloads, eight classes per source

**Interfaces:**

- Consumes: `NewsletterMessage` payload in HTML/plain/EML/multipart form.
- Produces: `parse_message(message) -> list[NewsletterItem]`; `normalize_tracking_url(url) -> str`; no network calls.

- [ ] **Step 1: Write the failing test**

```python
def test_parse_eml_returns_two_articles_and_removes_tracking_parameters(fixture):
    items = parse_message(fixture("wsj_multi_tracking.eml"))
    assert len(items) == 2
    assert all("utm_" not in item.url and "fbclid" not in item.url for item in items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/newsletter_ingestion/test_parser.py::test_parse_eml_returns_two_articles_and_removes_tracking_parameters`

Expected: FAIL because parser and fixture loader are absent.

- [ ] **Step 3: Write minimal implementation**

Parse headings/links and plain URL blocks, decode multipart parts, assign email date or `None`, normalize tracking parameters, reject non-HTTP(S)/private redirects, and deduplicate normalized URLs while preserving first occurrence. Parser must not import an HTTP client.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/newsletter_ingestion/test_parser.py tests/newsletter_ingestion/test_url_policy.py`

Expected: PASS for all 32 fixture payloads, including missing summary/time and HTML structure changes.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/newsletter_ingestion/parser.py app/newsletter_ingestion/url_policy.py tests/newsletter_ingestion/test_parser.py tests/newsletter_ingestion/test_url_policy.py tests/fixtures/international/newsletters --output validation/international_media/handoff_task_1_2_sha256.json
Set-Content validation/international_media/handoff_task_1_2.txt 'git=absent'
```

### Task 1.3: Convert Newsletter items into Article and preserve compatibility

**Files:**

- Create: `app/newsletter_ingestion/collector.py`
- Modify: `app/newsletter.py`
- Test: `tests/newsletter_ingestion/test_collector.py`
- Test: `tests/test_newsletter.py`

**Interfaces:**

- Consumes: `parse_message`, source config, `NewsletterItem`.
- Produces: Wave 1 defines `NewsletterCollector.collect(message: NewsletterMessage) -> list[Article]`; each existing `Article` carries `source_id`, `source_name`, `category=international`, `language=en`, `access_level=newsletter`, and `summary_source=newsletter` when teaser exists.

- [ ] **Step 1: Write the failing test**

```python
def test_newsletter_collector_sets_existing_article_fields():
    articles = NewsletterCollector(SOURCE).collect(NEWSLETTER_MESSAGE)
    assert articles[0].access_level == "newsletter"
    assert articles[0].language == "en"
    assert articles[0].source_id == "wsj_newsletter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/newsletter_ingestion/test_collector.py::test_newsletter_collector_sets_existing_article_fields`

Expected: FAIL because `NewsletterCollector` is not implemented.

- [ ] **Step 3: Write minimal implementation**

Implement source-aware conversion and adapter dispatch for WSJ/Bloomberg without fetching any Article URL; retain old `parse_newsletter`, `NewsletterParser`, and adapter imports through `app/newsletter.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/newsletter_ingestion/test_collector.py tests/test_newsletter.py`

Expected: PASS with backward-compatible old fixtures and exact Article fields.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/newsletter_ingestion/collector.py app/newsletter.py tests/newsletter_ingestion/test_collector.py tests/test_newsletter.py --output validation/international_media/handoff_task_1_3_sha256.json
Set-Content validation/international_media/handoff_task_1_3.txt 'git=absent'
```

## Wave 2 — Gmail Mailbox and Source Verification

### Task 2.1: Add official Gmail dependencies and clean-install contract

**Files:**

- Modify: `requirements.txt`
- Create: `validation/international_media/dependency_versions.json`
- Test: `tests/newsletter_ingestion/test_dependency_contract.py`

**Interfaces:**

- Consumes: Python 3 environment and requirements file.
- Produces: bounded dependency ranges and resolved version/hash record; no runtime installation.

- [ ] **Step 1: Write the failing test**

```python
def test_gmail_dependencies_have_bounded_official_ranges():
    text = Path("requirements.txt").read_text()
    assert "google-api-python-client>=2.170,<3" in text
    assert "google-auth>=2.35,<3" in text
    assert "google-auth-httplib2>=0.2,<1" in text
    assert "google-auth-oauthlib>=1.2,<2" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/newsletter_ingestion/test_dependency_contract.py`

Expected: FAIL because the four Gmail dependencies are not declared.

- [ ] **Step 3: Write minimal implementation**

Add only the four bounded official packages to `requirements.txt`; create a version recorder that writes resolved versions and hashes to `dependency_versions.json` after clean installation.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m venv .venv-clean-newsletter; .venv-clean-newsletter\Scripts\python -m pip install -r requirements.txt; .venv-clean-newsletter\Scripts\python -m pytest -q tests/newsletter_ingestion/test_dependency_contract.py`

Expected: PASS and a dependency record exists; no OAuth or Feishu call occurs.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include requirements.txt validation/international_media/dependency_versions.json tests/newsletter_ingestion/test_dependency_contract.py --output validation/international_media/handoff_task_2_1_sha256.json
Set-Content validation/international_media/handoff_task_2_1.txt 'git=absent'
```

### Task 2.2: Implement Gmail readonly mailbox and OAuth boundary

**Files:**

- Create: `app/newsletter_ingestion/mailbox.py`
- Create: `app/newsletter_ingestion/gmail_client.py`
- Create: `app/newsletter_ingestion/oauth.py`
- Test: `tests/newsletter_ingestion/test_mailbox_contract.py`
- Test: `tests/newsletter_ingestion/test_gmail_client.py`
- Test: `tests/newsletter_ingestion/test_oauth.py`

**Interfaces:**

- Consumes: `SourcePolicy`, Google credentials outside the project, label `InternationalNews`.
- Produces: `app.newsletter_ingestion.oauth::GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"`, approved `SCOPE_PROVENANCE_VALUES = {None, "authorized_user_file"}`, and `AuthContext(credentials_path: Path | None, token_path: Path | None, authorized: bool, reason: str, scope: str | None, scope_provenance: str | None)`. `load_auth_context(credentials_path: Path | None, token_path: Path | None) -> AuthContext` validates that an authorized context has exactly `scope=GMAIL_READONLY_SCOPE` and `scope_provenance="authorized_user_file"`; an unauthorized context has `scope=None` and `scope_provenance=None`. AuthContext is a dataclass with only these six non-secret fields: never secret/token values, client-secret contents, access tokens, refresh tokens, or service objects. Wave 2 also defines `MailboxClient` as a protocol with `list_messages(label: str, sender_allowlist: set[str], since: str) -> list[NewsletterMessage]` and `GmailMailboxClient(service: object, label: str, modify: bool, auth: AuthContext | None)` implementing that method with Gmail readonly scope and fake-client injection; missing or unauthorized auth returns `MAILBOX_AUTH_REQUIRED`.

- [ ] **Step 1: Write the failing test**

```python
def test_gmail_client_reads_only_label_and_never_mutates_mailbox(fake_service):
    import pytest
    from app.newsletter_ingestion.oauth import AuthContext, GMAIL_READONLY_SCOPE
    auth = AuthContext(credentials_path=None, token_path=None, authorized=False, reason="MAILBOX_AUTH_REQUIRED", scope=None, scope_provenance=None)
    client = GmailMailboxClient(service=fake_service, label="InternationalNews", modify=False, auth=auth)
    messages = client.list_messages(label="InternationalNews", sender_allowlist={"reuters.com"}, since="30d")
    assert messages == []
    assert auth.reason == "MAILBOX_AUTH_REQUIRED"
    assert auth.scope is None and auth.scope_provenance is None
    assert fake_service.modify_calls == []
    assert fake_service.delete_calls == []

def test_auth_context_rejects_nonreadonly_scope_or_unapproved_provenance():
    import pytest
    from app.newsletter_ingestion.oauth import AuthContext, GMAIL_READONLY_SCOPE
    valid = AuthContext(None, None, True, "authorized", GMAIL_READONLY_SCOPE, "authorized_user_file")
    assert valid.scope == GMAIL_READONLY_SCOPE
    assert valid.scope_provenance == "authorized_user_file"
    with pytest.raises(ValueError):
        AuthContext(None, None, True, "authorized", "gmail.modify", "authorized_user_file")
    with pytest.raises(ValueError):
        AuthContext(None, None, True, "authorized", GMAIL_READONLY_SCOPE, "runtime_service_object")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/newsletter_ingestion/test_gmail_client.py::test_gmail_client_reads_only_label_and_never_mutates_mailbox`

Expected: FAIL because Gmail client and readonly fake service contract are absent.

- [ ] **Step 3: Write minimal implementation**

Define `GMAIL_READONLY_SCOPE`, the approved `SCOPE_PROVENANCE_VALUES`, the six-field `AuthContext` dataclass, and `load_auth_context(credentials_path, token_path)` in `oauth.py`; validate exact readonly scope/provenance combinations and never include secret values, token contents, client-secret contents or service objects. Use official Google client discovery with injected service, pass the Wave 2-owned `AuthContext` into the Gmail client, request only readonly message metadata/content for the configured label, enforce sender/domain policy and size/time limits, and keep OAuth token loading outside the project. Do not call modify/delete/reply/send APIs or place secret values in the context, logs, fixtures or reports.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-clean-newsletter\Scripts\python -c "import googleapiclient.discovery, google.oauth2.credentials, google_auth_httplib2, google_auth_oauthlib.flow; import app.newsletter_ingestion.gmail_client"; .venv-clean-newsletter\Scripts\python -m pytest -q tests/newsletter_ingestion/test_mailbox_contract.py tests/newsletter_ingestion/test_gmail_client.py tests/newsletter_ingestion/test_oauth.py`

Expected: PASS without OAuth; unauthorized fake flow returns `MAILBOX_AUTH_REQUIRED` and no mailbox mutation is observed.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/newsletter_ingestion/mailbox.py app/newsletter_ingestion/gmail_client.py app/newsletter_ingestion/oauth.py tests/newsletter_ingestion/test_mailbox_contract.py tests/newsletter_ingestion/test_gmail_client.py tests/newsletter_ingestion/test_oauth.py --output validation/international_media/handoff_task_2_2_sha256.json
Set-Content validation/international_media/handoff_task_2_2.txt 'git=absent'
```

### Task 2.3: Implement separate public, Gmail and summary verification evidence

**Files:**

- Create: `app/newsletter_ingestion/verify_sources.py`
- Create: `tests/test_newsletter_source_verification.py`
- Create: `validation/international_media/newsletter_availability/{reuters_international,ft_alphaville,wsj_newsletter,bloomberg_newsletter}_{public,gmail,summary}_YYYY-MM-DD.json`
- Create: `validation/international_media/newsletter_availability/newsletter_live_verification_manifest.json`

**Interfaces:**

- Consumes: source ID, public page or Gmail client, explicit `auth: app.newsletter_ingestion.oauth.AuthContext | None` handed off by Task 2.2, as-of date, output path; it must use the same six-field type, accept only `scope=None` for unauthenticated evidence or `scope=GMAIL_READONLY_SCOPE` with `scope_provenance="authorized_user_file"` for authorized evidence, and must not reconstruct or serialize secret values.
- Produces: `run_verification(mode, source_id, output_path, auth=None, public_evidence=None, gmail_evidence=None) -> dict`; CLI `--mode public` → `*_public_YYYY-MM-DD.json`; `--mode gmail` → `*_gmail_YYYY-MM-DD.json`; `--mode summary` → `*_summary_YYYY-MM-DD.json`; each mode refuses existing output and public/gmail path collisions. A missing Gmail auth object produces `MAILBOX_AUTH_REQUIRED` without contacting Gmail.

- [ ] **Step 1: Write the failing test**

```python
def test_public_gmail_summary_are_independent_and_never_overwrite(tmp_path):
    import pytest
    public = run_verification("public", "wsj_newsletter", tmp_path / "public.json")
    gmail = run_verification("gmail", "wsj_newsletter", tmp_path / "gmail.json", auth=None)
    summary = run_verification("summary", "wsj_newsletter", tmp_path / "summary.json", public_evidence=public, gmail_evidence=gmail)
    assert public["status"] == "verified"
    assert gmail["reason"] == "MAILBOX_AUTH_REQUIRED"
    assert summary["status"] == "operator_action_required"
    with pytest.raises(FileExistsError):
        run_verification("public", "wsj_newsletter", tmp_path / "public.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_newsletter_source_verification.py::test_public_gmail_summary_are_independent_and_never_overwrite`

Expected: FAIL because the three modes and evidence writer do not exist.

- [ ] **Step 3: Write minimal implementation**

Implement mode-specific schemas with `source_id`, `verification_date`, `auth_state`, observed sender/name, counts, errors, verifier, and evidence SHA-256; make summary read-only over two input files and refuse any existing output. A summary is verified only when both inputs are verified.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_newsletter_source_verification.py`

Expected: PASS for public verification, unauthenticated Gmail status, summary merge, path collision, and no-overwrite behavior.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/newsletter_ingestion/verify_sources.py tests/test_newsletter_source_verification.py validation/international_media/newsletter_availability --output validation/international_media/handoff_task_2_3_sha256.json
Set-Content validation/international_media/handoff_task_2_3.txt 'git=absent'
```

### Task 2.4: Write the Newsletter operator guide

**Files:**

- Create: `docs/INTERNATIONAL_NEWSLETTER_OPERATOR_GUIDE.md`
- Test: `tests/newsletter_ingestion/test_operator_guide_contract.py`

**Interfaces:**

- Consumes: OAuth boundary, source IDs, public/gmail/summary commands.
- Produces: operator instructions that distinguish `operator_action_required` from verified and never expose credentials.

- [ ] **Step 1: Write the failing test**

```python
def test_operator_guide_has_readonly_scope_label_and_no_secret_examples():
    text = Path("docs/INTERNATIONAL_NEWSLETTER_OPERATOR_GUIDE.md").read_text()
    assert "InternationalNews" in text and "readonly" in text
    assert "MAILBOX_AUTH_REQUIRED" in text
    assert "FEISHU_APP_SECRET=" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/newsletter_ingestion/test_operator_guide_contract.py`

Expected: FAIL because the guide and test contract are absent.

- [ ] **Step 3: Write minimal implementation**

Document OAuth one-time action, label/sender setup, separate evidence commands, stop conditions, token location policy, no mailbox mutation, and disable/rollback steps without including credentials.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/newsletter_ingestion/test_operator_guide_contract.py`

Expected: PASS and the guide identifies no-auth state as operator action rather than verified.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include docs/INTERNATIONAL_NEWSLETTER_OPERATOR_GUIDE.md tests/newsletter_ingestion/test_operator_guide_contract.py --output validation/international_media/handoff_task_2_4_sha256.json
Set-Content validation/international_media/handoff_task_2_4.txt 'git=absent'
```

## Wave 3 — Sources, Configuration and Health

### Task 3.1: Add exact source schema and collector registration

**Files:**

- Modify: `config/sources.yaml`
- Modify: `config/international_media.yaml`
- Modify: `app/main.py`
- Create: `app/collectors/wsj_newsletter.py`
- Create: `app/collectors/bloomberg_newsletter.py`
- Test: `tests/test_international_collection_wiring.py`
- Test: `tests/test_international_config.py`
- Test: `tests/test_wsj_newsletter.py`
- Test: `tests/test_bloomberg_newsletter.py`

**Interfaces:**

- Consumes: Newsletter collector from Wave 1, mailbox from Wave 2, existing `COLLECTOR_MAP` and `validate_sources_config`.
- Produces: `app.main::load_sources(path) -> list[dict]`, exact IDs `reuters_international`, `ft_alphaville`, `wsj_newsletter`, `bloomberg_newsletter`, four independent disabled entries, and frozen `wsj_international`/`wsj_rss` disabled entry.

- [ ] **Step 1: Write the failing test**

```python
def test_four_exact_international_ids_are_independent_and_disabled():
    ids = {source["id"]: source for source in load_sources()}
    assert ids["reuters_international"]["enabled"] is False
    assert ids["ft_alphaville"]["enabled"] is False
    assert ids["wsj_newsletter"]["enabled"] is False
    assert ids["bloomberg_newsletter"]["enabled"] is False
    assert ids["wsj_international"]["enabled"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_collection_wiring.py::test_four_exact_international_ids_are_independent_and_disabled`

Expected: FAIL because the Newsletter source IDs/types are not registered.

- [ ] **Step 3: Write minimal implementation**

Add the four source mappings with required `id/name/type/category/url/enabled`, register Newsletter collector types, preserve old WSJ RSS disabled, and keep `international_media.yaml` fail-closed when missing or malformed.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_collection_wiring.py tests/test_international_config.py tests/test_wsj_newsletter.py tests/test_bloomberg_newsletter.py tests/test_config_validation.py`

Expected: PASS; configuration validation rejects duplicate IDs, missing URL/type, string `enabled`, unknown types, and any enabled production source in the release config.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include config/sources.yaml config/international_media.yaml app/main.py app/collectors/wsj_newsletter.py app/collectors/bloomberg_newsletter.py tests/test_international_collection_wiring.py tests/test_international_config.py tests/test_wsj_newsletter.py tests/test_bloomberg_newsletter.py --output validation/international_media/handoff_task_3_1_sha256.json
Set-Content validation/international_media/handoff_task_3_1.txt 'git=absent'
```

### Task 3.2: Add bounded retry and Source Health sidecar

**Files:**

- Modify: `app/collectors/base.py`
- Modify: `app/collectors/reuters.py`
- Modify: `app/collectors/ft_alphaville.py`
- Create: `app/source_health.py`
- Test: `tests/test_source_health.py`
- Test: `tests/test_reuters.py`
- Test: `tests/test_ft_alphaville.py`

**Interfaces:**

- Consumes: collector source config and HTTP response/error events.
- Produces: Wave 3 defines `SourceOutcome(http_status: int, schema_valid: bool, item_count: int)`, `ValidEmptyFeed(http_status=200, schema_valid=True, item_count=0)` as a test fixture, `HealthRecord(source_id: str, status: Literal["healthy","degraded","stale","broken","disabled"], last_success: datetime | None, last_item_at: datetime | None, items_fetched: int, parse_errors: int, consecutive_failures: int)`, and `SourceHealthStore(path: Path)` with `update(source_id: str, outcome: SourceOutcome) -> HealthRecord` and `get(source_id: str) -> HealthRecord`; statuses use atomic sidecar writes.

- [ ] **Step 1: Write the failing test**

```python
def test_valid_empty_feed_is_success_then_stale_after_three_runs(tmp_path):
    store = SourceHealthStore(tmp_path / "health.json")
    for _ in range(3):
        store.update("reuters_international", outcome=ValidEmptyFeed())
    assert store.get("reuters_international").status == "stale"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_source_health.py::test_valid_empty_feed_is_success_then_stale_after_three_runs`

Expected: FAIL because Source Health state transitions and atomic writes do not exist.

- [ ] **Step 3: Write minimal implementation**

Add connect/read timeout, finite retry/backoff, max response size, User-Agent, schema checks and atomic health JSON. Treat valid HTTP 200 schema with zero items as success; three valid zero-item runs or 48 hours since last item is stale; parse/required-node failure is degraded and three consecutive failures is broken.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_source_health.py tests/test_reuters.py tests/test_ft_alphaville.py`

Expected: PASS for timeout/403/empty/structure-change behavior, retry bounds, no body fetch for metadata-only, and existing Reuters/FT fixture behavior.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/collectors/base.py app/collectors/reuters.py app/collectors/ft_alphaville.py app/source_health.py tests/test_source_health.py tests/test_reuters.py tests/test_ft_alphaville.py --output validation/international_media/handoff_task_3_2_sha256.json
Set-Content validation/international_media/handoff_task_3_2.txt 'git=absent'
```

### Task 3.3: Connect per-source collection and first-run baseline

**Files:**

- Modify: `app/main.py`
- Test: `tests/test_catchup_main.py`
- Test: `tests/test_international_collection_wiring.py`

**Interfaces:**

- Consumes: four source mappings, Source Health store, existing `collect_all`, freshness and baseline functions.
- Produces: `validation.international_media.run_isolated::run_isolated_collection(config, db, reports_path, dry_run=True) -> IsolatedCollectionResult`; source-isolated collection; baseline recorded before delivery; failures do not stop Taiwan collection; no production DB or Scheduler mutation.

- [ ] **Step 1: Write the failing test**

```python
def test_international_source_failure_does_not_stop_taiwan_or_delivery(tmp_path):
    result = run_isolated_collection(config=ISOLATED_CONFIG, db=tmp_path / "news.db", reports_path=tmp_path / "reports", dry_run=True)
    assert result.taiwan_sources_completed is True
    assert result.failed_sources == ["reuters_international"]
    assert result.notification_candidates == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_collection_wiring.py::test_international_source_failure_does_not_stop_taiwan_or_delivery`

Expected: FAIL because Newsletter source isolation and baseline-aware delivery are not wired.

- [ ] **Step 3: Write minimal implementation**

Keep `validate_sources_config` before network/DB/Word/Feishu, catch failures per source, record baseline before new source articles enter delivery, and pass only isolated Article results onward.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_collection_wiring.py tests/test_catchup_main.py`

Expected: PASS; old Taiwan collectors continue after an international error and first-run catch-up is stored but not delivered.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/main.py tests/test_international_collection_wiring.py tests/test_catchup_main.py --output validation/international_media/handoff_task_3_3_sha256.json
Set-Content validation/international_media/handoff_task_3_3.txt 'git=absent'
```

## Wave 4 — Relevance, Importance and Goldens

### Task 4.1: Implement explainable RelevanceDecision

**Files:**

- Modify: `app/international.py`
- Test: `tests/test_international.py`
- Create: `tests/test_international_relevance_golden.py`
- Create: `tests/fixtures/international/golden_relevance.jsonl`

**Interfaces:**

- Consumes: title, teaser/summary, source metadata and `config/international_media.yaml`.
- Produces: `app.international::evaluate_relevance(title, summary, source_name, config) -> RelevanceDecision(relevant,tier,topics,entities,reason,rule_version,input_hash)` while preserving `classify_international` compatibility as a projection.

- [ ] **Step 1: Write the failing test**

```python
def test_plain_china_keyword_without_context_is_excluded():
    decision = evaluate_relevance("China restaurant expands", "Local dining story", "Reuters", CONFIG)
    assert decision.relevant is False
    assert "context" in decision.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_relevance_golden.py::test_plain_china_keyword_without_context_is_excluded`

Expected: FAIL because the explainable decision object and context reason are absent.

- [ ] **Step 3: Write minimal implementation**

Apply direct Taiwan rule, China-plus-context rule, US/international-plus-Taiwan-or-China rule, topic/entity extraction and deterministic reason/version/hash output; do not read article bodies.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international.py tests/test_international_relevance_golden.py`

Expected: PASS for direct positives and hard negatives including China restaurant, Washington local, Taiwan Semiconductor ambiguity, generic semiconductor, Pentagon personnel and Japan domestic politics.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/international.py tests/test_international.py tests/test_international_relevance_golden.py tests/fixtures/international/golden_relevance.jsonl --output validation/international_media/handoff_task_4_1_sha256.json
Set-Content validation/international_media/handoff_task_4_1.txt 'git=absent'
```

### Task 4.2: Freeze importance thresholds and tier-1 negative control

**Files:**

- Modify: `config/importance_rules.yaml` only if required by the existing schema
- Modify: `app/importance.py` only if required by the existing API
- Test: `tests/test_importance_tier1.py` (existing tier-1 regression; Wave 4 owns execution, modify only if the API contract requires it)
- Create: `tests/fixtures/international/golden_importance.jsonl`
- Create: `tests/test_international_importance_golden.py`

**Interfaces:**

- Consumes: Article and existing importance rules.
- Produces: Wave 4 defines `ImportanceResult(score: int, level: Literal["normal","important","critical"], reasons: list[str])` through the existing `app/importance.py` API, with important threshold 65, critical threshold 85, and tier-1 bonus 3.

- [ ] **Step 1: Write the failing test**

```python
def test_tier1_bonus_alone_does_not_make_reuters_important():
    result = score_article("Ordinary Reuters market note", "Reuters", "international", "", RULES)
    assert result.score < 65
    assert result.level == "normal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_importance_golden.py::test_tier1_bonus_alone_does_not_make_reuters_important`

Expected: FAIL if the source bonus bypasses the normal/important threshold.

- [ ] **Step 3: Write minimal implementation**

Preserve existing scoring, ensure Tier-1 bonus ≤ official bonus, and prevent source name alone from crossing 65. Do not alter unrelated Taiwan rules.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_importance_tier1.py tests/test_international_importance_golden.py`

Expected: PASS with level exact accuracy≥0.90 and important/critical precision≥0.90 across the minimum 32 gold rows.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/importance.py config/importance_rules.yaml tests/test_importance_tier1.py tests/test_international_importance_golden.py tests/fixtures/international/golden_importance.jsonl --output validation/international_media/handoff_task_4_2_sha256.json
Set-Content validation/international_media/handoff_task_4_2.txt 'git=absent'
```

### Task 4.3: Add quantitative golden runner and event pair labels

**Files:**

- Create: `tests/fixtures/international/golden_events.jsonl`
- Create: `tests/test_international_golden_metrics.py`
- Create: `validation/international_media/golden_metrics.py`

**Interfaces:**

- Consumes: 32 relevance/importance rows and 12 event pair labels with `expected_*` fields, including an explicit four-Article/four-source coverage fixture; Wave 4 does not load or fallback-implement `app.international_events`.
- Produces: Wave 4 defines `GoldRelevanceMetrics(total: int, precision: float, recall: float, hard_negative_fp: int, tier_exact: float, topic_exact: float, entity_exact: float)`, `GoldImportanceMetrics(level_accuracy: float, important_critical_precision: float)`, `GoldEventMetrics(status: Literal["pending_wave5","pass","fail"], counted: bool, total_pairs: int, pair_precision: float | None, pair_recall: float | None, cluster_exact: bool, canonical_exact: bool, coverage_exact: bool)`, and `GoldReport(relevance: GoldRelevanceMetrics, importance: GoldImportanceMetrics, events: GoldEventMetrics, minimum_counts_pass: bool, rc_eligible: bool)` in `validation/international_media/golden_metrics.py`; `evaluate_gold(root, event_cluster_module=None) -> GoldReport` calculates relevance/importance metrics, but returns `events.status="pending_wave5"`, `events.counted=false`, `events.pair_precision=None`, `events.pair_recall=None`, and `rc_eligible=false` when `event_cluster_module` is absent or `app.international_events` is unavailable. Wave 4 must not use a fallback clusterer or claim production event precision/recall.

- [ ] **Step 1: Write the failing test**

```python
def test_minimum_gold_counts_and_metrics_are_enforced():
    report = evaluate_gold("tests/fixtures/international")
    assert report.relevance.total >= 32
    assert report.events.total_pairs >= 12
    assert report.relevance.precision >= 0.95
    assert report.relevance.recall >= 0.90
    assert report.relevance.hard_negative_fp == 0
    assert report.events.status == "pending_wave5"
    assert report.events.counted is False
    assert report.events.pair_precision is None
    assert report.events.pair_recall is None
    assert report.rc_eligible is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_golden_metrics.py::test_minimum_gold_counts_and_metrics_are_enforced`

Expected: FAIL because the minimum corpus and metric runner are absent; after the minimal Wave 4 implementation, the event portion must remain explicitly `pending_wave5` rather than passing through a fallback.

- [ ] **Step 3: Write minimal implementation**

Create 32 labeled rows with exact `expected_relevant,expected_tier,expected_topics,expected_entities,expected_importance_level,expected_min_score,expected_notification,expected_cluster_id,expected_is_canonical,expected_coverage_source_ids,expected_reason_contains,body_fetch_forbidden`; create 12 labeled pair records with `expected_pair_merge`, plus a separate four-input coverage record containing four distinct Article inputs and source IDs `reuters_international`, `ft_alphaville`, `wsj_newsletter`, `bloomberg_newsletter`; calculate relevance/importance TP/FP/FN with nonzero-denominator checks. When `event_cluster_module is None` or `app.international_events` is unavailable, emit `pending_wave5` and do not calculate or report event precision/recall; do not use a fallback clusterer.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_golden_metrics.py; python validation/international_media/golden_metrics.py --fixtures tests/fixtures/international --output validation/international_media/golden_metrics.json`

Expected: PASS for Wave 4 corpus/relevance/importance gates while event status is `pending_wave5` and `rc_eligible` is false; event pair precision/recall and cluster/canonical/coverage exactness are not counted until Wave 5 supplies the real EventCluster module.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

Record that `golden_metrics.py` transfers to Wave 5 only after this manifest and `git=absent` handoff; Wave 5 must generate a new SHA-256 before modifying the runner.

```powershell
python validation/international_media/build_sha256_manifest.py --include tests/fixtures/international/golden_relevance.jsonl tests/fixtures/international/golden_importance.jsonl tests/fixtures/international/golden_events.jsonl tests/test_international_golden_metrics.py validation/international_media/golden_metrics.py validation/international_media/golden_metrics.json --output validation/international_media/handoff_task_4_3_sha256.json
Set-Content validation/international_media/handoff_task_4_3.txt 'git=absent'
```

## Wave 5 — Event Delivery, Translation and Word

### Task 5.1: Implement in-memory EventCluster and coverage

**Files:**

- Create: `app/international_events.py`
- Create: `tests/test_international_events.py`
- Modify: `validation/international_media/golden_metrics.py` only after Wave 4 `handoff_task_4_3_sha256.json` and `git=absent` handoff; Wave 5 becomes its sole owner
- Create: `tests/test_international_event_metrics_integration.py`
- Read-only dependency: `tests/test_international_golden_metrics.py` (Wave 4 pending-state contract; no Wave 5 edits)
- Read-only dependency: `tests/test_international.py` (run existing compatibility assertions; Wave 4 remains its sole edit owner)

**Interfaces:**

- Consumes: relevant Article list, published times, normalized title tokens, entities, event terms and config window.
- Produces: `cluster_international_articles(articles: list[Article], config) -> tuple[list[EventCluster], dict[str,list[Article]]]`; `EventCluster(event_id: str, canonical: Article, members: list[Article], coverage: list[Article], topics: list[str], time_window: tuple[datetime,datetime])`; and, after the Wave 4 handoff, `evaluate_gold(root, event_cluster_module=app.international_events) -> GoldReport` with `events.status="pass"`, `counted=true`, pair precision/recall `1.00`, and exact cluster ID, canonical URL, and coverage source IDs.

- [ ] **Step 1: Write the failing test**

```python
def test_four_sources_same_event_make_one_canonical_and_full_coverage():
    assert len(FOUR_SOURCE_EVENT) == 4
    assert {article.source_id for article in FOUR_SOURCE_EVENT} == {"reuters_international", "ft_alphaville", "wsj_newsletter", "bloomberg_newsletter"}
    clusters = cluster_international_articles(FOUR_SOURCE_EVENT, CONFIG)
    assert len(clusters) == 1
    assert len(clusters[0].members) == 4
    assert clusters[0].canonical.source_name == "Reuters"
    assert {a.source_id for a in clusters[0].coverage} == {"reuters_international", "ft_alphaville", "wsj_newsletter", "bloomberg_newsletter"}

def test_wave5_event_metrics_use_real_clusterer_and_four_article_coverage():
    import app.international_events
    from validation.international_media.golden_metrics import evaluate_gold
    report = evaluate_gold("tests/fixtures/international", event_cluster_module=app.international_events)
    assert report.events.status == "pass"
    assert report.events.counted is True
    assert report.events.pair_precision == 1.0
    assert report.events.pair_recall == 1.0
    assert report.events.cluster_exact is True
    assert report.events.canonical_exact is True
    assert report.events.coverage_exact is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_events.py::test_four_sources_same_event_make_one_canonical_and_full_coverage`

Expected: FAIL because EventCluster and coverage functions are absent.

- [ ] **Step 3: Write minimal implementation**

Use URL identity, normalized title similarity, shared core concepts and a 24-hour window; keep unrelated pairs separate, keep cross-day major follow-ups separate, choose canonical deterministically, and never write a new DB table. After verifying the Wave 4 handoff, extend `golden_metrics.py` in the transferred Wave 5 owner to call the real `app.international_events` module, change event status from `pending_wave5` to `pass` only after exact assertions, and never use a fallback clusterer.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_events.py tests/test_international_event_metrics_integration.py tests/test_international.py; python validation/international_media/golden_metrics.py --fixtures tests/fixtures/international --event-cluster-module app.international_events --output validation/international_media/golden_metrics.json`

Expected: PASS for six positive and six negative/follow-up pair labels, one explicit four-Article/four-source coverage case, exact cluster/canonical/coverage sets, Wave 5 event pair precision/recall 1.00, and no Taiwan non-international clustering.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

This is the Wave 5 ownership-transfer handoff for `golden_metrics.py`; the Wave 4 runner hash remains immutable and the new hash below is the only valid post-EventCluster owner record.

```powershell
python validation/international_media/build_sha256_manifest.py --include app/international_events.py validation/international_media/golden_metrics.py tests/test_international_events.py tests/test_international_event_metrics_integration.py tests/test_international.py --output validation/international_media/handoff_task_5_1_sha256.json
Set-Content validation/international_media/handoff_task_5_1.txt 'git=absent'
```

### Task 5.2: Add translator abstraction, fake translator and no-body fallback

**Files:**

- Create: `app/international_translation.py`
- Create: `tests/test_international_translation.py`
- Modify: `tests/test_word_digest.py` only for translation fields

**Interfaces:**

- Consumes: existing `Article` plus its title, source name and legal teaser/summary only; injected `InternationalNewsTranslator`.
- Produces: Wave 5 defines `InternationalNewsTranslator` as an `@runtime_checkable typing.Protocol` with the exact method `translate(self, title: str, summary: str | None, *, source_name: str) -> tuple[str, str]`; `TranslationResult(cn_title: str, cn_summary: str, status: Literal["translated","fallback"], limitation: str | None, body_fetch_count: int)` as a frozen slots dataclass; `translate_article(article: Article, translator: InternationalNewsTranslator, body_fetcher: Callable[[str], str] | None = None) -> TranslationResult`; and `FakeTranslator(InternationalNewsTranslator)` with the same `translate(...) -> tuple[str, str]` signature. `FakeTranslator` returns deterministic title/summary by default and can be configured to raise or return empty strings for fallback tests. On translator error/empty output, `translate_article` returns the Article's English title/legal teaser, `status="fallback"`, a nonempty limitation, and `body_fetch_count=0`; it never invokes `body_fetcher`.

- [ ] **Step 1: Write the failing test**

```python
def test_metadata_only_translation_never_fetches_article_body(monkeypatch):
    import inspect
    from app.international_translation import FakeTranslator, InternationalNewsTranslator, TranslationResult, translate_article
    signature = inspect.signature(FakeTranslator.translate)
    assert list(signature.parameters) == ["self", "title", "summary", "source_name"]
    assert signature.parameters["source_name"].kind is inspect.Parameter.KEYWORD_ONLY
    def fail_body_fetch(url):
        raise AssertionError("body fetch")
    result = translate_article(METADATA_ONLY_ARTICLE, translator=FakeTranslator(), body_fetcher=fail_body_fetch)
    assert isinstance(FakeTranslator(), InternationalNewsTranslator)
    assert isinstance(result, TranslationResult)
    assert result.status == "translated"
    assert result.body_fetch_count == 0

def test_translator_error_uses_english_metadata_fallback():
    result = translate_article(METADATA_ONLY_ARTICLE, translator=FakeTranslator(raise_error=True))
    assert result.status == "fallback"
    assert result.cn_title == METADATA_ONLY_ARTICLE.title
    assert result.body_fetch_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_translation.py::test_metadata_only_translation_never_fetches_article_body`

Expected: FAIL because `InternationalNewsTranslator`, `TranslationResult`, `FakeTranslator`, and the body-fetch guard are absent.

- [ ] **Step 3: Write minimal implementation**

Implement the exact `InternationalNewsTranslator.translate(title: str, summary: str | None, *, source_name: str) -> tuple[str, str]` Protocol, the compatible `TranslationResult` dataclass and `FakeTranslator` implementation; pass only Article title/source/legal teaser to the translator, catch translator errors/empty output, return English metadata with `status="fallback"` and a limitation, and expose `body_fetch_count=0` for metadata-only/newsletter paths without calling `body_fetcher`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_translation.py tests/test_word_digest.py`

Expected: PASS for success, translator failure, empty result, fact/analysis separation, 100–250 Chinese character target when content permits, and zero body fetches.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/international_translation.py tests/test_international_translation.py tests/test_word_digest.py --output validation/international_media/handoff_task_5_2_sha256.json
Set-Content validation/international_media/handoff_task_5_2.txt 'git=absent'
```

### Task 5.3: Build event-level notification_candidates

**Files:**

- Create: `app/notification_candidates.py`
- Modify: `app/main.py` after Wave 3 handoff
- Modify: `app/notifier.py`
- Create: `tests/test_notification_candidates.py`
- Test: `tests/test_notify.py` (existing notifier safety regression; Wave 5 owns assertions, no Feishu call)

**Interfaces:**

- Consumes: `EventCluster`, `ImportanceResult`, freshness/baseline state.
- Produces: Wave 5 defines `NotificationCandidate(event_id: str, canonical_url: str, cn_title: str, importance_level: Literal["important","critical"], score: int, relevance_reason: str, coverage_source_ids: list[str], coverage_urls: list[str], dedup_key: str, notifiable: bool)` in `app/notification_candidates.py`; `build_notification_candidates(clusters, importance_results, freshness_state, now) -> list[NotificationCandidate]`; `deduplicate_notification_candidates(candidates) -> list[NotificationCandidate]`; and existing `Notifier.send_event_candidates(candidates: list[NotificationCandidate]) -> None`.

- [ ] **Step 1: Write the failing test**

```python
def test_normal_relevant_is_word_only_and_important_cluster_has_one_candidate():
    candidates = build_notification_candidates([NORMAL_RELEVANT_CLUSTER, IMPORTANT_CLUSTER_WITH_FOUR_COVERAGE], RESULTS, FRESHNESS, NOW)
    assert len(candidates) == 1
    assert candidates[0].event_id == IMPORTANT_CLUSTER_WITH_FOUR_COVERAGE.event_id
    assert candidates[0].notifiable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_notification_candidates.py::test_normal_relevant_is_word_only_and_important_cluster_has_one_candidate`

Expected: FAIL because the candidate module and event notifier method do not exist.

- [ ] **Step 3: Write minimal implementation**

Filter `relevant=true`, fresh non-baseline articles and score≥65/level important or critical; build one candidate per event with `event_id,canonical_url,cn_title,importance_level,score,relevance_reason,coverage_source_ids,coverage_urls,dedup_key,notifiable`; deduplicate retries by event ID/key; keep normal relevant in Word only; route automated notifications to Recording/Null notifier.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_notification_candidates.py; python -m pytest -q tests/test_notify.py`

Expected: PASS for normal exclusion, one candidate for four-source coverage, duplicate retry suppression, baseline exclusion, and spy assertion that `app.feishu.send_document/send_card` is not called.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/notification_candidates.py app/main.py app/notifier.py tests/test_notification_candidates.py --output validation/international_media/handoff_task_5_3_sha256.json
Set-Content validation/international_media/handoff_task_5_3.txt 'git=absent'
```

### Task 5.4: Render Word international section and coverage

**Files:**

- Modify: `app/word_digest.py`
- Modify: `app/digest.py`
- Modify: `app/main.py` delivery call only after Wave 3 handoff
- Test: `tests/test_word_digest.py`
- Read-only dependency: `tests/assessment/test_word_report_renderer.py` (run its existing renderer regression; do not modify this assessment test)

**Interfaces:**

- Consumes: canonical EventClusters, `coverage`, TranslationResult and importance result.
- Produces: existing “新闻媒体” level-one section with international media Heading 2; each item has importance, Chinese title, source, English title, summary, Asia/Taipei time and URL; coverage renders once.

- [ ] **Step 1: Write the failing test**

```python
def test_word_has_international_heading_and_does_not_repeat_coverage(tmp_path):
    path = build_word_digest([CANONICAL_WITH_FOUR_COVERAGE], tmp_path, international_coverage=COVERAGE)
    text = extract_docx_text(path)
    assert text.count("國際媒體") == 1
    assert text.count("同一事件") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_word_digest.py::test_word_has_international_heading_and_does_not_repeat_coverage`

Expected: FAIL because the international Heading 2 and coverage rendering are absent.

- [ ] **Step 3: Write minimal implementation**

Add international section without changing politics/military/religion ordering, render canonical translation and source metadata, render coverage source names/links once, sort critical/important/normal then published time descending, and preserve URL hyperlinks.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_word_digest.py tests/assessment/test_word_report_renderer.py`

Expected: PASS for headings, Chinese/English fields, hyperlinks, ordering, no repeated coverage and unchanged existing sections.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include app/word_digest.py app/digest.py app/main.py tests/test_word_digest.py tests/assessment/test_word_report_renderer.py --output validation/international_media/handoff_task_5_4_sha256.json
Set-Content validation/international_media/handoff_task_5_4.txt 'git=absent'
```

## Wave 6 — Release Candidate, Operations and Isolated Validation

### Task 6.1: Add security scanner and final SHA-256 coverage

**Files:**

- Create: `validation/international_media/security_scan.py`
- Create: `validation/international_media/security_scan.json`
- Modify: `validation/international_media/build_sha256_manifest.py` only through Wave 0 owner handoff
- Test: `tests/test_international_security_scan.py`

**Interfaces:**

- Consumes: app/config/tests/docs/prompts and validation paths; structured allowlist for field names and fake values.
- Produces: Wave 6 defines `SecurityScanReport(status: Literal["pass","fail"], hits: list[str], rule_version: str, scanned_paths: list[str])`; `validation.international_media.security_scan::scan_paths(paths, excludes=()) -> SecurityScanReport`; nonzero exit on real credential/token/password/Cookie/Authorization/Feishu secret; final manifest includes prompts and validation outputs while excluding only the manifest itself.

- [ ] **Step 1: Write the failing test**

```python
def test_security_scan_rejects_bearer_secret_and_accepts_named_schema_field(tmp_path):
    (tmp_path / "bad.txt").write_text("Authorization: Bearer real-looking-secret")
    (tmp_path / "schema.json").write_text('{"required": ["client_secret"]}')
    assert scan_paths([tmp_path], excludes=()).status == "fail"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_security_scan.py::test_security_scan_rejects_bearer_secret_and_accepts_named_schema_field`

Expected: FAIL because the scanner does not exist.

- [ ] **Step 3: Write minimal implementation**

Scan actual values with regex/entropy rules, allow only documented schema keys and explicit fake values, exclude `.pyc`, `.db`, `data/**`, and write zero-hit counts plus rule version to `security_scan.json`; generate `final_validation_sha256_manifest.json` over `prompts/` and all final `validation/international_media/` outputs, excluding only generated manifest files.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_security_scan.py; python validation/international_media/security_scan.py --paths app config tests docs validation --exclude '*.pyc' --exclude '*.db' --exclude 'data/**' --manifest validation/international_media/security_scan.json; python validation/international_media/build_sha256_manifest.py --include prompts validation/international_media --exclude '**/sha256_manifest*.json' --output validation/international_media/final_validation_sha256_manifest.json`

Expected: PASS only when security hit count is zero and final manifest covers prompts, RC manifest, public/gmail/summary evidence, security scan, isolation results, Word OOXML/PDF and A–Q report.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include validation/international_media/security_scan.py validation/international_media/security_scan.json validation/international_media/final_validation_sha256_manifest.json prompts validation/international_media --exclude '**/sha256_manifest*.json' --output validation/international_media/handoff_task_6_1_sha256.json
Set-Content validation/international_media/handoff_task_6_1.txt 'git=absent'
```

### Task 6.2: Run isolated Reuters/FT two-pass validation

**Files:**

- Read-only dependency: `validation/international_media/run_isolated.py` (Wave 0 owner executes the shared Python runner)
- Read-only dependency: `validation/international_media/run_isolated.ps1` (optional thin-wrapper smoke; Wave 0 owner remains the only writer)
- Create: `validation/international_media/isolated_run_1.json`
- Create: `validation/international_media/isolated_run_2.json`
- Create: `validation/international_media/word_structure_report.json`
- Test: `tests/test_international_release_gates.py`

**Interfaces:**

- Consumes: isolated config, isolated SQLite, isolated reports, existing manual/now mechanism, `DISABLE_FEISHU_SEND=true`.
- Consumes read-only: `validation.international_media.run_isolated::load_runs(first_path, second_path) -> tuple[RunResult,RunResult]`, implemented and owned by Wave 0; uses the same Python module as Task 0.2 and Task 3.3. Produces only `isolated_run_1.json`, `isolated_run_2.json`, `word_structure_report.json`, per-source `fetched/parsed/inserted/fresh/relevant/important/errors`, and second-pass idempotency evidence; no real Feishu/Scheduler side effects.

- [ ] **Step 1: Write the failing test**

```python
def test_two_pass_isolation_has_no_duplicate_delivery_and_no_real_notifier_call():
    first, second = load_runs("isolated_run_1.json", "isolated_run_2.json")
    assert second.inserted == 0
    assert second.duplicate_word_items == 0
    assert second.real_feishu_calls == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_release_gates.py::test_two_pass_isolation_has_no_duplicate_delivery_and_no_real_notifier_call`

Expected: FAIL because isolated run artifacts and release assertions are absent.

- [ ] **Step 3: Write minimal implementation**

Invoke the Wave 0-owned `validation.international_media.run_isolated.run_isolated_collection(config, db, reports_path, dry_run=True)` for at least two passes with independent config/DB/reports, then call its read-only `load_runs(first_path, second_path)` to compare them. Record baseline and freshness, use disabled Newsletter sources when OAuth is absent, capture Word OOXML structure, and keep notifier dry-run. The PowerShell wrapper may be used only to forward the same arguments. If LibreOffice/Poppler exists, render PDF and record pixel-level operator check; if absent, record `operator_action_required`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python validation/international_media/run_isolated.py --config validation/international_media/config.yaml --db validation/international_media/news.db --reports validation/international_media/reports --dry-run; powershell -ExecutionPolicy Bypass -File validation/international_media/run_isolated.ps1 --config validation/international_media/config.yaml --db validation/international_media/news.db --reports validation/international_media/reports --dry-run; python -c "from validation.international_media.run_isolated import load_runs; load_runs('validation/international_media/isolated_run_1.json','validation/international_media/isolated_run_2.json')"; python -m pytest -q tests/test_international_release_gates.py`

Expected: PASS for Reuters/FT live gates, Taiwan failure isolation, first-run baseline, second-run idempotency, Word structure, and zero real Feishu calls. Newsletter without OAuth remains `MAILBOX_AUTH_REQUIRED`.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include validation/international_media/isolated_run_1.json validation/international_media/isolated_run_2.json validation/international_media/word_structure_report.json tests/test_international_release_gates.py --output validation/international_media/handoff_task_6_2_sha256.json
Set-Content validation/international_media/handoff_task_6_2.txt 'git=absent'
```

### Task 6.3: Freeze RC manifest and operator guide

**Files:**

- Create: `validation/international_media/rc_manifest.json`
- Create: `validation/international_media/rc_manifest.py`
- Create: `validation/international_media/INTERNATIONAL_MEDIA_RELEASE_CANDIDATE.md`
- Create: `docs/INTERNATIONAL_MEDIA_OPERATOR_GUIDE.md`
- Read-only dependency: `validation/international_media/newsletter_availability/newsletter_live_verification_manifest.json` (Wave 2 owner; do not rewrite evidence)
- Read-only dependency: `docs/INTERNATIONAL_NEWSLETTER_OPERATOR_GUIDE.md` (Wave 2 owner; do not rewrite)
- Test: `tests/test_international_release_manifest.py`

**Interfaces:**

- Consumes: all prior handoff manifests, dependency versions, gold metrics, source health, isolated runs, availability evidence, security scan and Word report; the Wave 5 post-handoff `golden_metrics.json` must have event status `pass` before RC eligibility.
- Produces: Wave 6 defines `RCManifest(has_sections(letters: list[str]) -> bool, source_status: dict[str,str], sections: dict[str,dict], artifact_hashes: dict[str,str], event_metrics_status: Literal["pending_wave5","pass","fail"])`; `validation.international_media.rc_manifest::load_rc_manifest(path) -> RCManifest`; immutable RC manifest and operator guide; states `verified`, `operator_action_required`, or `not_ready` per source without changing production switches, and rejects `pending_wave5` as an RC pass.

- [ ] **Step 1: Write the failing test**

```python
def test_rc_manifest_requires_all_a_to_q_sections_and_four_source_statuses():
    manifest = load_rc_manifest("validation/international_media/rc_manifest.json")
    assert manifest.has_sections(list("ABCDEFGHIJKLMNOPQ"))
    assert set(manifest.source_status) == {"reuters_international", "ft_alphaville", "wsj_newsletter", "bloomberg_newsletter"}
    assert manifest.event_metrics_status == "pass"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_release_manifest.py::test_rc_manifest_requires_all_a_to_q_sections_and_four_source_statuses`

Expected: FAIL because the final RC manifest and A–Q report do not exist.

- [ ] **Step 3: Write minimal implementation**

Record every source entry/path/hash, all commands/results, four source states, OAuth state, live dates, golden metrics, event candidate metrics, Word evidence, security zero-hit evidence, production switches, Scheduler unchanged state, rollback and operator actions. Reject or mark `not_ready` any gold report whose event status is `pending_wave5`; only Wave 5's real EventCluster metrics with exact pair/canonical/coverage results can satisfy the event RC gate. The guide must state that no automation/subagent sends Feishu and that RC approval is required before any operator action.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_release_manifest.py; python -m compileall app tests; python -m pytest -q`

Expected: PASS only when all automatic gates are green and missing OAuth/Word pixel inspection is explicitly represented as operator action rather than hidden.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include validation/international_media/rc_manifest.json validation/international_media/INTERNATIONAL_MEDIA_RELEASE_CANDIDATE.md validation/international_media/newsletter_availability validation/international_media/security_scan.json validation/international_media/final_validation_sha256_manifest.json docs/INTERNATIONAL_MEDIA_OPERATOR_GUIDE.md tests/test_international_release_manifest.py prompts --exclude '**/sha256_manifest*.json' --output validation/international_media/handoff_task_6_3_sha256.json
Set-Content validation/international_media/handoff_task_6_3.txt 'git=absent'
```

## Wave 7 — Independent Review and Release Decision

### Task 7.1: Execute independent A–Q review

**Files:**

- Create: `validation/international_media/independent_review.json`
- Create: `validation/international_media/independent_review.py`
- Test: `tests/test_international_independent_review.py`
- Read-only inputs: all source/config/app/tests/docs/validation artifacts from Waves 0–6

**Interfaces:**

- Consumes: RC manifest and all handoff SHA-256 manifests; does not modify implementation files or production state.
- Produces: Wave 7 defines `IndependentReview(sections: list[str], evidence: dict[str,str], real_feishu_calls: int, wsj_status: str, wsj_mailbox_evidence_status: str, final_status: str)`; `validation.international_media.independent_review::review_rc(path) -> IndependentReview` performs strict A–Q review with `pass/fail/operator_action_required/not_run` evidence and final one of three statuses.

- [ ] **Step 1: Write the failing test**

```python
def test_independent_review_has_strict_a_to_q_and_no_unverified_source_claims():
    report = review_rc("validation/international_media/rc_manifest.json")
    assert report.sections == list("ABCDEFGHIJKLMNOPQ")
    assert report.real_feishu_calls == 0
    assert report.wsj_status != "verified" or report.wsj_mailbox_evidence_status == "verified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_independent_review.py::test_independent_review_has_strict_a_to_q_and_no_unverified_source_claims`

Expected: FAIL because the independent review artifact is absent.

- [ ] **Step 3: Write minimal implementation**

Check source IDs/config, ownership/handoff, body-fetch guards, sender allowlist, OAuth scope, gold thresholds, event positive/negative exactness, notification candidate one-per-event, live empty/structure states, Word evidence, security scan, no-Git hashes and Taiwan regression. Mark missing OAuth or unavailable pixel render as operator action, never as verified.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_independent_review.py; python -m compileall app tests; python -m pytest -q`

Expected: PASS only with all automatic requirements proven; otherwise final status is `INTERNATIONAL_MEDIA_NOT_READY` or `INTERNATIONAL_MEDIA_COMPLETE_WITH_OPERATOR_ACTION` with the exact blocker recorded.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include validation/international_media/independent_review.json tests/test_international_independent_review.py validation/international_media/rc_manifest.json validation/international_media/final_validation_sha256_manifest.json --output validation/international_media/handoff_task_7_1_sha256.json
Set-Content validation/international_media/handoff_task_7_1.txt 'git=absent'
```

### Task 7.2: Record operator-only release decision and rollback readiness

**Files:**

- Read-only dependency: `validation/international_media/INTERNATIONAL_MEDIA_RELEASE_CANDIDATE.md` (Wave 6 owner; no Wave 7 edits)
- Read-only dependency: `docs/INTERNATIONAL_MEDIA_OPERATOR_GUIDE.md` (Wave 6 owner; no Wave 7 edits)
- Create: `validation/international_media/operator_release_decision.json`
- Create: `validation/international_media/operator_release.py`
- Test: `tests/test_international_operator_gate.py`

**Interfaces:**

- Consumes: independent review, source summaries, production config backup, Scheduler observation and rollback checklist.
- Produces: Wave 7 defines `OperatorDecision(status: str, automation_may_enable_sources: bool, automation_may_send_feishu: bool, backup_paths: list[str], rollback_command: str)`; `validation.international_media.operator_release::load_operator_decision(path) -> OperatorDecision`; operator-only decision; no code path, subagent, test, or RC runner can enable a source or send Feishu.

- [ ] **Step 1: Write the failing test**

```python
def test_operator_gate_keeps_all_sources_false_before_explicit_authorization():
    decision = load_operator_decision("validation/international_media/operator_release_decision.json")
    assert decision.automation_may_enable_sources is False
    assert decision.automation_may_send_feishu is False
    assert decision.status in {"INTERNATIONAL_MEDIA_NOT_READY", "INTERNATIONAL_MEDIA_COMPLETE_WITH_OPERATOR_ACTION"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_international_operator_gate.py::test_operator_gate_keeps_all_sources_false_before_explicit_authorization`

Expected: FAIL because operator decision artifact and guard are absent.

- [ ] **Step 3: Write minimal implementation**

Record config/DB backup paths, Scheduler observation, four source statuses, OAuth/pixel operator actions, rollback command, and explicit automation prohibitions. Do not change `enabled` values, Scheduler, Feishu, or production DB.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_international_operator_gate.py`

Expected: PASS with all four source switches false and automation permissions false; only a human can later authorize Reuters/FT or separately authorized Newsletter sources after RC.

- [ ] **Step 5: Generate handoff SHA-256 and record no Git**

```powershell
python validation/international_media/build_sha256_manifest.py --include validation/international_media/operator_release_decision.json validation/international_media/INTERNATIONAL_MEDIA_RELEASE_CANDIDATE.md docs/INTERNATIONAL_MEDIA_OPERATOR_GUIDE.md tests/test_international_operator_gate.py --output validation/international_media/handoff_task_7_2_sha256.json
Set-Content validation/international_media/handoff_task_7_2.txt 'git=absent'
```

## Quantitative Gold and Live Acceptance Contract

The minimum corpus and thresholds are mandatory, not suggestions:

- Relevance/importance: 32 rows, exactly 16 positive and 16 negative. Positive minimums are military 4, diplomacy/policy/sanctions 4, semiconductor/trade 4, China–US–Indo-Pacific political/security 4. Negative minimums are China restaurant/social 3, Washington local 3, `Taiwan Semiconductor` ambiguity 3, generic semiconductor company 3, Pentagon personnel 2, Japan domestic politics 2.
- Every relevance/importance row contains `case_id,title,summary,source_id,published_at,expected_relevant,expected_tier,expected_topics,expected_entities,expected_importance_level,expected_min_score,expected_notification,expected_cluster_id,expected_is_canonical,expected_coverage_source_ids,expected_reason_contains,body_fetch_forbidden`; event rows additionally contain `input_article_count,input_source_ids,expected_pair_merge`; predictions use `actual_*` fields. The four-source coverage row has `input_article_count=4` and all four exact source IDs.
- `TP = expected_relevant=true and actual_relevant=true`; `FP = expected_relevant=false and actual_relevant=true`; `FN = expected_relevant=true and actual_relevant=false`; precision and recall denominators of zero fail the gate.
- Relevance requires precision≥0.95, recall≥0.90, hard-negative FP=0, tier/topic/entity exact match≥0.90. Importance requires level exact accuracy≥0.90 and important/critical precision≥0.90.
- Event corpus has 12 labeled pairs: six same-event positives, four similar-but-different negatives, two cross-day major-follow-up negatives. It also contains a separate four-Article coverage sample with one input Article from each exact source ID; it is not represented by duplicating or disguising a pair. Positive pairs require exact same cluster ID; the four-Article sample requires one canonical and exact four-source coverage IDs; negatives require different cluster IDs and separate canonicals. Wave 4 reports event status `pending_wave5` and does not count event metrics; after Wave 5 supplies the real EventCluster, pair precision and recall, cluster ID, canonical, and coverage exactness must all be 1.00/true.
- Newsletter fixtures contain 32 payloads: eight classes for each of Reuters/FT/WSJ/Bloomberg—normal, multi-article, missing summary, tracking URL, duplicate URL, multipart, missing time, and HTML structure change.
- Live valid-empty source is `healthy` with zero items; three valid empty runs or 48 hours without an item is `stale`; missing required nodes or non-empty unparseable response is `degraded`; three consecutive failures is `broken`.
- Public/Gmail/summary evidence is immutable: each mode refuses an existing output; summary records input SHA-256 and is verified only when both inputs are verified. No OAuth means Gmail `operator_action_required` and `MAILBOX_AUTH_REQUIRED`.

## Final A–Q Report Contract

The final report must contain these exact sections in this order, with evidence or an explicit `not_run`/`operator_action_required` reason:

- **A:** final architecture and complete data flow.
- **B:** added/modified/deleted files, unique owner, handoff SHA-256, and no-Git record.
- **C:** Reuters/FT/WSJ/Bloomberg entry, access level, body capability, enabled state, health, evidence date.
- **D:** Gmail mode, label/sender policy, adapter, tracking cleanup, deduplication, real-mail evidence or `MAILBOX_AUTH_REQUIRED`.
- **E:** gold count, TP/FP/FN, precision/recall, tier/topic/entity exact metrics, false-positive and false-negative cases.
- **F:** importance count, level accuracy, important/critical precision, score thresholds and Tier-1 bonus test.
- **G:** EventCluster canonical/coverage, positive/negative pairs, notification candidate count and one-event-one-alert proof.
- **H:** Article/SQLite schema, migration, URL/identity dedup, proof that no event table was added.
- **I:** Word international heading, Chinese title/summary, English title, sorting, hyperlinks, coverage and render evidence.
- **J:** readonly scope, allowlist, no password/token/Cookie/secret evidence and security scan result.
- **K:** compileall, pytest total/passed/failed/skipped and specialized gate results.
- **L:** Reuters/FT/Newsletter isolated live results, dates, per-source fetched/parsed/inserted/fresh/relevant/important/errors.
- **M:** first/second run inserted counts, duplicate Word count, duplicate candidate count and baseline proof.
- **N:** Taiwan source, military, religion, Word, Feishu dry-run, Scheduler and DB regression.
- **O:** four production states, release authorization and operator actions.
- **P:** only objective remaining limits such as missing OAuth or unavailable pixel renderer.
- **Q:** exactly one of `INTERNATIONAL_MEDIA_COMPLETE`, `INTERNATIONAL_MEDIA_COMPLETE_WITH_OPERATOR_ACTION`, `INTERNATIONAL_MEDIA_NOT_READY`, with reason.

## Release and Rollback Rules

1. Before RC, all sources stay false; all automation/subagents use Null/Recording notifier or dry-run and never call real Feishu.
2. If any automatic gate fails, report `INTERNATIONAL_MEDIA_NOT_READY`, leave all switches false, and do not wait for or modify Scheduler.
3. If automatic gates pass but Gmail OAuth or Word pixel inspection is missing, report `INTERNATIONAL_MEDIA_COMPLETE_WITH_OPERATOR_ACTION`; do not claim WSJ/Bloomberg verified.
4. Only after RC review may an operator back up config/DB, record Scheduler state, and explicitly authorize Reuters/FT. WSJ/Bloomberg require their own verified summary evidence and OAuth before separate authorization.
5. To roll back, set only the failing source to `enabled: false`, retain Article/history/health, stop international delivery candidates if needed, restore prior config/code manifest, and rerun Taiwan smoke. Never delete or reset production DB; never alter Scheduler as part of rollback.
