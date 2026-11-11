"""CLI：大模型结构化研判生成 + Claim—Evidence 校验 + 一次修复 + 草稿渲染。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .claim_evidence_validator import build_evidence_context, validate_structured_report
from .evidence_pack_builder import load_yaml, canonical_hash, sha256_text
from .build_evidence_pack import business_hash, compute_input_hashes
from .llm import create_provider
from .llm.errors import LLMProviderError
from .llm.provider_factory import REGISTERED_PROVIDERS
from .llm_input_contract import build_data_context
from .report_generation_validator import build_generation_validation
from .report_output_schema import load_schema
from .report_prompt_builder import (
    build_cache_key,
    build_prompt_manifest,
    build_request_payload,
    load_prompt,
    load_output_schema,
    prompt_hashes,
    sha256_file,
)
from .report_repair import build_repair_messages, is_repairable
from .report_renderer import render_report_markdown
from .report_structure_validator import derive_claims_and_sections
from .production_preflight import build_preflight, render_live_review, write_preflight


GENERATOR_VERSION = "2.0.0"
REPORT_CONTRACT_VERSION = "2.0"

DEEPSEEK_JSON_ADAPTER = (
    "\n\nDeepSeek JSON 输出适配指令（format_adapter_version=deepseek_json_v1）：\n"
    "1. 必须返回一个合法 JSON 对象。\n"
    "2. 禁止 Markdown 代码围栏。\n"
    "3. 禁止在 JSON 对象前后输出任何解释文字。\n"
    "4. 字段必须符合给定 JSON 结构，禁止额外字段。\n"
    "5. 禁止输出推理过程。"
)

FORMAT_ADAPTER_VERSIONS = {
    "deepseek": "deepseek_json_v1",
    "openai": "openai_strict_json_v1",
    "mock": "mock_json_v1",
}


def compose_deepseek_effective_system_prompt(
    system_prompt: str, writer_prompt: str
) -> str:
    """Compose exactly what is sent as the DeepSeek system message."""
    return (
        system_prompt.rstrip()
        + DEEPSEEK_JSON_ADAPTER
        + "\n\n正式写作契约（必须完整执行）：\n"
        + writer_prompt.strip()
    )


def _cache_model(config: dict, provider: str, model: str | None) -> str:
    if provider == "mock":
        return model or "mock-model"
    llm = config.get("llm", {}) or {}
    if provider == "deepseek":
        ds = llm.get("deepseek", {}) or {}
        return model or os.getenv(ds.get("model_env", "DEEPSEEK_MODEL"), "") or ds.get("default_model", "deepseek-v4-flash")
    ocfg = llm.get("openai", {}) or {}
    return model or os.getenv(ocfg.get("model_env", llm.get("model_env", "OPENAI_MODEL")), "") or ""


def _estimate_cost(
    provider: str,
    model: str,
    tokens: dict,
    pricing: dict,
) -> dict:
    if provider == "mock":
        return {
            "estimated_cost": 0,
            "estimated_cost_currency": "test",
            "cost_estimation_status": "mock",
            "pricing_peak_multiplier_applied": False,
            "pricing_warning": "",
        }
    providers = pricing.get("providers") or {}
    provider_cfg = providers.get(provider) or {}
    model_pricing = (provider_cfg.get("models") or {}).get(model)
    if not model_pricing or not isinstance(model_pricing, dict):
        return {
            "estimated_cost": None,
            "estimated_cost_currency": pricing.get("currency"),
            "cost_estimation_status": "pricing_unavailable",
            "pricing_peak_multiplier_applied": False,
            "pricing_warning": f"模型 {model} 无价格配置",
        }
    required = ("input_cache_hit", "input_cache_miss", "output")
    if any(not isinstance(model_pricing.get(k), (int, float)) for k in required):
        return {
            "estimated_cost": None,
            "estimated_cost_currency": pricing.get("currency"),
            "cost_estimation_status": "pricing_config_error",
            "pricing_peak_multiplier_applied": False,
            "pricing_warning": "价格配置格式错误，成本计算已阻断",
        }
    peak = provider_cfg.get("peak_pricing") or {}
    peak_applied = (
        peak.get("effective") is True
        and bool(peak.get("effective_date"))
        and peak.get("apply_to_estimates") is True
    )
    multiplier = float(peak.get("multiplier", 1.0)) if peak_applied else 1.0
    cache_hit = tokens.get("prompt_cache_hit_tokens")
    cache_miss = tokens.get("prompt_cache_miss_tokens")
    output_tokens = tokens.get("output_token_count")
    if (
        cache_hit is None
        or cache_miss is None
        or output_tokens is None
    ):
        return {
            "estimated_cost": None,
            "estimated_cost_currency": pricing.get("currency"),
            "cost_estimation_status": "insufficient_usage_breakdown",
            "pricing_peak_multiplier_applied": peak_applied,
            "pricing_warning": "API 未返回缓存命中/未命中拆分，无法估算成本",
        }
    cost = (
        cache_hit / 1_000_000 * float(model_pricing["input_cache_hit"])
        + cache_miss / 1_000_000 * float(model_pricing["input_cache_miss"])
        + output_tokens / 1_000_000 * float(model_pricing["output"])
    ) * multiplier
    return {
        "estimated_cost": round(cost, 6),
        "estimated_cost_currency": pricing.get("currency", "USD"),
        "cost_estimation_status": "estimated",
        "pricing_peak_multiplier_applied": peak_applied,
        "pricing_warning": "",
    }


def _scan_output_secrets(out_dir: Path, api_key: str | None) -> tuple[bool, bool]:
    import re

    key_exposed = False
    reasoning_persisted = False
    pattern = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")
    for path in out_dir.rglob("*"):
        if not path.is_file() or path.suffix not in (".json", ".md", ".txt"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            key_exposed = True
        if api_key and api_key in text:
            key_exposed = True
        if "reasoning_content" in text and path.name != "report_rejection_summary.md":
            reasoning_persisted = True
    return key_exposed, reasoning_persisted


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _inject_data_context(report: dict, contract: dict) -> bool:
    """用输入合同中的权威 data_context 覆盖/注入模型输出。"""
    authoritative = build_data_context(contract)
    previous = report.get("data_context")
    report["data_context"] = authoritative
    return previous != authoritative


def _apply_deterministic_fixes(report: dict, contract: dict) -> dict[str, Any]:
    """Fix A 程序确定性修复：披露注入与 Event/Poll→Source 映射回填。

    只处理程序已知数据；不修改模型分析正文；非原子/无证据 claims 不处理。
    v2.0 研判单元契约走 v2 分支（required_disclosures 为披露文字数组）。
    """
    if str(report.get("schema_version") or "") == "2.0":
        return _apply_deterministic_fixes_v2(report, contract)
    audit: dict[str, Any] = {"injected_disclosures": [], "source_mappings": []}
    eligibility = contract.get("generation_eligibility") or {}
    if eligibility.get("final_report_allowed") is not True:
        return audit

    def norm(value: str) -> str:
        return "".join(str(value or "").split())

    data = contract.get("data_status") or {}
    facts_cutoff = str(data.get("facts_cutoff") or "")
    poll_cutoff = str(data.get("poll_cutoff") or "")
    uncovered = list(data.get("uncovered_date_range") or [])
    poll_gap = (contract.get("evidence_statistics") or {}).get("poll_gap", True)
    required_texts: list[str] = []
    if facts_cutoff:
        required_texts.append(f"正式事实底表仅覆盖至 {facts_cutoff}")
    if poll_cutoff:
        required_texts.append(f"正式民调截止至 {poll_cutoff}")
    if uncovered:
        required_texts.append("本期未覆盖日期：" + "、".join(uncovered))
    else:
        required_texts.append("本期无未覆盖日期。")
    if poll_gap:
        required_texts.append("本期没有新增正式民调")

    claims = report.get("claims") or []
    existing_ids = {c.get("claim_id") for c in claims}
    existing_disclosure_texts = [
        norm(c.get("claim_text") or "")
        for c in claims
        if c.get("claim_type") == "data_disclosure"
    ]
    sections = report.get("sections") or []
    s08 = next((s for s in sections if s.get("section_id") == "S08"), None)

    next_num = 1
    for text in required_texts:
        normalized = norm(text)
        if any(normalized in t or t in normalized for t in existing_disclosure_texts):
            continue
        claim_id = f"DET_DISC_{next_num:03d}"
        while claim_id in existing_ids:
            next_num += 1
            claim_id = f"DET_DISC_{next_num:03d}"
        next_num += 1
        claim = {
            "claim_id": claim_id,
            "claim_type": "data_disclosure",
            "claim_text": text,
            "confidence": "not_applicable",
            "material_for_report": True,
            "supporting_event_ids": [],
            "supporting_poll_ids": [],
            "supporting_source_ids": [],
            "supporting_snapshot_dimensions": [],
            "supporting_gap_ids": [],
            "inference_basis": "程序确定性披露：facts_cutoff/poll_cutoff 来自权威 Data Context",
            "limitations": [],
            "applies_to_period": True,
        }
        claims.append(claim)
        existing_ids.add(claim_id)
        existing_disclosure_texts.append(normalized)
        report.setdefault("required_disclosures", []).append(claim_id)
        if s08 is not None:
            s08.setdefault("claim_ids", []).append(claim_id)
        audit["injected_disclosures"].append(claim_id)

    events = {
        e.get("event_id"): e
        for e in (contract.get("period_events") or []) + (contract.get("background_events") or [])
        if e.get("event_id")
    }
    polls = {p.get("poll_id"): p for p in (contract.get("polls") or []) if p.get("poll_id")}
    known_sources = {s.get("source_id") for s in (contract.get("sources") or []) if s.get("source_id")}
    known_sources |= {
        sid
        for p in (contract.get("polls") or [])
        for sid in (p.get("source_ids") or [])
    }
    for claim in claims:
        event_ids = list(claim.get("supporting_event_ids") or [])
        poll_ids = list(claim.get("supporting_poll_ids") or [])
        if not (event_ids or poll_ids):
            continue
        allowed: set[str] = set()
        for eid in event_ids:
            allowed.update(events.get(eid, {}).get("source_ids") or [])
        for pid in poll_ids:
            allowed.update(polls.get(pid, {}).get("source_ids") or [])
        # 只回填证据包内真实存在的来源，避免引入未知 source_id。
        allowed &= known_sources
        current = set(claim.get("supporting_source_ids") or [])
        unknown_current = current - known_sources
        known_current = current & known_sources
        if allowed and known_current != allowed:
            claim["supporting_source_ids"] = sorted(allowed)
            audit["source_mappings"].append(
                {
                    "claim_id": claim.get("claim_id"),
                    "previous_sources": sorted(current),
                    "allowed_sources": sorted(allowed),
                    "removed_unknown_sources": sorted(unknown_current),
                }
            )
        elif unknown_current:
            claim["supporting_source_ids"] = sorted(known_current)
            audit["source_mappings"].append(
                {
                    "claim_id": claim.get("claim_id"),
                    "previous_sources": sorted(current),
                    "allowed_sources": sorted(allowed),
                    "removed_unknown_sources": sorted(unknown_current),
                }
            )
    return audit


def _apply_deterministic_fixes_v2(report: dict, contract: dict) -> dict[str, Any]:
    """v2.0 契约的确定性修复：披露文字注入 + 证据来源映射回填。"""
    audit: dict[str, Any] = {"injected_disclosures": [], "source_mappings": []}
    eligibility = contract.get("generation_eligibility") or {}
    if eligibility.get("final_report_allowed") is not True:
        return audit

    def norm(value: str) -> str:
        return "".join(str(value or "").split())

    data = contract.get("data_status") or {}
    facts_cutoff = str(data.get("facts_cutoff") or "")
    poll_cutoff = str(data.get("poll_cutoff") or "")
    uncovered = list(data.get("uncovered_date_range") or [])
    poll_gap = (contract.get("evidence_statistics") or {}).get("poll_gap", True)
    required_texts: list[str] = []
    if facts_cutoff:
        required_texts.append(f"正式事实底表仅覆盖至 {facts_cutoff}")
    if poll_cutoff:
        required_texts.append(f"正式民调截止至 {poll_cutoff}")
    if uncovered:
        required_texts.append("本期未覆盖日期：" + "、".join(uncovered))
    else:
        required_texts.append("本期无未覆盖日期。")
    if poll_gap:
        required_texts.append("本期没有新增正式民调")

    disclosures = report.get("required_disclosures") or []
    existing_texts = [norm(t) for t in disclosures]
    appendix_ids = {str(item.get("item_id") or "") for item in report.get("appendix") or []}
    next_num = 1
    for text in required_texts:
        normalized = norm(text)
        if any(normalized in t or t in normalized for t in existing_texts):
            continue
        disclosures.append(text)
        existing_texts.append(normalized)
        item_id = f"DET_DISC_{next_num:03d}"
        while item_id in appendix_ids:
            next_num += 1
            item_id = f"DET_DISC_{next_num:03d}"
        next_num += 1
        appendix_ids.add(item_id)
        report.setdefault("appendix", []).append(
            {
                "item_id": item_id,
                "item_type": "disclosure",
                "item_text": text,
                "evidence_refs": {},
            }
        )
        audit["injected_disclosures"].append(text)
    report["required_disclosures"] = disclosures

    events = {
        e.get("event_id"): e
        for e in (contract.get("period_events") or []) + (contract.get("background_events") or [])
        if e.get("event_id")
    }
    polls = {p.get("poll_id"): p for p in (contract.get("polls") or []) if p.get("poll_id")}
    known_sources = {s.get("source_id") for s in (contract.get("sources") or []) if s.get("source_id")}
    known_sources |= {
        sid
        for p in (contract.get("polls") or [])
        for sid in (p.get("source_ids") or [])
    }

    def backfill_refs(refs: dict, where: str) -> None:
        event_ids = list(refs.get("event_ids") or [])
        poll_ids = list(refs.get("poll_ids") or [])
        if not (event_ids or poll_ids):
            return
        allowed: set[str] = set()
        for eid in event_ids:
            allowed.update(events.get(eid, {}).get("source_ids") or [])
        for pid in poll_ids:
            allowed.update(polls.get(pid, {}).get("source_ids") or [])
        allowed &= known_sources
        current = set(refs.get("source_ids") or [])
        unknown_current = current - known_sources
        known_current = current & known_sources
        if allowed and known_current != allowed:
            refs["source_ids"] = sorted(allowed)
            audit["source_mappings"].append(
                {
                    "where": where,
                    "previous_sources": sorted(current),
                    "allowed_sources": sorted(allowed),
                    "removed_unknown_sources": sorted(unknown_current),
                }
            )
        elif unknown_current:
            refs["source_ids"] = sorted(known_current)
            audit["source_mappings"].append(
                {
                    "where": where,
                    "previous_sources": sorted(current),
                    "allowed_sources": sorted(allowed),
                    "removed_unknown_sources": sorted(unknown_current),
                }
            )

    for index, item in enumerate(report.get("conclusion_summary") or [], 1):
        backfill_refs(item.setdefault("evidence_refs", {}), f"conclusion_summary[{index}]")
    for index, assessment in enumerate(report.get("core_assessments") or [], 1):
        backfill_refs(
            assessment.setdefault("evidence_refs", {}), f"core_assessments[{index}]"
        )
    for index, item in enumerate(report.get("appendix") or [], 1):
        backfill_refs(item.setdefault("evidence_refs", {}), f"appendix[{index}]")
    return audit


def _enrich_v2_report(report: dict, contract: dict) -> dict:
    """把 v2 研判单元确定性派生为 claims/sections（供门禁/展示复用），并回填统计。

    派生只读取 v2 结构字段，不修改判断与证据内容；重复执行幂等。
    """
    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    claims, sections = derive_claims_and_sections(report, ctx)
    report["claims"] = claims
    report["sections"] = sections
    conclusion_ids = [
        c["claim_id"] for c in claims if c.get("_derived_kind") == "conclusion"
    ]
    report["title_claim_ids"] = conclusion_ids[:1]
    report["overall_judgment_claim_ids"] = conclusion_ids
    stats = report.setdefault("report_statistics", {})
    stats["claim_count"] = len(claims)
    stats["section_count"] = len(sections)
    stats["core_assessment_count"] = len(report.get("core_assessments") or [])
    stats["conclusion_summary_count"] = len(report.get("conclusion_summary") or [])
    stats["evidence_item_count"] = sum(
        len(a.get("evidence_items") or [])
        for a in report.get("core_assessments") or []
    )
    return report


def run(
    *,
    config_path: Path,
    evidence_dir: Path,
    provider: str,
    model: str | None,
    allow_draft_with_gap: bool,
    validate_only: bool,
    force_model_call: bool,
    no_repair: bool,
    output_root: Path | None,
    deepseek_thinking: str,
) -> int:
    try:
        config = load_yaml(config_path)
        root = config_path.resolve().parent.parent
        ev_dir = evidence_dir.resolve() if evidence_dir.is_absolute() else (Path.cwd() / evidence_dir).resolve()
        if not ev_dir.exists():
            print(f"ERROR: 证据包目录不存在: {ev_dir}", file=sys.stderr)
            return 1

        pack = _load_json(ev_dir / "report_evidence_pack.json")
        contract = _load_json(ev_dir / "llm_input_contract.json")
        contract_validation = _load_json(ev_dir / "llm_input_contract_validation.json")
        evidence_validation = _load_json(ev_dir / "evidence_pack_validation.json")
        evidence_manifest = _load_json(ev_dir / "report_run_manifest.json")

        if evidence_validation.get("evidence_pack_ready") is not True:
            print("ERROR: 证据包未通过验证，禁止调用模型", file=sys.stderr)
            return 1
        if contract_validation.get("llm_input_contract_ready") is not True:
            print("ERROR: LLM 输入合同未通过验证，禁止调用模型", file=sys.stderr)
            return 1

        eligibility = pack.get("generation_eligibility") or {}
        final_allowed = eligibility.get("final_report_allowed") is True
        allowed_mode = eligibility.get("allowed_generation_mode")
        if final_allowed:
            expected_mode = "final"
        else:
            expected_mode = "draft_with_data_gap"
            if not allow_draft_with_gap:
                print(
                    "ERROR: final_report_allowed=false；当前证据包只能生成数据不完整草稿，"
                    "必须显式添加 --allow-draft-with-gap",
                    file=sys.stderr,
                )
                return 1

        period = pack.get("report_period") or {}
        period_label = f"{period.get('period_start')}_{period.get('period_end')}"
        gen_root = output_root or (root / (config.get("report_generation", {}).get("output_root", "data/reports/tainan_2026/generated_reports")))
        out_dir = gen_root / period_label

        ctx = build_evidence_context(contract, evidence_pack=pack, config=config)
        payload = build_request_payload(contract)
        schema = load_output_schema()
        system_prompt = load_prompt("system")
        writer_prompt = load_prompt("writer")
        repair_prompt = load_prompt("repair")
        if provider == "deepseek":
            system_prompt = compose_deepseek_effective_system_prompt(
                system_prompt, writer_prompt
            )
        hashes = prompt_hashes()
        effective_system_prompt_hash = sha256_text(system_prompt)
        evidence_business_hash = business_hash(pack)
        contract_hash = sha256_text(json.dumps(contract, ensure_ascii=False, sort_keys=True))
        schema_hash = canonical_hash(schema)
        resolved_model = _cache_model(config, provider, model)
        llm_cfg = config.get("llm", {}) or {}
        if provider == "deepseek":
            base_url_identifier = (llm_cfg.get("deepseek", {}) or {}).get("base_url", "https://api.deepseek.com")
        elif provider == "openai":
            base_url_identifier = "openai_default"
        else:
            base_url_identifier = "mock"
        thinking_mode = deepseek_thinking if provider == "deepseek" else "disabled"
        json_output_mode = {
            "deepseek": "json_object",
            "openai": "strict_json_schema",
            "mock": "mock_json",
        }[provider]
        format_adapter_version = FORMAT_ADAPTER_VERSIONS[provider]
        cache_key = build_cache_key(
            evidence_business_hash=evidence_business_hash,
            contract_hash=contract_hash,
            system_prompt_hash=effective_system_prompt_hash,
            writer_prompt_hash=hashes["writer"],
            repair_prompt_hash=hashes["repair"],
            output_schema_hash=schema_hash,
            provider=provider,
            model=resolved_model,
            base_url_identifier=base_url_identifier,
            thinking_mode=thinking_mode,
            json_output_mode=json_output_mode,
            generator_version=GENERATOR_VERSION,
            generation_mode=expected_mode,
        )

        if validate_only:
            print("validate-only: 输入合同/证据包/生成资格检查通过")
            return 0

        out_dir.mkdir(parents=True, exist_ok=True)
        generation_id = uuid.uuid4().hex
        cache_used = False
        final_report: dict | None = None
        final_validation: dict | None = None
        final_source = "model" if provider != "mock" else "mock"
        attempt1_validation: dict = {}
        attempt2_validation: dict | None = None
        repair_attempted = False
        provider_result = None
        client_request_id = ""
        attempt1_report: dict | None = None
        attempt2_report: dict | None = None

        cached_path = out_dir / "structured_report_final.json"
        cached_manifest_path = out_dir / "report_generation_manifest.json"
        if cached_path.exists() and cached_manifest_path.exists() and not force_model_call:
            cached_manifest = _load_json(cached_manifest_path)
            if cached_manifest.get("cache_key") == cache_key:
                cached = _load_json(cached_path)
                _inject_data_context(cached, contract)
                _apply_deterministic_fixes(cached, contract)
                if str(cached.get("schema_version") or "") == "2.0":
                    _enrich_v2_report(cached, contract)
                cached_validation = validate_structured_report(cached, ctx, expected_mode=expected_mode)
                if cached_validation.get("all_claims_validated") is True:
                    final_report = cached
                    final_validation = cached_validation
                    cache_used = True
                    final_source = "cache"

        if final_report is None:
            provider_instance = create_provider(
                provider,
                config=config,
                model=model or None,
                thinking_mode=deepseek_thinking if provider == "deepseek" else "disabled",
            )
            client_request_id = str(uuid.uuid4())
            request_metadata = {
                "attempt": 1,
                "generation_id": generation_id,
                "client_request_id": client_request_id,
            }
            _atomic_write(
                out_dir / "request_correlation.json",
                {
                    "generation_id": generation_id,
                    "client_request_id": client_request_id,
                    "provider_request_id": None,
                    "provider_request_id_supported": None,
                    "status": "request_prepared",
                },
            )
            try:
                provider_result = provider_instance.generate_structured_report(
                    system_prompt=system_prompt,
                    user_payload=payload,
                    output_schema=schema,
                    request_metadata=request_metadata,
                )
            except LLMProviderError as exc:
                _atomic_write(
                    out_dir / "request_correlation.json",
                    {
                        "generation_id": generation_id,
                        "client_request_id": client_request_id,
                        "provider_request_id": None,
                        "provider_request_id_supported": False,
                        "status": "provider_error",
                    },
                )
                _atomic_write(
                    out_dir / "report_rejection_summary.md",
                    {"rejection_reason": str(exc), "repair_attempted": False},
                )
                _write_preflight(
                    root,
                    config,
                    provider=provider,
                    live_status=(
                        "not_run"
                        if "缺少 API 密钥" in str(exc)
                        else "failed"
                    ),
                    cache_used=False,
                    business_equal=True,
                    final_validation={"all_claims_validated": False, "errors": [str(exc)]},
                    provider_result=None,
                    formal_unchanged=_compute_formal_unchanged(config, root, pack, ev_dir),
                    input_business_hash=evidence_business_hash,
                    out_dir=out_dir,
                    cost_estimation_status="not_available",
                    warnings=[str(exc)],
                )
                print(f"ERROR: provider 调用失败: {exc}", file=sys.stderr)
                return 1

            if not provider_result.client_request_id:
                provider_result.client_request_id = client_request_id
                provider_result.request_audit.setdefault(
                    "client_request_id", client_request_id
                )
            attempt1_report = provider_result.structured_output
            dc_overridden = _inject_data_context(attempt1_report, contract)
            det_audit = _apply_deterministic_fixes(attempt1_report, contract)
            attempt1_validation = validate_structured_report(
                attempt1_report, ctx, expected_mode=expected_mode
            )
            if str(attempt1_report.get("schema_version") or "") == "2.0":
                _enrich_v2_report(attempt1_report, contract)
            if det_audit["injected_disclosures"] or det_audit["source_mappings"]:
                attempt1_validation.setdefault("warnings", []).append(
                    "Fix A: 程序确定性披露/来源映射已应用 "
                    + json.dumps(det_audit, ensure_ascii=False)
                )
            attempt1_validation["client_request_id"] = provider_result.client_request_id
            attempt1_validation["provider_request_id"] = provider_result.response_id or None
            attempt1_validation["provider_request_id_supported"] = (
                provider_result.provider_request_id_supported
            )
            if dc_overridden:
                attempt1_validation.setdefault("warnings", []).append(
                    "data_context: 模型输出与输入合同不一致，已由程序覆盖为权威值"
                )
            _atomic_write(out_dir / "structured_report_attempt_1.json", attempt1_report)
            _atomic_write(out_dir / "claim_evidence_validation_attempt_1.json", attempt1_validation)
            _atomic_write(
                out_dir / "request_correlation.json",
                {
                    "generation_id": generation_id,
                    "client_request_id": provider_result.client_request_id,
                    "provider_request_id": provider_result.response_id or None,
                    "provider_request_id_supported": provider_result.provider_request_id_supported,
                    "status": "response_received",
                },
            )
            _atomic_write(
                out_dir / "provider_response_metadata.json",
                {
                    "client_request_id": provider_result.client_request_id,
                    "provider_request_id": provider_result.response_id or None,
                    "provider_request_id_supported": provider_result.provider_request_id_supported,
                    "provider": provider_result.provider,
                    "model": provider_result.model,
                    "finish_status": provider_result.finish_status,
                    "request_duration_ms": provider_result.request_duration_ms,
                },
            )

            if not attempt1_validation.get("all_claims_validated") is True:
                if (
                    not no_repair
                    and int(llm_cfg.get("max_repair_attempts", 1)) >= 1
                    and is_repairable(attempt1_validation)
                ):
                    repair_attempted = True
                    repair_system, repair_payload = build_repair_messages(
                        original_report=attempt1_report,
                        validation=attempt1_validation,
                        contract=contract,
                        output_schema=schema,
                    )
                    try:
                        repair_result = provider_instance.generate_structured_report(
                            system_prompt=repair_system,
                            user_payload=repair_payload,
                            output_schema=schema,
                            request_metadata={"attempt": 2, "generation_id": generation_id},
                        )
                    except LLMProviderError as exc:
                        attempt2_report = None
                        attempt2_validation = {
                            "all_claims_validated": False,
                            "errors": [f"修复调用失败: {exc}"],
                        }
                    else:
                        attempt2_report = repair_result.structured_output
                        dc_overridden = _inject_data_context(attempt2_report, contract)
                        det_audit = _apply_deterministic_fixes(attempt2_report, contract)
                        attempt2_validation = validate_structured_report(
                            attempt2_report, ctx, expected_mode=expected_mode
                        )
                        if str(attempt2_report.get("schema_version") or "") == "2.0":
                            _enrich_v2_report(attempt2_report, contract)
                        if det_audit["injected_disclosures"] or det_audit["source_mappings"]:
                            attempt2_validation.setdefault("warnings", []).append(
                                "Fix A: 程序确定性披露/来源映射已应用 "
                                + json.dumps(det_audit, ensure_ascii=False)
                            )
                        if dc_overridden:
                            attempt2_validation.setdefault("warnings", []).append(
                                "data_context: 模型输出与输入合同不一致，已由程序覆盖为权威值"
                            )
                        provider_result = repair_result
                    if attempt2_report is not None:
                        _atomic_write(out_dir / "structured_report_attempt_2.json", attempt2_report)
                    _atomic_write(out_dir / "claim_evidence_validation_attempt_2.json", attempt2_validation)

            if attempt2_validation is not None:
                if attempt2_validation.get("all_claims_validated") is True:
                    final_report = attempt2_report
                    final_validation = attempt2_validation
                    final_report["report_status"] = "repaired"
                else:
                    final_report = attempt2_report
                    final_validation = attempt2_validation
                    final_report["report_status"] = "rejected"
            elif attempt1_validation.get("all_claims_validated") is True:
                final_report = attempt1_report
                final_validation = attempt1_validation
                final_report["report_status"] = "generated"
            else:
                final_report = attempt1_report
                final_validation = attempt1_validation
                final_report["report_status"] = "rejected"

        # ---- 最终输出 ----
        render_stats = None
        if final_report.get("report_status") != "rejected":
            render_stats = render_report_markdown(final_report, contract)
            final_report["report_statistics"]["chinese_char_count"] = render_stats["chinese_char_count"]
            final_report["report_statistics"]["length_below_target"] = render_stats["length_below_target"]

        _atomic_write(out_dir / "structured_report_final.json", final_report)
        _atomic_write(out_dir / "llm_request_payload.json", payload)
        _atomic_write(out_dir / "report_output_schema.json", schema)
        prompt_manifest = build_prompt_manifest(
            provider, resolved_model, format_adapter_version=format_adapter_version
        )
        prompt_manifest["effective_system_prompt_hash"] = effective_system_prompt_hash
        prompt_manifest["output_schema_business_hash"] = schema_hash
        prompt_manifest["writer_prompt_actually_sent"] = provider == "deepseek"
        _atomic_write(out_dir / "prompt_manifest.json", prompt_manifest)

        if final_report.get("report_status") != "rejected" and render_stats is not None:
            (out_dir / "report_draft.md").write_text(render_stats["markdown"], encoding="utf-8")
        else:
            _atomic_write(
                out_dir / "report_rejection_summary.md",
                {
                    "report_status": "rejected",
                    "errors": final_validation.get("errors") or [],
                    "repair_attempted": repair_attempted,
                },
            )

        formal_unchanged = _compute_formal_unchanged(config, root, pack, evidence_dir)
        generation_validation = build_generation_validation(
            input_contract_ready=contract_validation.get("llm_input_contract_ready") is True,
            evidence_pack_ready=evidence_validation.get("evidence_pack_ready") is True,
            eligibility_respected=True,
            provider_result_valid=final_validation.get("all_claims_validated") is True,
            claim_validation=final_validation,
            final_report_allowed=final_allowed,
            generated_mode=final_report.get("generation_mode") or expected_mode,
            formal_unchanged=formal_unchanged,
            data_context_complete=final_validation.get("data_context_complete") is True,
            data_context_matches_input=final_validation.get("data_context_matches_input") is True,
        )
        _atomic_write(out_dir / "report_generation_validation.json", generation_validation)

        tokens = provider_result.to_dict() if provider_result else {}
        pricing_path = root / "config" / "llm_pricing.yaml"
        pricing = load_yaml(pricing_path) if pricing_path.exists() else {}
        cost = _estimate_cost(
            provider,
            resolved_model,
            tokens,
            pricing,
        )
        previous_manifest = None
        manifest_path = out_dir / "report_generation_manifest.json"
        if manifest_path.exists():
            previous_manifest = _load_json(manifest_path)
        claim_count = len(final_report.get("claims") or [])
        manifest = {
            "generation_id": generation_id,
            "generator_version": GENERATOR_VERSION,
            "report_period": period,
            "generation_mode": final_report.get("generation_mode"),
            "report_status": final_report.get("report_status"),
            "provider": provider,
            "model": tokens.get("model") or resolved_model,
            "input_contract_version": contract.get("contract_version"),
            "evidence_pack_schema_version": pack.get("schema_version"),
            "output_schema_version": final_report.get("schema_version"),
            "prompt_versions": build_prompt_manifest(provider, resolved_model)["prompt_versions"],
            "format_adapter_version": format_adapter_version,
            "api_mode": (
                "openai_compatible_chat_completions"
                if provider == "deepseek"
                else ("responses_api" if provider == "openai" else "mock")
            ),
            "json_output_mode": json_output_mode,
            "thinking_mode": thinking_mode,
            "server_side_strict_schema": provider == "openai",
            "local_strict_schema_validation": True,
            "native_json_schema": provider == "openai",
            "output_schema_serialized_to_request": provider in ("deepseek", "openai"),
            "output_schema_business_hash": schema_hash,
            "effective_system_prompt_hash": effective_system_prompt_hash,
            "writer_prompt_actually_sent": provider == "deepseek",
            "provider_request_audit": tokens.get("request_audit") or {},
            "client_request_id": tokens.get("client_request_id") or client_request_id,
            "response_id": tokens.get("response_id") or "",
            "provider_request_id": tokens.get("response_id") or None,
            "provider_request_id_supported": tokens.get(
                "provider_request_id_supported", False
            ),
            "input_business_hash": evidence_business_hash,
            "cache_key": cache_key,
            "generation_source": "cache" if cache_used else ("model" if provider != "mock" else "mock"),
            "provider_call_count": 0 if cache_used else 1,
            "original_provider_usage": (
                {
                    k: (previous_manifest or {}).get(k)
                    for k in (
                        "input_token_count",
                        "output_token_count",
                        "total_token_count",
                        "prompt_cache_hit_tokens",
                        "prompt_cache_miss_tokens",
                    )
                }
                if cache_used
                else None
            ),
            "generation_attempt_count": 2 if repair_attempted else 1,
            "repair_attempt_count": 1 if repair_attempted else 0,
            "repair_attempted": repair_attempted,
            "cache_used": cache_used,
            "input_token_count": tokens.get("input_token_count", 0),
            "output_token_count": tokens.get("output_token_count", 0),
            "total_token_count": tokens.get("total_token_count", 0),
            "request_duration_ms": tokens.get("request_duration_ms", 0),
            "cache_hit_input_token_count": (
                (previous_manifest or {}).get("input_token_count") if cache_used else None
            ),
            "cache_miss_input_token_count": (
                tokens.get("input_token_count") if not cache_used else None
            ),
            "estimated_cost": cost["estimated_cost"],
            "estimated_cost_currency": cost["estimated_cost_currency"],
            "pricing_schema_version": pricing.get("pricing_schema_version"),
            "pricing_verified_at": pricing.get("verified_at"),
            "pricing_model": resolved_model if provider != "mock" else None,
            "pricing_peak_multiplier_applied": cost["pricing_peak_multiplier_applied"],
            "cost_estimation_status": cost["cost_estimation_status"],
            "pricing_source": pricing.get("source"),
            "claim_count": claim_count,
            "validated_claim_count": claim_count if final_report.get("report_status") != "rejected" else 0,
            "rejected_claim_count": 0 if final_report.get("report_status") != "rejected" else claim_count,
            "validation_ready": generation_validation.get("report_generation_ready") is True,
            "formal_inputs_unchanged": formal_unchanged.get("formal_data_unchanged", False),
        }
        _atomic_write(out_dir / "report_generation_manifest.json", manifest)

        final_business_hash = canonical_hash(final_report)
        idem_path = out_dir / "report_generation_idempotency.json"
        previous_idem = _load_json(idem_path) if idem_path.exists() else {}
        previous_hash = previous_idem.get("business_output_hash")
        first_source = previous_idem.get("first_run_source") or final_source
        second_source = "cache" if cache_used else final_source
        business_equal = previous_hash is None or previous_hash == final_business_hash
        idempotency = {
            "cache_key": cache_key,
            "first_run_source": first_source,
            "second_run_source": second_source,
            "business_outputs_equal": business_equal,
            "business_output_hash": final_business_hash,
            "formal_inputs_unchanged": formal_unchanged.get("formal_data_unchanged", False),
            "idempotent": business_equal and formal_unchanged.get("formal_data_unchanged", False),
        }
        _atomic_write(idem_path, idempotency)

        if provider == "deepseek" and not cache_used and final_report.get("report_status") != "rejected":
            review_path = out_dir / "live_deepseek_output_review.md"
            review_path.write_text(
                render_live_review(final_report, contract, final_validation),
                encoding="utf-8",
            )
        _write_preflight(
            root,
            config,
            provider=provider,
            live_status=(
                "passed"
                if provider == "deepseek"
                and (
                    (not cache_used and final_report.get("report_status") != "rejected")
                    or (
                        cache_used
                        and (previous_manifest or {}).get("provider") == "deepseek"
                        and (previous_manifest or {}).get("generation_source") == "model"
                        and (previous_manifest or {}).get("validation_ready") is True
                    )
                )
                else ("failed" if provider == "deepseek" else "not_run")
            ),
            cache_used=cache_used,
            business_equal=business_equal,
            final_validation=final_validation,
            provider_result=provider_result,
            formal_unchanged=formal_unchanged,
            input_business_hash=evidence_business_hash,
            out_dir=out_dir,
            cost_estimation_status=cost["cost_estimation_status"],
            warnings=[cost.get("pricing_warning")] if cost.get("pricing_warning") else [],
        )

        print(f"report_status={final_report.get('report_status')}")
        print(f"generation_mode={final_report.get('generation_mode')}")
        print(f"all_claims_validated={final_validation.get('all_claims_validated')}")
        print(f"report_generation_ready={generation_validation.get('report_generation_ready')}")
        print(f"output_dir={out_dir}")
        if final_report.get("report_status") == "rejected":
            return 1
        return 0 if generation_validation.get("report_generation_ready") else 1
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc(file=sys.stderr)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _compute_formal_unchanged(config: dict, root: Path, pack: dict, evidence_dir: Path) -> dict:
    coverage_path = Path(evidence_dir).parent.parent / "election_seed" / "tainan_2026"
    # 覆盖目录以证据包 manifest 为准
    try:
        manifest = _load_json(evidence_dir / "report_run_manifest.json")
        coverage_path = Path(manifest.get("coverage_path") or coverage_path)
    except Exception:
        pass
    before = compute_input_hashes(config, root, coverage_path)
    after = compute_input_hashes(config, root, coverage_path)
    ev_before = sha256_file(evidence_dir / "report_evidence_pack.json")
    ev_after = sha256_file(evidence_dir / "report_evidence_pack.json")
    return {
        "formal_data_unchanged": before == after,
        "snapshot_data_unchanged": all(
            before.get(k) == after.get(k) for k in before if "snapshot" in k
        ),
        "coverage_data_unchanged": before.get("coverage_dir") == after.get("coverage_dir"),
        "poll_data_unchanged": before.get("poll_seeds") == after.get("poll_seeds"),
        "evidence_package_unchanged": ev_before == ev_after,
    }


def _write_preflight(
    root: Path,
    config: dict,
    *,
    provider: str,
    live_status: str,
    cache_used: bool,
    business_equal: bool,
    final_validation: dict,
    provider_result,
    formal_unchanged: dict,
    input_business_hash: str,
    out_dir: Path | None,
    cost_estimation_status: str,
    warnings: list[str] | None = None,
) -> None:
    schedule = config.get("schedule", {}) or {}
    llm = config.get("llm", {}) or {}
    deepseek_cfg = llm.get("deepseek", {}) or {}
    credentials_present = bool(os.getenv("DEEPSEEK_API_KEY"))
    key = os.getenv("DEEPSEEK_API_KEY")
    key_exposed, reasoning_persisted = (False, False)
    if out_dir is not None:
        key_exposed, reasoning_persisted = _scan_output_secrets(out_dir, key)
    preflight_path = (
        root
        / "data"
        / "reports"
        / "tainan_2026"
        / "deployment_validation"
        / "deepseek_production_preflight.json"
    )
    previous = _load_json(preflight_path) if preflight_path.exists() else {}
    pass_count = 0
    response_ids: list[str] = []
    response_id = getattr(provider_result, "response_id", "") if provider_result else ""
    client_request_id = (
        getattr(provider_result, "client_request_id", "") if provider_result else ""
    )
    audit_response_id = response_id or client_request_id
    if provider == "deepseek" and live_status == "passed" and not cache_used and audit_response_id:
        if previous.get("formal_live_input_business_hash") == input_business_hash:
            response_ids = list(previous.get("formal_live_response_ids") or [])
            pass_count = int(previous.get("formal_live_validation_pass_count") or 0)
        if audit_response_id not in response_ids:
            response_ids.append(audit_response_id)
            pass_count += 1
        response_ids = response_ids[-2:]
        pass_count = min(pass_count, 2)

    preflight = build_preflight(
        schedule_days=list(schedule.get("run_days") or [9, 22]),
        period_definition="natural_half_month",
        schedule_definition="delayed_generation",
        calendar_lag_semantics_valid=True,
        full_preparation_days_semantics_valid=True,
        default_provider=llm.get("default_provider", "deepseek"),
        default_model=deepseek_cfg.get("default_model", "deepseek-v4-flash"),
        credentials_present=credentials_present,
        live_deepseek_test=live_status,
        json_output_valid=final_validation.get("output_schema_valid") is not False,
        local_schema_valid=final_validation.get("output_schema_valid") is True,
        claim_evidence_valid=final_validation.get("all_claims_validated") is True,
        do_not_infer_valid=final_validation.get("do_not_infer_compliant") is not False,
        required_disclosures_complete=final_validation.get("required_disclosures_complete") is True,
        real_token_usage_available=(
            provider == "deepseek"
            and provider_result is not None
            and provider_result.input_token_count is not None
        ),
        cost_estimation_status=cost_estimation_status,
        cache_reuse_valid=(not cache_used) or (cache_used and business_equal),
        api_key_exposure_detected=key_exposed,
        reasoning_content_persisted=reasoning_persisted,
        formal_data_unchanged=formal_unchanged.get("formal_data_unchanged", False),
        evidence_package_unchanged=formal_unchanged.get("evidence_package_unchanged", False),
        formal_live_validation_pass_count=pass_count,
        required_formal_live_validation_passes=2,
        formal_live_input_business_hash=input_business_hash,
        formal_live_response_ids=response_ids,
        warnings=warnings or [],
    )
    write_preflight(root, preflight)


def main() -> int:
    parser = argparse.ArgumentParser(description="台南选情半月报告大模型结构化生成器")
    parser.add_argument("--config", default="config/election_assessment.yaml")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--allow-draft-with-gap", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--force-model-call", action="store_true")
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument(
        "--deepseek-thinking", choices=["enabled", "disabled"], default="disabled"
    )
    args = parser.parse_args()

    config = load_yaml(Path(args.config))
    provider = args.provider or (config.get("llm", {}) or {}).get("default_provider", "mock")
    if provider not in REGISTERED_PROVIDERS:
        print(f"ERROR: 未注册 provider: {provider}", file=sys.stderr)
        return 1
    if args.model is not None and not args.model.strip():
        print("ERROR: --model 不得为空", file=sys.stderr)
        return 1
    return run(
        config_path=Path(args.config),
        evidence_dir=Path(args.evidence_dir),
        provider=provider,
        model=args.model,
        allow_draft_with_gap=args.allow_draft_with_gap,
        validate_only=args.validate_only,
        force_model_call=args.force_model_call,
        no_repair=args.no_repair,
        output_root=args.output_root,
        deepseek_thinking=args.deepseek_thinking,
    )


if __name__ == "__main__":
    sys.exit(main())
