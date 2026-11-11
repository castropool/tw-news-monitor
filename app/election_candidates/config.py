from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "election_candidate_pipeline.yaml"


@dataclass
class CandidatePipelineConfig:
    raw: dict[str, Any]
    root: Path = PROJECT_ROOT

    def __post_init__(self):
        self.root = Path(self.root)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CandidatePipelineConfig":
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls(raw=raw)

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            for prefix in ("data/", "config/", "dist/", "release/"):
                if value.startswith(prefix):
                    return self.root / value
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return self._resolve(node)

    @property
    def pipeline_version(self) -> str:
        return str(self.get("versions.candidate_pipeline_version", "0.1.0"))

    @property
    def schema_version(self) -> str:
        return str(self.get("versions.candidate_schema_version", "1.0"))

    @property
    def canonical_election_id(self) -> str:
        return str(self.get("election.canonical_election_id", "tainan_mayoral_2026"))

    @property
    def election_id_aliases(self) -> dict[str, str]:
        aliases = self.get("election.election_id_aliases", {}) or {}
        return {str(k): str(v) for k, v in aliases.items()}

    def resolve_election_id(self, election_id: str | None) -> str:
        requested = election_id or self.canonical_election_id
        return self.election_id_aliases.get(requested, requested)

    @property
    def candidate_id_prefix(self) -> str:
        return str(self.get("election.candidate_id_prefix", "cand_tnn"))

    @property
    def candidate_id_hash_length(self) -> int:
        return int(self.get("election.candidate_id_hash_length", 10))

    def path(self, key: str) -> Path:
        value = self.get(f"paths.{key}")
        if value is None:
            raise KeyError(f"missing path config: paths.{key}")
        return Path(value) if isinstance(value, str) else value

    @property
    def test_mode(self) -> bool:
        if os.getenv("CANDIDATE_PIPELINE_TEST_MODE", "").strip().lower() in ("1", "true", "yes"):
            return True
        return bool(self.get("test_mode", False))


def load_config(path: str | Path = DEFAULT_CONFIG) -> CandidatePipelineConfig:
    return CandidatePipelineConfig.from_yaml(path)
