"""Research programs P1–P6: configuration, tagging, extraction, drafts, assistant.

Config: ``config/programs.yaml``.  Everything here is off in the regular run
unless its switch is set, and never blocks news delivery.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "programs.yaml"
PROGRAM_IDS = ("P1", "P2", "P3", "P4", "P5", "P6")


class ProgramConfigError(ValueError):
    pass


def validate(cfg: dict) -> dict:
    def need(cond, msg):
        if not cond:
            raise ProgramConfigError(msg)

    need(isinstance(cfg, dict) and cfg.get("version") == 1, "version 1 required")
    ladder = cfg.get("evidence_ladder")
    need(isinstance(ladder, list) and len(ladder) == 6, "evidence_ladder must list 6 stages")
    need(set(cfg.get("ladder_labels") or {}) == set(ladder), "ladder_labels must cover the ladder")
    programs = cfg.get("programs")
    need(isinstance(programs, dict) and set(programs) == set(PROGRAM_IDS), "programs P1..P6 required")
    for pid, p in programs.items():
        need(isinstance(p.get("name"), str) and p["name"], f"{pid}: name")
        hyps = p.get("hypotheses")
        need(isinstance(hyps, dict) and len(hyps) >= 3, f"{pid}: at least three rival hypotheses")
        qs = p.get("questions")
        need(isinstance(qs, list) and qs, f"{pid}: questions")
        for q in qs:
            need(str(q.get("id", "")).startswith(pid + "-Q"), f"{pid}: question id {q.get('id')}")
            need(q.get("text") and q.get("indicators"), f"{q.get('id')}: text and indicators required")
        r = p.get("routing") or {}
        need(isinstance(r.get("strong", []), list) and isinstance(r.get("support", []), list), f"{pid}: routing")
        need(isinstance(r.get("min_support", 2), int), f"{pid}: min_support")
        need(isinstance(r.get("require_any", []), list), f"{pid}: require_any")
        need(isinstance(r.get("require_also", []), list), f"{pid}: require_also")
    return cfg


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return validate(yaml.safe_load(f))


def load(path: str | Path | None = None) -> dict:
    return _load(str(path or CONFIG_PATH))


def program(pid: str, path: str | Path | None = None) -> dict:
    cfg = load(path)
    if pid not in cfg["programs"]:
        raise ProgramConfigError(f"unknown program {pid}")
    return cfg["programs"][pid]
