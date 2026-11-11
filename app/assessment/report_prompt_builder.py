"""提示词与 LLM 请求载荷构建（只读 llm_input_contract.json）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .llm_input_contract import ALLOWED_TOP_LEVEL_FIELDS, build_data_context
from .llm_input_contract import _scan_prohibited


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

PROMPT_FILES = {
    "system": "tainan_report_system_v2.txt",
    "writer": "tainan_report_writer_v2.txt",
    "repair": "tainan_report_repair_v2.txt",
}
PROMPT_VERSIONS = {key: "2.0" for key in PROMPT_FILES}


class PromptBuildError(RuntimeError):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def load_prompt(name: str) -> str:
    if name not in PROMPT_FILES:
        raise PromptBuildError(f"未知提示词: {name}")
    path = PROMPT_DIR / PROMPT_FILES[name]
    return path.read_text(encoding="utf-8")


def prompt_hashes() -> dict[str, str]:
    return {key: sha256_file(PROMPT_DIR / fname) for key, fname in PROMPT_FILES.items()}


def load_output_schema() -> dict:
    path = SCHEMA_DIR / "tainan_assessment_report_v2.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_request_payload(contract: dict) -> dict:
    payload = {key: contract.get(key) for key in ALLOWED_TOP_LEVEL_FIELDS}
    payload["data_context"] = build_data_context(contract)
    missing = [k for k in ALLOWED_TOP_LEVEL_FIELDS if payload.get(k) is None]
    if missing:
        raise PromptBuildError(f"合同缺少必需字段: {missing}")
    errors = _scan_prohibited(payload)
    if errors:
        raise PromptBuildError("请求载荷包含禁止内容：" + "; ".join(errors))
    return payload


def build_prompt_manifest(
    provider: str, model: str, format_adapter_version: str | None = None
) -> dict:
    manifest = {
        "prompt_versions": dict(PROMPT_VERSIONS),
        "prompt_hashes": prompt_hashes(),
        "provider": provider,
        "model": model,
    }
    if format_adapter_version:
        manifest["format_adapter_version"] = format_adapter_version
    return manifest


def build_cache_key(
    *,
    evidence_business_hash: str,
    contract_hash: str,
    system_prompt_hash: str,
    writer_prompt_hash: str,
    repair_prompt_hash: str,
    output_schema_hash: str,
    provider: str,
    model: str,
    base_url_identifier: str,
    thinking_mode: str,
    json_output_mode: str,
    generator_version: str,
    generation_mode: str,
) -> str:
    parts = [
        ("evidence", evidence_business_hash),
        ("contract", contract_hash),
        ("system", system_prompt_hash),
        ("writer", writer_prompt_hash),
        ("repair", repair_prompt_hash),
        ("schema", output_schema_hash),
        ("provider", provider),
        ("model", model),
        ("base_url", base_url_identifier),
        ("thinking", thinking_mode),
        ("json_mode", json_output_mode),
        ("generator", generator_version),
        ("mode", generation_mode),
    ]
    return sha256_text(json.dumps(parts, ensure_ascii=False, sort_keys=True))
