# Phase 4.2 Claim–Evidence Blocker Remediation Design

## 1. Status and scope

- Approved approach: A — audit first, then evidence-driven minimal remediation.
- Release baseline: `tainan-assessment-production-rc3`.
- Input contract: `1.0`.
- Report output Schema: `1.1`.
- Provider/model: DeepSeek `deepseek-v4-pro`.
- This phase addresses only the formal Assessment Claim–Evidence blocker.
- The workspace is not a Git repository, so this document can be persisted but cannot be committed.

The phase must not change formal facts, sources, polls, Coverage, `facts_cutoff`, the active Snapshot, Candidate Pipeline, Publication Pipeline, or Scheduler state. It must not send a production Feishu report or claim credential rotation.

## 2. Success definition

The accepted path is:

```text
DeepSeek formal output
→ Schema v1.1 valid
→ event references valid
→ source references valid
→ Claim–Evidence valid
→ report accepted
```

The same frozen formal input must produce at least two consecutive complete passes within at most three new formal Live calls. Text equality is not required. A pass requires an auditable client request ID, valid Schema, valid event/source references, valid Claim–Evidence, and `report_status=accepted`.

Only then may `formal_assessment_live_ready` and `production_llm_ready` become true. If no two consecutive passes occur within three calls, stop with `blocker=provider_claim_evidence_instability`.

## 3. Non-negotiable invariants

- Do not alter Schema v1.1, `additionalProperties=false`, required fields, section count, or section order.
- Do not weaken event/source reference or Claim–Evidence validation.
- Do not add a semantic normalizer, Claim splitter, evidence selector, or Claim/Evidence generator.
- Do not use a second LLM call to repair a report.
- Do not switch models or providers.
- Retain the format-only normalizer boundary: BOM, whitespace, one code fence, and unique JSON extraction.
- Keep `coverage_status=partial`, `facts_cutoff=2026-07-27`, and the active Snapshot unchanged.
- Keep `production_delivery_ready=false` and `scheduler_installed=false`.

## 4. Phase structure

### 4.1 Freeze before production-code changes

Create `deployment/phase4/claim_evidence_remediation/` and freeze:

- the RC3 baseline and protected hashes;
- all available Phase 4.1 formal attempts 1–3;
- raw sanitized and normalized responses;
- Schema, reference, and Claim–Evidence validation artifacts;
- request-shape and prompt/schema/contract hashes;
- evidence-pack and formal-state hashes;
- Coverage version and Snapshot ID;
- historical Provider request IDs, using explicit unavailable markers when absent.

Historical attempts must never be reconstructed through new Provider calls.

### 4.2 Per-Claim failure analysis

Generate `claim_evidence_failure_matrix.json`. Every Claim in all three attempts receives:

- attempt, section, ID, text summary, type, and internal strength classification;
- referenced event/source IDs and existence checks;
- direct, partial, or absent semantic support result;
- one or more CE01–CE19 failure classes;
- one or more root-cause classes: model noncompliance, Prompt ambiguity, Validator defect, or Evidence Pack limitation;
- repairability and validator rule ID.

Generate `formal_live_claim_evidence_summary.json` with per-attempt and aggregate counts. Attempt 3 must not replace attempts 1 and 2.

### 4.3 Four independent audits

1. Validator audit: document actual rules, reference checks, semantic coverage, multi-event/source behavior, type handling, false-positive risks, and consistency with Schema and Prompt.
2. Prompt audit: inspect the complete effective Provider request, not only source files. Verify Atomic Claim, fact/analysis separation, statement attribution, allegation attribution, evidence strength, and reference instructions.
3. Evidence Pack audit: verify event/source IDs, summaries, attribution, assertion type, dates, and limitations. Distinguish information present in formal storage from information actually transmitted.
4. Historical replay: rerun current deterministic validators over unmodified historical outputs to separate Validator changes from Prompt-only effects.

These are independent verification channels: source review, historical response replay, golden tests, integration tests, and new Live behavior must not be treated as interchangeable evidence.

## 5. Repair decision gate

No production code is changed until the four audits are complete.

- Path A: modify the Prompt only for proven contract ambiguity.
- Path B: modify the Validator only for a reproducible false positive. Record rule before/after, why the prior implementation was wrong, and why safety is preserved.
- Path C: modify the Evidence Pack Builder only when an existing formal fact is not faithfully transmitted.
- Path D: make no contract code change when Prompt, Validator, and Evidence Pack are already adequate; record Provider incompatibility.

More than one of A/B/C may be selected only when each has independent evidence. Every changed file is listed in `minimal_fix_manifest.json` with `why_changed`, `root_cause`, `expected_effect`, and `safety_invariant_preserved`.

## 6. Claim semantics

### 6.1 Atomic Claim

One Claim expresses one core assertion that its cited Evidence can independently support or reject. The Provider must split compound conclusions while generating; application code must not rewrite or split the response.

### 6.2 Fact and analysis

- A factual Claim requires direct formal support.
- An analytical Claim may combine multiple formal events, but must use bounded inference language, identify its basis, and not present the inference as a verified fact.
- Internal strength categories are `direct_fact`, `attributed_statement`, `bounded_inference`, `strong_inference`, and `unsupported`. They do not alter Schema v1.1.
- A strong inference cannot be accepted from one weak fact.

### 6.3 Actor statements and allegations

Evidence that a person said or alleged X directly supports only the attributed statement or allegation. It does not establish X as an objective fact without independent formal evidence. This rule must be represented in Prompt text, Validator behavior, and golden cases.

## 7. Request correlation

Generate a UUID `client_request_id` before every new Live request. It is the mandatory primary audit key and must appear in:

- request audit;
- sanitized raw response metadata;
- Assessment run and generation manifest;
- validation reports;
- Phase 4.2 Live call audit.

It must not enter the Prompt body. `provider_request_id` is supplementary. If the service omits it, persist `provider_request_id=null` and `provider_request_id_supported=false` without losing correlation.

## 8. Golden corpus

Add at least 30 Claim–Evidence cases:

- 20 calibration cases used while implementing;
- 10 holdout boundary cases kept separate from rule tuning;
- zero skipped cases.

Coverage includes direct facts, multi-source events, multi-event analysis, correct/incorrect statement attribution, correct/incorrect allegation attribution, missing and invalid references, overstatement, compound Claims, temporal and actor mismatch, bounded and strong inference, and section-summary consistency.

Required metrics:

```text
valid_claim_acceptance=1.00
unsupported_claim_rejection=1.00
statement_as_fact_rejection=1.00
allegation_as_fact_rejection=1.00
invalid_reference_rejection=1.00
validator_false_positive_rate=0
unsafe_relaxation_count=0
fabricated_claim_count=0
fabricated_evidence_count=0
```

## 9. Local validation order

After minimal remediation, run in this order:

1. Claim–Evidence golden cases.
2. Historical formal attempts 1–3 replay.
3. Phase 4.1 contract golden cases.
4. Mock Assessment.
5. Claim–Evidence full integration tests.
6. Complete pytest.

Any local failure prohibits real DeepSeek calls. Existing tests may not be deleted, skipped, xfailed, or weakened.

## 10. Live validation

- Skip Neutral Live unless a neutral structured-output layer changed.
- Use the same frozen formal input for every new formal call.
- Permit at most three formal Live calls.
- Stop early once two consecutive complete passes occur.
- A `PASS / FAIL / PASS` sequence is not stable and remains blocked.
- Record each call in `phase42_live_call_audit.json` with timestamps, both request IDs, hashes, model, transport result, all gate results, failure summary, and latency. Never record credentials or reasoning content.

Only after two consecutive passes may the last accepted output undergo local Word validation. Word generation is not production delivery.

## 11. Preflight and release semantics

Preflight must distinguish:

- `production_llm_ready`;
- `production_delivery_ready`;
- `scheduler_technical_install_ready`;
- `scheduler_activation_authorized`;
- `scheduler_installed`;
- `production_system_ready`;
- `current_reporting_period_final_ready`.

Coverage partial must not be mislabeled as a technical system failure. Even if LLM readiness succeeds, Feishu rotation remains unconfirmed, delivery remains false, Scheduler activation remains unauthorized, and production activation remains blocked.

Create RC4 only if production code changes. Preserve RC1–RC3. If created, RC4 requires full tests, manifest/hash verification, archive SHA-256, independent extraction validation, critical-file comparison, and credential-value scanning.

## 12. Security and formal-data protection

Scan source, release/deployment artifacts, runtime logs, LLM artifacts, and Word temporary files. Actual DeepSeek keys, Feishu secrets, and Authorization values must have zero matches.

The following remain false:

```text
production_real_candidate_commit_performed
production_real_snapshot_activation_performed
production_real_coverage_commit_performed
production_real_assessment_delivery_performed
```

Before/after business hashes must prove formal state, Coverage, and active Snapshot are unchanged.

## 13. Deliverables

Required artifacts under `deployment/phase4/claim_evidence_remediation/` include:

- baseline and frozen-input manifests;
- protected hashes before/after;
- three historical attempt directories;
- Claim–Evidence Failure Matrix and aggregate summary;
- Validator, Prompt, and Evidence Pack audits;
- Validator change justification when applicable;
- minimal fix manifest;
- golden results and historical replay results;
- Phase 4.2 Live call audit;
- credential scan;
- Phase 4.2 quality gate;
- final report covering all 73 requested details.

The phase stops after reporting. It does not proceed to another phase, install Scheduler, send a production report, or activate production.
