"""Fix cand_tnn_86acc80dcf review template with correct UTF-8 Chinese content.

The previous template was written through a console pipe that mangled Chinese
characters into '?'. This script runs from a UTF-8 source file to avoid that.
"""

from __future__ import annotations

import json
from pathlib import Path


TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "election_candidates"
    / "tainan_2026"
    / "review_templates"
    / "cand_tnn_86acc80dcf.json"
)


def main() -> None:
    t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    t["decision"] = "approve_as_subevent"
    t["reviewer"] = "guanliyuan"
    t["review_reason"] = "人工审核批准为子事件：黄国昌与谢龙介台南合体助选并称蓝白合不用怀疑"
    t["target_formal_event_id"] = "evt_tnn_20260128_hsieh_kmt_tpp_proposal"
    t["event"]["event_date"] = "2026-08-02T00:00:00+08:00"
    t["event"]["event_date_precision"] = "day"
    t["event"]["event_type"] = "joint_campaign"
    t["event"]["title"] = "黃國昌合體謝龍介台南助選 喊藍白合「不用懷疑」"
    t["event"]["summary"] = "黃國昌與謝龍介在台南合體助選，黃國昌稱藍白合「不用懷疑」"
    t["event"]["actors"] = ["謝龍介", "黃國昌"]
    t["event"]["themes"] = ["藍白合", "助選"]
    t["event"]["locations"] = ["台南"]
    t["event"]["observed_facts"] = ["黃國昌與謝龍介在台南合體助選"]
    t["event"]["attributed_statements"] = ["黃國昌稱藍白合「不用懷疑」"]
    t["event"]["allegations"] = []
    t["event"]["limitations"] = []
    TEMPLATE.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    print("title repr:", repr(t["event"]["title"]))
    print("actors repr:", repr(t["event"]["actors"]))
    print("summary repr:", repr(t["event"]["summary"]))


if __name__ == "__main__":
    main()
