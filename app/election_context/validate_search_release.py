import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.election_context.repository import ElectionContextRepository

def validate_golden_case(repo, case):
    query = case["query"]
    must_include = case.get("must_include", [])
    must_exclude = case.get("must_exclude", [])
    must_rank_first = case.get("must_rank_first", "")
    must_rank_before = case.get("must_rank_before", [])
    max_results = case.get("max_results", 20)
    res = repo.search_events(keyword=query, election_id="TW-2026-TNN-MAYOR", limit=max_results)
    eids = [e["event_id"] for e in res]
    result = {"query": query, "returned_event_ids": eids, "result_count": len(eids), "errors": []}
    inc_pass = all(e in eids for e in must_include)
    exc_pass = all(e not in eids for e in must_exclude)
    r1_pass = not must_rank_first or (eids and eids[0] == must_rank_first)
    rb_pass = True
    for a, b in must_rank_before:
        if a in eids and b in eids and eids.index(a) > eids.index(b):
            rb_pass = False
    max_pass = len(eids) <= max_results
    result["must_include_passed"] = inc_pass
    result["must_exclude_passed"] = exc_pass
    result["must_rank_first_passed"] = r1_pass
    result["must_rank_before_passed"] = rb_pass
    result["max_results_passed"] = max_pass
    if not inc_pass: result["errors"].append("Missing: " + str([e for e in must_include if e not in eids]))
    if not exc_pass: result["errors"].append("Excluded present: " + str([e for e in must_exclude if e in eids]))
    if not r1_pass: result["errors"].append("Expected first " + must_rank_first + " got " + (eids[0] if eids else "none"))
    if not rb_pass:
        for a, b in must_rank_before:
            if a in eids and b in eids and eids.index(a) > eids.index(b):
                result["errors"].append("Rank: " + a + " after " + b)
    if not max_pass: result["errors"].append("Exceeded max " + str(max_results) + ": " + str(len(eids)))
    result["passed"] = all([inc_pass, exc_pass, r1_pass, rb_pass, max_pass])
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-cases", required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    with open(args.golden_cases, encoding="utf-8") as f:
        golden = json.load(f)
    repo = ElectionContextRepository(args.db)
    repo.connect()
    results = []
    all_pass = True
    for case in golden:
        cr = validate_golden_case(repo, case)
        results.append(cr)
        if not cr["passed"]:
            all_pass = False
    output = {"release_ready": all_pass, "errors": [], "cases": results}
    if not all_pass:
        for cr in results:
            if not cr["passed"]:
                output["errors"].append("FAIL: " + cr["query"] + " - " + "; ".join(cr["errors"]))
    repo.close()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not all_pass:
        sys.exit(1)

if __name__ == "__main__":
    main()
