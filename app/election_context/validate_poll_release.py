import argparse, json, yaml, sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.election_context.poll_validator import validate_poll_record

def _load_items(path):
    """Load all items regardless of structure. Golden mode needs all items."""
    with open(path, encoding='utf-8') as f:
        raw = f.read().strip()
        if raw.startswith('['):
            return json.loads(raw)
        items = []
        for line in raw.split('\n'):
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
        return items

def _run_golden_mode(items, rules):
    """Golden mode: compare against expected_valid/expected_comparable in each case."""
    from app.election_context.poll_validator import check_poll_comparability
    case_results = []
    unexecuted = []
    for item in items:
        case_id = item.get('case_id', 'unknown')
        case_type = item.get('case_type', 'record_validation')
        expected_valid = item.get('expected_valid', True)

        if case_type == 'record_validation' and 'record' in item:
            rec = item['record']
            vr = validate_poll_record(rec, rules)
            actual_valid = vr['valid']
            passed = (actual_valid == expected_valid)
            case_results.append({
                'case_id': case_id, 'case_type': case_type,
                'expected_valid': expected_valid, 'actual_valid': actual_valid,
                'passed': passed, 'errors': vr['errors'][:3], 'warnings': vr['warnings'][:3],
            })
        elif case_type == 'comparability_validation' and 'comparability_test' in item:
            ct = item['comparability_test']
            poll_a = {'questions':[{'question_type':'head_to_head','candidate_set':ct.get('a_candidates',[])}],'population':{'eligible_population':ct.get('a_population','registered_voters')},'poll_type':'general_election_poll'}
            poll_b = {'questions':[{'question_type':'head_to_head','candidate_set':ct.get('b_candidates',[])}],'population':{'eligible_population':ct.get('b_population','likely_voters')},'poll_type':'general_election_poll'}
            cr = check_poll_comparability(poll_a, poll_b)
            expected_comp = item.get('expected_comparable', ct.get('comparable', False))
            actual_comp = cr['comparable']
            passed = (actual_comp == expected_comp)
            case_results.append({
                'case_id': case_id, 'case_type': case_type,
                'expected_comparable': expected_comp, 'actual_comparable': actual_comp,
                'passed': passed, 'errors': cr['errors'][:3],
            })
        else:
            unexecuted.append(case_id)

    passed_count = sum(1 for c in case_results if c['passed'])
    failed = [c for c in case_results if not c['passed']]

    # Trend eligible count from records
    trend_eligible = sum(1 for item in items if 'record' in item and item['record'].get('usable_for_poll_trend', False))

    output = {
        'mode': 'golden',
        'golden_case_count': len(items),
        'executed_case_count': len(case_results),
        'unexecuted_cases': unexecuted,
        'passed_as_expected_count': passed_count,
        'failed_cases': failed,
        'trend_eligible_count': trend_eligible,
        'release_ready': len(failed) == 0,
    }
    return output

def _run_release_mode(items, rules):
    """Release mode: all records must have valid=true."""
    record_results = []
    trend_eligible = 0
    pt_counts = Counter()
    fs_counts = Counter()
    mc_counts = Counter()

    for item in items:
        if 'record' in item:
            rec = item['record']
        elif 'poll_id' in item:
            rec = item
        else:
            continue

        vr = validate_poll_record(rec, rules)
        pt_counts[rec.get('poll_type','unknown')] += 1
        fs_counts[rec.get('fact_status','unknown')] += 1
        mc_counts[str(rec.get('methodology_complete',False))] += 1
        if rec.get('usable_for_poll_trend', False):
            trend_eligible += 1
        record_results.append({
            'poll_id': rec.get('poll_id',''),
            'valid': vr['valid'],
            'errors': vr['errors'][:5],
            'warnings': vr['warnings'][:3],
        })

    all_valid = all(r['valid'] for r in record_results)
    output = {
        'mode': 'release',
        'release_ready': all_valid,
        'record_count': len(record_results),
        'valid_count': sum(1 for r in record_results if r['valid']),
        'invalid_count': sum(1 for r in record_results if not r['valid']),
        'trend_eligible_count': trend_eligible,
        'poll_type_counts': dict(pt_counts),
        'fact_status_counts': dict(fs_counts),
        'methodology_complete_counts': dict(mc_counts),
        'records': record_results,
    }
    return output

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--schema')
    parser.add_argument('--rules')
    parser.add_argument('--mode', choices=['golden','release'], default='release')
    args = parser.parse_args()

    rules = {}
    if args.rules and Path(args.rules).exists():
        with open(args.rules, encoding='utf-8') as f:
            rules = yaml.safe_load(f) or {}

    items = _load_items(args.input)

    if args.mode == 'golden':
        output = _run_golden_mode(items, rules)
    else:
        output = _run_release_mode(items, rules)

    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not output['release_ready']:
        sys.exit(1)

if __name__ == '__main__':
    main()
