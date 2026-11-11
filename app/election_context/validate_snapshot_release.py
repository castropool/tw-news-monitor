import argparse
import json
import sqlite3
import sys
import re
from collections import defaultdict
from pathlib import Path

def collect_ids(snap):
    ids = list(snap.get("supporting_event_ids", []))
    for cs in snap.get("candidate_status", {}).values():
        ids.extend(cs.get("supporting_event_ids", []))
    for f in ["dpp_integration","kmt_organization","kmt_tpp_cooperation","structural_lean","competitiveness"]:
        o = snap.get(f, {})
        if isinstance(o, dict):
            ids.extend(o.get("supporting_event_ids", []))
    for iss in snap.get("core_issues", []):
        ids.extend(iss.get("supporting_event_ids", []))
    for r in snap.get("key_risks", []):
        ids.extend(r.get("supporting_event_ids", []))
    ids.extend(snap.get("milestone_events", []))
    return ids

def validate(candidate_path, db_path, history_path=None, rules_path=None):
    with open(candidate_path, encoding="utf-8") as f:
        snap = json.load(f)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    errors, warnings = [], []
    checks = {}
    eid = snap.get("election_id", "")

    el = db.execute("SELECT 1 FROM elections WHERE election_id=?", (eid,)).fetchone()
    checks["election_id_exists"] = el is not None
    if not el: errors.append("election_id not found")

    ac = db.execute("SELECT COUNT(*) FROM election_state_snapshots WHERE election_id=? AND snapshot_status='active'", (eid,)).fetchone()[0]
    checks["one_active_snapshot_per_election"] = ac <= 1
    if ac > 1: errors.append(str(ac) + " active snapshots")
    elif ac == 0: warnings.append("no active snapshot")

    latest = db.execute("SELECT snapshot_id FROM election_state_snapshots WHERE election_id=? AND snapshot_status='active' LIMIT 1", (eid,)).fetchone()
    checks["latest_snapshot_is_active"] = latest is not None
    if not latest: errors.append("no active snapshot found")

    if history_path and Path(history_path).exists():
        hids = set()
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    hids.add(json.loads(line).get("snapshot_id", ""))
        overlap = hids & {snap.get("snapshot_id", "")}
        checks["no_snapshot_id_overlap"] = len(overlap) == 0
        if overlap: errors.append("snapshot_id overlap: " + str(overlap))
    else:
        checks["no_snapshot_id_overlap"] = True

    all_ids = collect_ids(snap)
    in_field_dups = set()
    for field_name in ["supporting_event_ids"]:
        seen = set()
        for eid_item in snap.get(field_name, []):
            if eid_item in seen: in_field_dups.add(eid_item)
            seen.add(eid_item)
    for cname, cdata in snap.get("candidate_status", {}).items():
        seen = set()
        for eid_item in cdata.get("supporting_event_ids", []):
            if eid_item in seen: in_field_dups.add(eid_item)
            seen.add(eid_item)
    for field_name in ["dpp_integration","kmt_organization","kmt_tpp_cooperation","structural_lean","competitiveness"]:
        o = snap.get(field_name, {})
        if isinstance(o, dict):
            seen = set()
            for eid_item in o.get("supporting_event_ids", []):
                if eid_item in seen: in_field_dups.add(eid_item)
                seen.add(eid_item)

    missing_ids, cross = [], defaultdict(int)
    for eid_item in all_ids:
        if not eid_item: continue
        cross[eid_item] += 1
        if not db.execute("SELECT 1 FROM election_events WHERE event_id=?", (eid_item,)).fetchone():
            missing_ids.append(eid_item)
    checks["all_supporting_ids_exist"] = len(missing_ids) == 0
    if missing_ids: errors.append("missing ids: " + str(missing_ids))
    sre = {"within_field_duplicates": sorted(in_field_dups),
           "cross_field_reuse_count": len([k for k,v in cross.items() if v > 1])}
    if in_field_dups: warnings.append("within-field dupes: " + str(sorted(in_field_dups)))
    checks["no_within_field_duplicate_supporting_ids"] = len(in_field_dups) == 0

    cov = snap.get("coverage", {})
    checks["coverage_status_partial"] = cov.get("coverage_status") == "partial"
    if not checks["coverage_status_partial"]: errors.append("coverage must be partial")
    checks["known_gaps_not_empty"] = len(cov.get("known_gaps", [])) > 0
    if not checks["known_gaps_not_empty"]: errors.append("known_gaps empty")

    td = json.dumps(snap, ensure_ascii=False)
    pm = re.findall(r"\d+\.?\d*%\u4e00-\u9fff|\d{2}%", td)
    checks["no_poll_numbers"] = len(pm) == 0
    if pm: errors.append("poll numbers: " + str(pm))

    dpp = snap.get("dpp_integration", {})
    checks["dpp_not_fully_integrated"] = dpp.get("formal_status") != "fully_integrated"
    if not checks["dpp_not_fully_integrated"]: errors.append("dpp cannot be fully_integrated")

    ktpp = snap.get("kmt_tpp_cooperation", {})
    checks["kmt_tpp_no_formal_agreement"] = ktpp.get("formal_agreement") is False
    if not checks["kmt_tpp_no_formal_agreement"]: errors.append("kmt_tpp must not be formal_agreement")

    registered = any(c.get("status") == "registered_candidate" for c in snap.get("candidate_status", {}).values())
    checks["no_registered_candidate"] = not registered
    if registered: errors.append("registered_candidate found")

    for fn in ["structural_lean", "competitiveness"]:
        o = snap.get(fn, {})
        fs = o.get("fact_status", "") if isinstance(o, dict) else ""
        checks[fn + "_is_analytical"] = fs == "analytical_inference"
        if fs != "analytical_inference": errors.append(fn + " must be analytical_inference")

    result = {"release_ready": len(errors) == 0 and len(warnings) == 0,
              "errors": errors, "warnings": warnings, "checks": checks,
              "supporting_event_reuse": sre}
    db.close()
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--candidate", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--history")
    p.add_argument("--rules")
    args = p.parse_args()
    r = validate(args.candidate, args.db, args.history, args.rules)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if not r["release_ready"]: sys.exit(1)

if __name__ == "__main__": main()
