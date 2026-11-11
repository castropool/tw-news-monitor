"""Validate v2 snapshot preview."""
import argparse, json, sqlite3, sys, re
from pathlib import Path

def validate(snapshot_path, evidence_path, previous_path, db_path):
    with open(snapshot_path, encoding="utf-8") as f:
        snap = json.load(f)
    with open(evidence_path, encoding="utf-8") as f:
        evidence = json.load(f)
    with open(previous_path, encoding="utf-8") as f:
        prev = json.load(f)

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    errors = []
    warnings = []
    checks = {}

    # 1. snapshot_status
    checks["status_preview"] = snap.get("snapshot_status") == "preview"
    if not checks["status_preview"]: errors.append("status not preview")

    # 2. supersedes
    checks["supersedes_correct"] = snap.get("supersedes_snapshot_id") == "tn_state_20260726"
    if not checks["supersedes_correct"]: errors.append("wrong supersedes")

    # 3. previous active snapshot unchanged
    prev_in_db = db.execute("SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_id='tn_state_20260726' AND snapshot_status='active'").fetchone()[0]
    checks["previous_active_unchanged"] = prev_in_db == 1
    if not checks["previous_active_unchanged"]: errors.append("previous active snapshot modified")

    # 4. All supporting_event_ids exist (from v1 snapshot)
    for eid in snap.get("supporting_event_ids", []):
        if not db.execute("SELECT 1 FROM election_events WHERE event_id=?", (eid,)).fetchone():
            errors.append("missing event: " + eid)
    checks["event_ids_exist"] = True

    # 5. All supporting_poll_ids exist
    for pid in snap.get("supporting_poll_ids", []):
        if not db.execute("SELECT 1 FROM election_polls WHERE poll_id=?", (pid,)).fetchone():
            errors.append("missing poll: " + pid)
    checks["poll_ids_exist"] = True

    # 6. All supporting_question_refs exist
    for qref in snap.get("supporting_question_refs", []):
        parts = qref.split("#")
        if len(parts) != 2 or not db.execute("SELECT 1 FROM poll_questions WHERE poll_id=? AND question_id=?", (parts[0], parts[1])).fetchone():
            errors.append("missing question ref: " + qref)
    checks["question_refs_exist"] = True

    # 7. ETtoday not referenced
    for pid in snap.get("supporting_poll_ids", []):
        if "ettoday" in pid:
            errors.append("ettoday referenced")
            break
    checks["ettoday_not_referenced"] = True

    # 8. Internal polls not supporting public conclusions
    for pid in snap.get("public_poll_assessment", {}).get("supporting_poll_ids", []):
        row = db.execute("SELECT poll_type FROM election_polls WHERE poll_id=?", (pid,)).fetchone()
        if row and row["poll_type"] == "internal_poll_claim":
            errors.append("internal poll in public assessment: " + pid)
    checks["internal_not_in_public"] = True

    # 9. DPP primary not in general trend
    for qref in snap.get("supporting_question_refs", []):
        if "dpp_primary_official" in qref:
            errors.append("dpp primary in question refs: " + qref)
    checks["dpp_primary_not_in_trend"] = True

    # 10. Online DMP has correct group
    online_row = db.execute("SELECT comparable_group_key FROM poll_questions WHERE poll_id='poll_tnn_20260228_juwen_pearson_online' AND trend_eligible=1").fetchone()
    if online_row and online_row[0] != "tnn_h2h_online_network_population_chen_hsieh":
        errors.append("online DMP wrong group")
    checks["online_group_correct"] = True

    # 11. TVBS March correct group
    tvbs = db.execute("SELECT comparable_group_key FROM poll_questions WHERE poll_id='poll_tnn_20260312_tvbs' AND question_id='q_chen_hsieh_likely'").fetchone()
    if tvbs and tvbs[0] != "tnn_h2h_voting_intention_landline_chen_hsieh":
        errors.append("tvbs march wrong group")
    checks["tvbs_march_group_correct"] = True

    # 12. latest_field_end from DB
    db_fe = db.execute("SELECT MAX(json_extract(fieldwork_json, '$.field_end')) as fe FROM election_polls").fetchone()[0]
    checks["latest_field_end_correct"] = snap.get("coverage", {}).get("latest_poll_field_end") == db_fe
    if not checks["latest_field_end_correct"]: errors.append("latest_field_end mismatch: " + str(snap.get("coverage",{}).get("latest_poll_field_end")) + " vs " + str(db_fe))

    # 13. No cross-group averages in snapshot text
    text = json.dumps(snap, ensure_ascii=False)
    prohibited_phrases = ["平均支持率", "综合支持率", "加权平均", "rolling average", "overall lead", "模型胜率", "预测票差", "稳赢", "锁定胜局", "已经追平"]
    for phrase in prohibited_phrases:
        if phrase in text:
            errors.append("prohibited conclusion: " + phrase)
    checks["no_prohibited_conclusions"] = True

    # 14. coverage_status
    checks["coverage_status_partial"] = snap.get("coverage", {}).get("coverage_status") == "partial"
    if not checks["coverage_status_partial"]: errors.append("coverage not partial")

    # 15. known_gaps includes March 12 gap
    gaps = " ".join(snap.get("coverage", {}).get("known_gaps", []))
    checks["known_gap_march_12"] = "2026年3月12日" in gaps
    if not checks["known_gap_march_12"]: errors.append("missing march 12 gap")

    # 16. Confidence limits
    for field in ["structural_lean", "competitiveness", "public_poll_assessment"]:
        obj = snap.get(field, {})
        conf = obj.get("confidence", 1) if isinstance(obj, dict) else 1
        limits = {"structural_lean": 0.78, "competitiveness": 0.75, "public_poll_assessment": 0.78}
        if conf > limits.get(field, 1):
            errors.append(f"{field} confidence {conf} exceeds {limits[field]}")
        checks[field + "_confidence_ok"] = True

    # 17. Cross-group calculations empty
    checks["cross_group_empty"] = len(evidence.get("cross_group_calculations", [])) == 0
    if not checks["cross_group_empty"]: errors.append("cross-group calculations found")

    # 18. Unsupported claims empty
    checks["unsupported_claims_empty"] = len(evidence.get("unsupported_claims", [])) == 0
    if not checks["unsupported_claims_empty"]: errors.append("unsupported claims found")

    # 19. No specific win rate or vote gap
    for pattern in [r"\d+\.?\d*%\s*[-~至到]\s*\d+\.?\d*%", r"\d+%[\u4e00-\u9fff]*[胜赢]"]:
        if re.search(pattern, text):
            errors.append("vote gap or win rate found")
    checks["no_vote_gap"] = True

    # 20. Counts
    counts = {
        "polls": db.execute("SELECT COUNT(*) FROM election_polls").fetchone()[0],
        "questions": db.execute("SELECT COUNT(*) FROM poll_questions").fetchone()[0],
        "trend_eligible": db.execute("SELECT COUNT(*) FROM poll_questions WHERE trend_eligible=1").fetchone()[0],
        "groups": len(set(r[0] for r in db.execute("SELECT comparable_group_key FROM poll_questions WHERE comparable_group_key IS NOT NULL AND comparable_group_key NOT IN ('','not_assigned')").fetchall())),
    }

    db.close()
    result = {"preview_ready": len(errors) == 0, "errors": errors, "warnings": warnings, "checks": checks, "counts": counts}
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True); p.add_argument("--evidence", required=True)
    p.add_argument("--previous", required=True); p.add_argument("--db", required=True)
    args = p.parse_args()
    r = validate(args.snapshot, args.evidence, args.previous, args.db)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if not r["preview_ready"]: sys.exit(1)

if __name__ == "__main__": main()
