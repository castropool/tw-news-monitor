"""轻量响应数据模型（不保存密钥、推理过程或敏感元数据）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReportStatistics:
    claim_count: int = 0
    section_count: int = 0
    event_reference_count: int = 0
    poll_reference_count: int = 0
    source_reference_count: int = 0
    gap_reference_count: int = 0
    chinese_char_count: int = 0
    length_below_target: bool = False

    def to_dict(self) -> dict:
        return {
            "claim_count": self.claim_count,
            "section_count": self.section_count,
            "event_reference_count": self.event_reference_count,
            "poll_reference_count": self.poll_reference_count,
            "source_reference_count": self.source_reference_count,
            "gap_reference_count": self.gap_reference_count,
            "chinese_char_count": self.chinese_char_count,
            "length_below_target": self.length_below_target,
        }

