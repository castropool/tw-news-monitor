"""Stage 2 writer request construction for one-to-one Claim rendering."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .claim_plan_schema import load_stage2_draft_schema


PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "tainan_report_writer_stage2_v1.txt"


def load_stage2_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_stage2_request(stage2_input: dict) -> dict:
    """Build the auditable Stage 2 envelope without evidence identifiers."""

    return {
        "contract_envelope_version": "report_writer_stage2_envelope_v1",
        "stage2_input": deepcopy(stage2_input),
        "output_contract": {
            "schema_version": "1.0",
            "json_schema": load_stage2_draft_schema(),
        },
    }
