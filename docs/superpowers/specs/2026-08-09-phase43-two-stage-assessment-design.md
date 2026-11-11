# Phase 4.3 Two-Stage Assessment Generation Design

## 1. Decision and status

Phase 4.3 implements the approved **Two-Stage Assessment Generation** architecture as an isolated production-candidate experiment. It does not activate production.

Approved decisions:

- Preserve the RC4 single-stage pipeline unchanged as the default, benchmark baseline, debugging path, and research-only path.
- Add `assessment_generation_mode=two_stage` as an opt-in path.
- Use a Stage 1 Claim Planner followed by validation and a Stage 2 one-to-one Claim Rendering writer.
- Require S01, S02, S03, S04, S07, and S08 to contain validated Claims before Stage 2 may run.
- Permit S05 and S06 to use deterministic evidence-insufficiency disclosure when no valid Claim exists.
- Require at least one `bounded_inference` and one `forward_outlook` for Stage 1 eligibility; the real benchmark quality gate is stricter and requires at least two bounded inferences and one forward outlook.
- Keep the production default at `single_stage` throughout this phase.

The workspace is not a Git repository. This specification can be persisted and reviewed, but it cannot be committed.

## 2. Versions and unchanged contracts

New experimental versions:

```text
assessment_generation_pipeline_version=2.0.0-rc1
claim_plan_schema_version=1.0
claim_planner_contract_version=1.0
report_writer_stage2_contract_version=1.0
```

Existing authoritative versions remain:

```text
input_contract_version=1.0
report_output_schema_version=1.1
```

Phase 4.3 does not upgrade the formal input Contract or the final report Schema. Internal Stage 1 and Stage 2 Schemas are run-artifact contracts, not replacements for either authoritative contract.

## 3. Non-negotiable invariants

The implementation must not:

- modify formal facts, sources, polls, Coverage, `facts_cutoff`, or active Snapshot;
- modify Candidate or Publication behavior;
- install or authorize Scheduler;
- send a production Feishu report;
- claim Feishu credential rotation;
- switch from `deepseek-v4-pro`;
- delete or weaken the RC4 single-stage path;
- lower Claim–Evidence or reference standards;
- add LLM repair calls for Stage 1 or Stage 2;
- programmatically rewrite, split, strengthen, or repair a failed Planner Claim;
- permit Stage 2 to select Event, Source, Poll, Gap, or Snapshot evidence;
- change the production default mode during this phase.

The following remain false:

```text
production_real_candidate_commit_performed
production_real_snapshot_activation_performed
production_real_coverage_commit_performed
production_real_assessment_delivery_performed
scheduler_installed
scheduler_activation_authorized
production_delivery_ready
```

## 4. Architecture

```text
assessment_generation_mode
├── single_stage
│   └── existing RC4 generation and validation path
└── two_stage
    ├── Evidence Pack → Planner Envelope adapter
    ├── Stage 1 Claim Planner
    ├── Claim Plan Schema Validator
    ├── Claim Plan semantic/reference Validator
    ├── existing Phase 4.2 per-Claim semantic validation
    ├── Validated Claim Store
    ├── Stage 2 one-to-one Claim Rendering writer
    ├── Final Claim Coverage Validator
    ├── deterministic Schema v1.1 assembler
    ├── existing final report Validator
    └── isolated Word validation when eligible
```

The two modes share the existing Evidence Pack Builder, unified Provider, Provider request audit, output normalizer, Data Context builder, final report Schema, final Validator, renderer, and security controls. Two-stage code must not copy parallel versions of those facilities.

## 5. Planner Envelope and evidence boundaries

Stage 1 receives the existing Contract and Evidence Pack through a smaller Planner Envelope. The adapter may project existing authoritative fields but may not infer facts.

For every Event it exposes:

```text
event_id
event_date
fact/status/assertion metadata already present
allowed_source_ids
```

For every Poll it exposes:

```text
poll_id
poll dates and existing result metadata
allowed_poll_source_ids
```

`allowed_source_ids` and `allowed_poll_source_ids` are derived deterministically from formal relationships already present in the current Evidence Pack. They do not upgrade Contract 1.0. Stage 1 may cite only edges explicitly present in the current Planner Envelope. A Source that exists elsewhere in the formal database but is absent from an Event or Poll allow-list is invalid for that Claim.

The Envelope also includes authoritative Data Context, `do_not_infer`, limitations, coverage gaps, Snapshot dimensions, formal-state hash, Evidence Pack hash, period, and fixed section definitions.

## 6. Stage 1 responsibilities and Prompt

Stage 1 answers only: which atomic Claims can enter this reporting period?

It must not produce an eight-section article, long narrative, rhetorical headings, background filler, conclusions outside Claim objects, or unbound analysis. Output must be short, structured, atomic, and directly auditable.

The Prompt states:

- one Claim contains one independently verifiable core assertion;
- direct facts may not exceed direct evidence;
- actor statements remain attributed statements;
- allegations remain attributed allegations;
- analytical Claims must identify their evidence combination and remain bounded;
- forward outlook must use explicit prediction/possibility language and meet existing evidence-count rules;
- Claim strength may not exceed evidence strength;
- Event, Source, and Poll IDs must be selected only from supplied allow-lists;
- rejected or uncertain material must not be converted into a stronger Claim;
- no report prose is requested at this stage.

## 7. Claim Plan Schema 1.0

The Claim Plan Schema uses JSON Schema Draft 2020-12 and `additionalProperties=false`.

Required top-level fields:

```text
claim_plan_version
claim_planner_contract_version
election_id
reporting_period
formal_state_hash
evidence_pack_hash
claims
data_limitations
```

Each Claim requires:

```text
claim_id
target_section_id
claim_type
claim_strength
claim_text
event_ids
source_ids
poll_ids
snapshot_dimensions
gap_ids
evidence_reasoning_summary
confidence
limitations
material_for_report
applies_to_period
```

`claim_type` reuses the final v1.1 enum:

```text
factual_synthesis
current_assessment
comparative_assessment
forward_outlook
limitation
data_disclosure
```

`claim_strength` is an internal validation classification:

```text
direct_fact
attributed_statement
bounded_inference
strong_inference
unsupported
```

It does not create a competing final-report Claim type.

Claim IDs follow `CP_S##_NNN`, are unique within the immutable Claim Plan, and match `target_section_id`. A deterministic `claim_business_hash` is calculated after validation for cross-run matching. The business hash is not the human-facing Claim ID.

## 8. Stage 1 validation and status

Validation order is fail-closed:

```text
Claim Plan Schema
→ Claim ID and section validation
→ Event existence
→ Source existence
→ Event–Source allow-list relationship
→ Poll existence and Poll–Source allow-list relationship
→ atomicity
→ existing Claim type rules
→ Claim strength rules
→ statement attribution
→ allegation attribution
→ Phase 4.2 Claim–Evidence semantics
→ section coverage
```

Evidence minimums exactly preserve the existing formal type rules: `factual_synthesis` requires at least one Event or Poll; `current_assessment` requires two Events or one Snapshot dimension plus one Event; `comparative_assessment` requires a Snapshot dimension; `forward_outlook` requires at least two Events/Polls; `limitation` and `data_disclosure` retain their existing narrow rules. No Claim may cite evidence outside the current Planner Envelope.

Each failed Claim is preserved under `rejected_claims` with all deterministic reasons. It is never changed, repaired, split, or passed to Stage 2.

Claim Plan status:

- `accepted`: every generated Claim is valid and the coverage gate passes.
- `accepted_with_rejections`: some Claims are invalid, but the remaining Claims satisfy all minimum coverage and analysis requirements.
- `rejected`: minimum coverage or analysis requirements fail; Stage 2 is not called.

Minimum Stage 1 eligibility:

- S01, S02, S03, S04, S07, and S08 each contain at least one validated Claim.
- S05 and S06 may lack a substantive Claim only when a deterministic insufficiency disclosure is generated from authoritative limitations.
- The accepted set contains at least one `bounded_inference` and one `forward_outlook`.
- S07 includes a bounded prospective Claim meeting the existing forward-outlook evidence gate.
- S08 includes applicable limitation or data-disclosure Claims.

## 9. Validated Claim Store

`validated_claim_plan.json` is the formal boundary between stages. It contains:

```text
input, Prompt, Schema, Provider, and business hashes
raw Claim Plan
all generated Claims
accepted Claims
rejected Claims and validation reasons
section coverage
claim_plan_status
claim_validation_status
claim_plan_business_hash
client/provider request IDs
tokens, cost, latency, and API-call counts
```

The store is immutable for the run. Stage 2 receives only accepted Claims, necessary restricted evidence summaries, authoritative Data Context, fixed sections, and explicit limitations. It does not receive the complete Evidence Pack for fresh analysis.

## 10. Stage 2 one-to-one Claim Rendering

Stage 2 is a constrained writer. It may improve expression, ordering, headings, flow, compression, and section coherence. It is not an evidence selector or political-analysis planner.

The Stage 2 Prompt states:

- use only VALIDATED CLAIMS;
- do not add a substantive political judgment;
- do not add a person, organization, poll, date, number, or fact;
- do not alter Claim strength;
- do not select or emit Event, Source, Poll, Gap, or Snapshot IDs;
- preserve actor-statement and allegation attribution;
- disclose insufficient evidence when a section lacks a valid substantive Claim;
- do not hide `facts_cutoff`, uncovered dates, poll limitations, or other supplied limitations.

The internal Stage 2 Draft Schema uses `additionalProperties=false` and contains:

```text
stage2_draft_version
report_writer_stage2_contract_version
validated_claim_plan_hash
title
title_claim_ids
overall_judgment_claim_ids
sections[section_id, heading, claim_ids, section_purpose]
claim_renderings[claim_id, rendered_text]
```

It contains no Evidence IDs or new Claim metadata. Every accepted Claim, including nonmaterial disclosures, must have exactly one rendering and appear in exactly one section. Every rendered ID and section ID must exist in the Validated Claim Store.

## 11. Final Claim Coverage and deterministic assembly

The Final Claim Coverage Validator does not rely on a fuzzy LLM judgment as its sole gate. It checks:

- exact Claim ID set and one-to-one rendering cardinality;
- fixed section count/order and Claim-to-section consistency;
- title and overall IDs are valid subsets;
- every accepted validated Claim is used exactly once;
- no unauthorized rendering or Claim ID exists;
- persons, organizations, dates, and numbers stay within the corresponding validated Claim's allowed set plus existing whitelists;
- attributed statements retain the speaker and attribution marker;
- allegations retain accuser and allegation semantics;
- bounded inferences and forward outlook retain limiting language;
- strong terms are not introduced beyond the validated strength;
- rendered text remains atomic;
- deterministic insufficiency disclosures contain only authoritative limitation content.

The assembler creates final Schema v1.1 by copying all authoritative Claim metadata and evidence mappings from the Validated Claim Store and using Stage 2 only for approved text, title, and ordering. It injects Data Context and required disclosures programmatically.

The assembled report then reruns the complete existing Schema v1.1, reference, Claim–Evidence, disclosure, `do_not_infer`, and Data Context validators. Stage 2 never fills Source IDs a second time.

## 12. Failure handling and audit state

No Stage receives an automatic LLM repair call.

- Stage 1 Provider/Schema failure: `claim_plan_status=rejected`, `report_generation_not_started=true`.
- Stage 1 partial Claim failure with adequate coverage: preserve rejected Claims and continue only with accepted Claims.
- Stage 1 inadequate coverage: stop before Stage 2.
- Stage 2 Provider/Schema failure: preserve the Validated Claim Store; report is rejected.
- Final coverage or final Validator failure: preserve both stage artifacts; report is rejected; Word is not generated.

Each run records independently:

```text
claim_plan_status
claim_validation_status
stage2_generation_status
final_claim_coverage_status
final_report_status
```

Every Provider call receives a new `client_request_id`; Provider IDs remain supplementary. Request IDs, model, hashes, tokens, cost status, latency, and call count propagate through stage and run manifests without entering Prompt bodies.

## 13. Offline Benchmark

The isolated corpus contains:

- the frozen real Tainan Evidence Pack from Phase 4.2;
- at least 10 complex end-to-end fixtures covering actor statements, allegations, multi-event inference, poll, no-poll, weak evidence, compound Claims, forward outlook, source ambiguity, and partial Coverage.

For each scenario, A and B use the same Evidence Pack business hash, formal-state hash, reporting period, Snapshot, Coverage, and declared `deepseek-v4-pro` model identifier. The single-stage implementation is not edited for benchmark advantage.

Offline fixtures are frozen before implementation-rule tuning. Holdout cases are not used to tune validators. The offline benchmark demonstrates architectural containment and deterministic gate behavior; it does not misrepresent fixtures as proof of real Provider stability.

Core metrics:

```text
claim_plan_schema_pass_rate
event_reference_accuracy
source_relationship_accuracy
claim_evidence_pass_rate
atomic_claim_rate
unsupported_claim_count
statement_as_fact_count
allegation_as_fact_count
final_claim_coverage_rate
stage2_new_claim_count
full_pipeline_pass_rate
```

Cost and performance record input/output tokens, cost or unavailable status, latency, and API-call count. Correctness and auditability take precedence over cost.

## 14. Writing-quality gate

The real Tainan previews are assessed across the fixed eight sections for:

```text
information_retention
analytical_depth
section_coherence
language_quality
redundancy
```

Each receives a documented 1–5 score using a fixed rubric. `analytical_quality_acceptable=true` requires:

- no dimension below 3;
- mean score at least 4;
- all accepted validated Claims retained exactly once;
- at least two legal `bounded_inference` Claims;
- at least one legal `forward_outlook` Claim;
- no degeneration into a list of direct facts;
- all eight business sections represented; S05 or S06 must contain the approved deterministic evidence-insufficiency disclosure when no substantive validated Claim exists.

Both `single_stage_report_preview` and `two_stage_report_preview` are preserved for review.

## 15. Offline outperformance decision

`two_stage_outperforms_single_stage=true` only when:

- B Event, Source relationship, Claim–Evidence, and final coverage rates are all 1.00;
- B unauthorized, fabricated Claim, Event, and Source counts are all zero;
- B full-pipeline pass rate is strictly higher than A on the isolated corpus;
- B meets the analysis-content minimum;
- B passes the writing-quality gate.

If any condition fails, no real Live call is permitted and no RC5 is created.

## 16. Live Benchmark budget and stability

Only after all offline gates pass:

```text
fresh single-stage baseline: at most 1 call
two-stage Stage 1: at most 3 calls
Stage 2: one call only for each legal Stage 1 result
```

All Live runs use the same frozen formal input and fixed code, Prompt, Schema, model, and configuration hashes. There is no mid-Benchmark tuning.

A complete two-stage Live pass requires:

```text
Stage 1 Schema PASS
Claim Plan reference/semantic PASS
minimum coverage PASS
Stage 2 Schema PASS
Final Claim Coverage PASS
final Schema v1.1 PASS
final reference and Claim–Evidence PASS
auditable request IDs
```

`two_stage_live_stable=true` requires two consecutive complete passes. `PASS / FAIL / PASS` is not stable. Stop immediately after two consecutive passes or after the third Stage 1 call. No fourth attempt is allowed.

## 17. Golden and regression tests

Add at least 40 golden cases:

- Stage 1: 15 calibration and 5 holdout.
- Stage 2/Coverage: 15 calibration and 5 holdout.
- zero skipped golden cases.

Add at least 10 end-to-end Benchmark fixtures separately.

Stage 1 metrics must be 1.00 for valid acceptance, invalid rejection, Event relationship, Source relationship, statement-as-fact rejection, allegation-as-fact rejection, and unsupported-Claim rejection.

Stage 2 metrics must be 1.00 for validated Claim usage, unauthorized-new-Claim rejection, and final coverage. Fabricated Claim, Event, and Source counts must be zero.

Required regression groups:

```text
Stage 1 specialized
Stage 2 specialized
Claim–Evidence
Phase 4.2
Phase 4.1
Phase 3 Assessment
Phase 3 Snapshot/Trigger
Phase 2.5 Recovery
Phase 2 Publication
Phase 1.5 Golden
complete pytest
```

Existing tests may not be deleted, skipped, xfailed, or weakened.

## 18. Implementation boundaries

Recommended focused modules:

```text
app/assessment/claim_plan_schema.py
app/assessment/claim_planner.py
app/assessment/claim_plan_validator.py
app/assessment/validated_claim_store.py
app/assessment/report_writer_stage2.py
app/assessment/final_claim_coverage_validator.py
app/assessment/two_stage_pipeline.py
```

Add Claim Planner and Stage 2 Prompts and internal Schemas under the existing Prompt and Schema directories. Extend Provider request-envelope construction through shared interfaces; do not fork Provider transport or normalization code.

Expose `assessment_generation_mode` through configuration and the appropriate CLI entry point. Its default remains `single_stage`.

## 19. Release decision

The implementation is experimental until all gates pass.

- Offline gate failure: stop, no Live, no RC5.
- Offline success and Live instability: stop, no RC5.
- Offline success, two consecutive Live passes, all quality/security/data gates pass: create `tainan-assessment-production-rc5`.

RC5 requires complete tests, release manifest, source and bundle hashes, archive SHA-256, independent extraction validation, critical-file verification, and credential-value scans. RC1–RC4 remain preserved.

Even if RC5 is created:

- production default remains `single_stage`;
- no Scheduler is installed or authorized;
- no Feishu report is sent;
- production is not activated.

## 20. Security and data protection

Scan actual configured DeepSeek keys, Feishu secrets, and Authorization values across source, Benchmark artifacts, logs, previews, Word temporary files, and RC5 surfaces when created. All actual-value match counts must be zero. Reasoning content and authorization material are never persisted.

Before/after hashes must prove formal state, Coverage, facts cutoff, and active Snapshot remain unchanged.

## 21. Deliverables

Write Phase 4.3 evidence under:

```text
deployment/phase4/two_stage_benchmark/
```

Required artifacts include:

- baseline and protected-hash manifests;
- frozen A/B input manifest;
- Stage 1 and Stage 2 Schema/Prompt/request audits;
- 40-case golden results and at least 10 end-to-end fixture results;
- Validated Claim Store examples and failure artifacts;
- A/B metrics and cost comparison;
- real single-stage and two-stage previews;
- writing-quality rubric and scores;
- Live audit when eligible;
- security scan and protected hashes after;
- RC5 validation only when eligible;
- `phase43_quality_gate.json`;
- final report covering all 74 requested items.

The phase stops after reporting. It does not activate production, change the production default, install Scheduler, or send Feishu.
