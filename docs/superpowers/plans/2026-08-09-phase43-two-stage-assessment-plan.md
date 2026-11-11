# Phase 4.3 Two-Stage Assessment Implementation Plan

## Objective

Implement the approved isolated two-stage Assessment candidate without changing the RC4 single-stage default, prove its deterministic safety and analytical quality against the same frozen inputs, and permit bounded DeepSeek Live and RC5 creation only when every preceding gate passes.

## Fixed guardrails

- Keep input Contract `1.0` and final report Schema `1.1` unchanged.
- Keep `assessment_generation_mode=single_stage` as the default.
- Preserve the RC4 single-stage production files and behavior as the A baseline.
- Do not change formal facts, sources, polls, Coverage, `facts_cutoff`, active Snapshot, Candidate, Publication, Scheduler, or delivery state.
- Do not add LLM repair calls or programmatic semantic repair.
- Do not switch from `deepseek-v4-pro`.
- Do not call DeepSeek unless the offline benchmark passes every required metric.
- Do not build RC5 unless offline and Live gates pass.
- Do not call Feishu, install Scheduler, or activate production.

## Task 1: Freeze the Phase 4.3 baseline

1. Create `deployment/phase4/two_stage_benchmark/`.
2. Programmatically record RC4 archive, manifest, source, report Schema, input Contract, prompts, Provider, Validator, formal-state, Coverage, facts cutoff, and Snapshot hashes.
3. Freeze the Phase 4.2 real Evidence Pack and its Contract as the real Benchmark input without rebuilding formal facts.
4. Record the Phase 4.2 single-stage Live results as historical A evidence and identify unavailable fields honestly.
5. Record the direct pytest baseline and prove Scheduler/delivery/production activation remain false.

Deliverables:

- `baseline_manifest.json`
- `protected_hashes_before.json`
- `frozen_benchmark_input_manifest.json`
- `single_stage_rc4_baseline_manifest.json`

## Task 2: Add configuration and routing tests first

Files:

- modify `config/election_assessment.yaml`
- modify the relevant Assessment CLI/orchestrator
- add `tests/assessment/test_assessment_generation_mode.py`

Tests must prove:

- omitted mode resolves to `single_stage`;
- explicit `single_stage` invokes the existing path unchanged;
- explicit `two_stage` invokes only the new orchestrator;
- unknown modes fail closed;
- mode selection cannot enable delivery or Scheduler;
- version fields equal the approved values.

Run the failing tests before implementation, then add the smallest routing change.

## Task 3: Add internal Schemas and loading utilities

Files:

- add `app/assessment/schemas/claim_plan_v1.schema.json`
- add `app/assessment/schemas/stage2_report_draft_v1.schema.json`
- add `app/assessment/claim_plan_schema.py`
- add `tests/assessment/test_claim_plan_schema.py`
- add `tests/assessment/test_stage2_report_draft_schema.py`

Requirements:

- Draft 2020-12 and `additionalProperties=false` at every controlled object boundary.
- Exact version constants and required fields from the approved design.
- Existing final report Schema file remains byte-identical.
- Schema loaders have deterministic business hashes.
- Claim IDs match `CP_S(01–08)_NNN` and section IDs remain fixed.

## Task 4: Build the read-only Planner Envelope

Files:

- add `app/assessment/claim_planner.py`
- add `tests/assessment/test_claim_planner_envelope.py`
- add `app/assessment/prompts/tainan_claim_planner_v1.txt`

Implement a pure adapter that:

- projects only current Contract/Evidence Pack fields;
- derives `allowed_source_ids` for each Event and `allowed_poll_source_ids` for each Poll from existing relationships;
- carries Data Context, limitations, gaps, Snapshot dimensions, `do_not_infer`, and all frozen hashes;
- excludes unrelated full-source-library freedom;
- serializes the exact Claim Plan Schema into the Provider request;
- records the effective Planner Prompt and Schema hashes;
- never writes or changes formal data.

Prompt tests must inspect the final complete Provider message, not only source text.

## Task 5: Implement Claim Plan validation with TDD

Files:

- add `app/assessment/claim_plan_validator.py`
- add `tests/assessment/test_claim_plan_validator.py`
- reuse `app/assessment/claim_evidence_semantics.py`
- reuse or factor only necessary pure helpers from `claim_evidence_validator.py`

Validation sequence:

1. Claim Plan Schema.
2. Claim ID format, uniqueness, and target-section match.
3. Event/Poll/Source existence in the Planner Envelope.
4. Event–Source and Poll–Source allow-list relationships.
5. Atomicity.
6. Existing Claim type minimums.
7. Claim strength and confidence.
8. Actor-statement attribution.
9. Allegation attribution.
10. Existing Claim–Evidence semantics through a deterministic Claim adapter.
11. Section and analysis minimums.

Tests cover accepted, accepted-with-rejections, and rejected plans. They also prove rejected Claims are byte-identical before/after validation and never enter the accepted set.

## Task 6: Implement the immutable Validated Claim Store

Files:

- add `app/assessment/validated_claim_store.py`
- add `tests/assessment/test_validated_claim_store.py`

The store must contain raw, accepted, and rejected Claims; all validation reasons; section coverage; status fields; input/Prompt/Schema/Provider hashes; deterministic Claim business hashes; plan business hash; request IDs; token/cost/latency metadata; and an explicit no-repair/no-mutation audit.

Write atomically. Re-reading the same store must produce the same business hash. Stage 2 receives a restricted projection of accepted Claims and never the complete Evidence Pack.

## Task 7: Implement Stage 2 one-to-one rendering with TDD

Files:

- add `app/assessment/report_writer_stage2.py`
- add `app/assessment/prompts/tainan_report_writer_stage2_v1.txt`
- add `tests/assessment/test_report_writer_stage2.py`

The Stage 2 request contains only:

- accepted Claim IDs, canonical texts, types, strengths, confidence, limitations, and restricted reasoning summaries;
- authoritative Data Context and fixed section definitions;
- the Stage 2 Draft Schema.

It excludes Evidence IDs from the model-output contract. Prompt/request tests verify Stage 2 is explicitly forbidden from fresh analysis, new entities/facts/Claims, strength changes, and evidence selection.

## Task 8: Implement Final Claim Coverage and deterministic assembly

Files:

- add `app/assessment/final_claim_coverage_validator.py`
- add `tests/assessment/test_final_claim_coverage_validator.py`
- add focused final-assembly tests

Validate exact one-to-one Claim rendering, fixed eight-section order, section ownership, title/overall subsets, entity/organization/date/number boundaries, attribution, allegation markers, bounding language, prohibited strong-term escalation, and atomicity.

Assemble Schema v1.1 by copying all evidence metadata from accepted Claims and applying only approved render text/title/order. Inject Data Context and disclosures deterministically. Rerun the entire existing final Schema/reference/Claim–Evidence validation. Any failure rejects the report and prevents Word generation.

## Task 9: Add the two-stage orchestrator and auditable artifacts

Files:

- add `app/assessment/two_stage_pipeline.py`
- add `tests/assessment/test_two_stage_pipeline.py`
- minimally integrate with the existing CLI/orchestrator
- extend Mock support through the existing Provider interface without forking transport

Artifacts per run:

- Planner request/Prompt/Schema manifests;
- raw normalized Claim Plan;
- Claim Plan validation;
- `validated_claim_plan.json`;
- Stage 2 request/Prompt/Schema manifests when eligible;
- Stage 2 draft and coverage validation;
- final Schema v1.1 report and existing final validation;
- run manifest with independent stage statuses, calls, IDs, tokens, cost, latency, and hashes.

No code path may automatically call Stage 2 when Stage 1 is rejected.

## Task 10: Build and freeze the golden/Benchmark corpus

Files:

- add Stage 1 golden tests: 15 calibration + 5 holdout;
- add Stage 2/Coverage golden tests: 15 calibration + 5 holdout;
- add at least 10 end-to-end isolated Benchmark fixtures;
- add a Benchmark runner under `deployment/phase4/tools/` or a focused reusable module.

Freeze fixture hashes before validator tuning. Cover actor statements, allegations, multi-event inference, poll/no-poll, weak evidence, compound Claims, forward outlook, Source ambiguity, partial Coverage, and unauthorized Stage 2 additions.

Required golden metrics are exactly 1.00; skipped count is zero; fabricated Claim/Event/Source counts are zero.

## Task 11: Execute the offline A/B Benchmark

1. Run the unmodified RC4 single-stage path and two-stage path against the same frozen real sample and each matched fixture scenario.
2. Verify A/B Evidence Pack, formal-state, period, Coverage, Snapshot, and model identifiers match for each pair.
3. Produce A/B correctness, auditability, quality, cost, and latency metrics.
4. Preserve `single_stage_report_preview` and `two_stage_report_preview`.
5. Score information retention, analytical depth, coherence, language, and redundancy with the fixed 1–5 rubric.
6. Require two bounded inferences, one forward outlook, no quality dimension below 3, and mean at least 4.
7. Set `two_stage_outperforms_single_stage=true` only if every approved condition is met and B full-pipeline pass rate is strictly higher.

Deliverables:

- `offline_benchmark_results.json`
- `ab_input_equivalence.json`
- `ab_metrics.json`
- `writing_quality_rubric.json`
- `writing_quality_assessment.json`
- the two report previews

Stop before all Live calls if the offline gate fails.

## Task 12: Run local regression gates

Run in order:

1. Stage 1 specialized tests.
2. Stage 2/Coverage specialized tests.
3. Claim–Evidence and Phase 4.2 tests.
4. Phase 4.1 contract tests.
5. Phase 3 Assessment tests.
6. Phase 3 Snapshot/Trigger tests.
7. Phase 2.5 Recovery tests.
8. Phase 2 Publication tests.
9. Phase 1.5 Golden tests.
10. Complete `python -m pytest -q`.

Any failure prohibits Live. Record every command, count, skipped count, duration, and result. Do not delete, skip, xfail, or weaken existing tests.

## Task 13: Conditional bounded Live Benchmark

Only when Tasks 10–12 are fully green:

1. Freeze final code, Prompt, internal Schemas, final Schema, Contract, formal state, Evidence Pack, Coverage, Snapshot, and model hashes.
2. Execute at most one fresh RC4 single-stage baseline call.
3. Execute at most three Stage 1 calls; call Stage 2 once only for each Stage 1 result eligible for Stage 2.
4. Use `deepseek-v4-pro`, no repair, and a new auditable client request ID per call.
5. Stop after two consecutive complete two-stage passes or the third Stage 1 call.
6. Treat `PASS / FAIL / PASS` as unstable.
7. Do not tune code or Prompt between Live attempts.

Deliverable: `phase43_live_call_audit.json`.

## Task 14: Conditional Word and RC5

Only after two consecutive complete Live passes:

1. Generate Word locally from the last accepted v1.1 report.
2. Validate eight sections, all Claims, evidence mappings, Data Context, cutoffs, Snapshot, Coverage, and limitations.
3. Do not deliver Word externally.
4. Build `tainan-assessment-production-rc5` without replacing RC1–RC4.
5. Validate release manifest, source/bundle hashes, archive SHA-256, deployment copy, independent extraction, and critical files.

If any condition fails, record Word/RC5 as not executed and preserve only experimental artifacts.

## Task 15: Security, protected hashes, quality gate, and report

1. Scan actual DeepSeek keys, Feishu secrets, and Authorization values across source, Benchmark, logs, previews, Word temporary files, and RC5 when created. All counts must be zero.
2. Prove formal state, Coverage, facts cutoff, and active Snapshot hashes remain unchanged.
3. Generate `phase43_quality_gate.json` with truthful success or blocker status.
4. Generate the final report containing all 74 required items.
5. Preserve:

```text
production_default_mode=single_stage
production_delivery_ready=false
scheduler_activation_authorized=false
scheduler_installed=false
production_system_ready=false
```

Stop after reporting. Do not proceed to production activation or another architecture.
