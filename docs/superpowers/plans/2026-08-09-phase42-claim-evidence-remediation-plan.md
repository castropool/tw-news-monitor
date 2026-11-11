# Phase 4.2 Claim–Evidence Remediation Implementation Plan

## Objective

Audit every Claim in the three Phase 4.1 formal Live outputs, identify the evidence-backed root causes, apply only proven minimal fixes, and determine whether `deepseek-v4-pro` can produce two consecutive fully accepted reports from one frozen formal input within at most three new formal calls.

## Guardrails

- Keep input Contract 1.0 and report Schema 1.1 unchanged.
- Do not alter formal facts, sources, polls, Coverage, facts cutoff, active Snapshot, candidate/publication pipelines, or Scheduler.
- Do not add semantic rewriting, a second LLM repair, or a different model/provider.
- Do not call Feishu or install/activate Scheduler.
- Do not call DeepSeek until every local gate passes.

## Task 1: Freeze Phase 4.2 baseline

1. Programmatically read the Phase 4.1 quality gate and current pytest baseline.
2. Create `deployment/phase4/claim_evidence_remediation/`.
3. Copy the three historical formal attempt directories without modifying their content.
4. Record baseline, protected hashes, frozen-input hashes, RC1–RC3 hashes, Coverage, facts cutoff, Snapshot, formal-state hash, Prompt/Schema/Contract hashes, and unavailable historical request IDs.
5. Run a credential-value scan over the frozen copy.

Deliverables:

- `baseline_manifest.json`
- `protected_hashes_before.json`
- `frozen_input_manifest.json`
- `formal_attempt_01/` through `formal_attempt_03/`

## Task 2: Build deterministic Claim audit tooling

1. Add a Phase 4.2 audit tool under `deployment/phase4/tools/` or a focused test-support module when production reuse is required.
2. Parse every Claim from all historical attempts.
3. Resolve cited events, polls, sources, gaps, and dimensions against the frozen input.
4. Classify strength, support scope, CE01–CE19 failures, and A/B/C/D causes without editing reports.
5. Produce per-attempt and aggregate counts.

Deliverables:

- `claim_evidence_failure_matrix.json`
- `formal_live_claim_evidence_summary.json`

## Task 3: Complete independent audits

1. Validator audit: map every implemented rule and reproduce false positives/negatives.
2. Prompt audit: reconstruct the complete effective Provider request and verify the eight required Claim contract topics.
3. Evidence Pack audit: compare formal storage fields with transmitted event objects and identify lost attribution/assertion information.
4. Historical replay: run current deterministic Schema/reference/Claim–Evidence validation over all three immutable outputs.
5. Select Path A/B/C/D only after audit evidence exists.

Deliverables:

- `claim_evidence_validator_audit.json`
- `claim_evidence_prompt_audit.json`
- `evidence_pack_claim_support_audit.json`
- `historical_formal_replay_results.json`
- `root_cause_decision.json`

## Task 4: Write failing golden and request-correlation tests

1. Add at least 20 calibration and 10 holdout Claim–Evidence cases.
2. Cover direct facts, multi-source and multi-event support, attribution, allegations, missing/invalid references, overstatement, compound Claims, temporal/actor mismatch, bounded/strong inference, and section-summary consistency.
3. Add tests that assert unsupported statement/allegation upgrades are rejected.
4. Add tests that assert valid attributed statements and bounded inferences are accepted.
5. Add tests proving `client_request_id` exists throughout request audit, Provider result, manifests, validation artifacts, and Live audit metadata while remaining absent from Prompt content.

Expected initial result: targeted tests expose only audited gaps.

## Task 5: Apply minimal production fixes

Depending on the recorded decision:

- Path A: add explicit Atomic Claim, fact/analysis, attributed statement, allegation, and strength rules to the actual writer Prompt.
- Path B: fix only demonstrated Validator false positives or missing safety checks. Record rule before/after and safety proof.
- Path C: transmit only existing formal attribution/assertion fields omitted by the Evidence Pack Builder.
- Request correlation: generate mandatory UUID `client_request_id` before the call and persist it through all artifacts; record Provider ID support separately.

Do not change the Schema file or format-only normalizer. Record every changed production file in `minimal_fix_manifest.json`.

## Task 6: Run local gates in the required order

1. Claim–Evidence golden suite.
2. Historical formal replay suite.
3. Phase 4.1 DeepSeek contract golden suite.
4. Mock Assessment run and artifact validation.
5. Claim–Evidence integration suite.
6. Phase-specific regression groups: Assessment, Snapshot/Trigger, recovery, publication, and Phase 1.5 golden gate.
7. Complete `python -m pytest -q`.

Stop before Live if any required local test fails.

## Task 7: Freeze Live input and execute bounded validation

1. Recompute formal state, Evidence Pack, Coverage, Snapshot, Prompt, writer Prompt, Schema, and Contract hashes.
2. Prove the input is identical for every new call.
3. Skip Neutral unless the neutral structured-output layer changed.
4. Execute at most three formal Live calls with `deepseek-v4-pro`.
5. Persist client/provider request IDs and every gate result.
6. Stop early on two consecutive full passes; otherwise stop after attempt 3.

Deliverable: `phase42_live_call_audit.json`.

## Task 8: Conditional Word validation

Only after two consecutive full passes:

1. Generate Word from the last accepted report.
2. Verify eight sections, Data Context, formal state, Coverage, cutoffs, Snapshot, limitations, and Claim/Evidence references.
3. Do not deliver the document externally.

If stability fails, record Word validation as not executed due to gate failure.

## Task 9: Preflight, release, and security verification

1. Regress technical/delivery/period readiness separation.
2. Add `scheduler_technical_install_ready` and `scheduler_activation_authorized` only if not already represented correctly.
3. If production code changed, build RC4 without overwriting RC1–RC3.
4. Validate bundle, deployment copy, archive, independent extraction, manifest, source hashes, and critical files.
5. Scan actual configured credential values and Authorization header values across source, release, deployment, runtime, LLM, and Word artifacts.
6. Prove formal state, Coverage, facts cutoff, and Snapshot hashes are unchanged.

## Task 10: Final quality gate and report

Generate:

- `phase42_quality_gate.json`
- production status update that preserves delivery/Scheduler blocking
- final report containing all 73 requested items

If Live is stable, record LLM readiness true while production activation remains blocked by external delivery safety. If Live is not stable, record `blocker=provider_claim_evidence_instability` and stop without further Prompt tuning or model calls.
