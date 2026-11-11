import json, yaml, copy
from datetime import datetime
from pathlib import Path
from typing import Any

ACCEPTABLE_POLL_TYPES = frozenset(['general_election_poll','primary_poll','internal_poll_claim','favorability_poll','party_support_poll','issue_poll','online_vote','unclassified_poll'])
ACCEPTABLE_FACT_STATUSES = frozenset(['poll_result','candidate_claim','party_claim','media_interpretation','pending_verification'])
ACCEPTABLE_QUESTION_TYPES = frozenset(['head_to_head','multi_candidate','primary_comparison','primary_mutual','favorability','party_support','issue_position','other'])
ACCEPTABLE_OPTION_TYPES = frozenset(['candidate','party','undecided','refused','other'])

def validate_poll_record(record: dict, rules: dict | None = None) -> dict:
    errors = []
    warnings = []
    checks = {}
    r = record

    pid = r.get('poll_id','')
    eid = r.get('election_id','')
    pt = r.get('poll_type','')
    fs = r.get('fact_status','')
    mc = r.get('methodology_complete', False)

    checks['poll_id_present'] = bool(pid)
    if not pid: errors.append('poll_id为空')

    checks['election_id_valid'] = eid == 'TW-2026-TNN-MAYOR'
    if eid != 'TW-2026-TNN-MAYOR': errors.append('election_id无效')

    checks['poll_type_valid'] = pt in ACCEPTABLE_POLL_TYPES
    if pt not in ACCEPTABLE_POLL_TYPES: errors.append(f'poll_type无效: {pt}')

    checks['fact_status_valid'] = fs in ACCEPTABLE_FACT_STATUSES
    if fs not in ACCEPTABLE_FACT_STATUSES: errors.append(f'fact_status无效: {fs}')

    fw = r.get('fieldwork', {})
    fs_date = fw.get('field_start','')
    fe_date = fw.get('field_end','')
    checks['field_dates_valid'] = True
    if fs_date and fe_date and fs_date > fe_date:
        checks['field_dates_valid'] = False
        errors.append(f'field_start({fs_date})晚于field_end({fe_date})')

    meth = r.get('methodology', {})
    ss = meth.get('sample_size')
    checks['sample_size_valid'] = ss is None or (isinstance(ss, int) and ss > 0)
    if ss is not None and not (isinstance(ss, int) and ss > 0):
        errors.append(f'sample_size无效: {ss}')

    questions = r.get('questions', [])
    checks['questions_not_empty'] = len(questions) > 0
    if not questions: errors.append('questions为空')

    results = r.get('results', [])
    checks['results_not_empty'] = len(results) > 0
    if not results: errors.append('results为空')

    qids = {q.get('question_id','') for q in questions}
    for res in results:
        rqid = res.get('question_id','')
        if rqid and rqid not in qids:
            errors.append(f'结果引用了不存在的问题ID: {rqid}')
    checks['result_question_ids_valid'] = not any(e.startswith('结果引用了不存在') for e in errors[-len(results):])

    sources = r.get('source_ids', [])
    checks['source_ids_not_empty'] = len(sources) > 0
    if not sources: errors.append('source_ids为空')

    t = r.get('option_type', '')
    if t and t not in ACCEPTABLE_OPTION_TYPES:
        warnings.append(f'option_type无效: {t}')

    if mc:
        mc_errors = []
        required_for_complete = ['publication.pollster', 'fieldwork.field_start', 'fieldwork.field_end', 'methodology.sample_size', 'methodology.sampling_method', 'methodology.interview_method', 'population.eligible_population']
        for field_path in required_for_complete:
            parts = field_path.split('.')
            val = r
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p, None)
                else:
                    val = None
                    break
            if val is None or val == '' or val == 0:
                mc_errors.append(field_path)
        if mc_errors:
            checks['methodology_complete_consistent'] = False
            for me in mc_errors:
                errors.append(f'methodology_complete=true但{me}缺失')
    else:
        checks['methodology_complete_consistent'] = True

    if pt == 'internal_poll_claim':
        if r.get('usable_for_poll_trend', True) or r.get('usable_as_scientific_poll', True):
            warnings.append('内参民调不得标记为科学趋势')
        checks['internal_poll_not_scientific'] = True
    else:
        checks['internal_poll_not_scientific'] = True

    if pt == 'online_vote':
        if r.get('usable_as_scientific_poll', True):
            warnings.append('网络投票不得标记为科学民调')
        checks['online_vote_not_scientific'] = True
    else:
        checks['online_vote_not_scientific'] = True

    if r.get('publication',{}).get('is_syndicated', False):
        errors.append('转载稿件不得创建独立poll_id')
        checks['no_duplicate_syndicated_poll'] = False
    else:
        checks['no_duplicate_syndicated_poll'] = True

    # --- No unjustified normalization ---
    checks['no_unjustified_normalization'] = True
    for res in results:
        rv = res.get('reported_value', '')
        nv = res.get('normalized_value')
        if nv is not None and rv:
            try:
                parsed_rv = float(rv.replace('%','').strip())
            except (ValueError, TypeError):
                continue
            if abs(parsed_rv - nv) > 0.5:
                has_limitation = any('归一' in lim or 'normalize' in lim.lower() for lim in r.get('limitations', []))
                if not has_limitation:
                    errors.append(f'normalized_value与reported_value不一致且无合理解释: {rv}->{nv}')
                    checks['no_unjustified_normalization'] = False

    # --- Sum check for normalized values ---
    numeric_nv = [res.get('normalized_value') for res in results if res.get('normalized_value') is not None]
    if numeric_nv and abs(sum(numeric_nv) - 100) > 1:
        has_limitation = any('非100' in lim or '不' in lim for lim in r.get('limitations', []))
        if not has_limitation:
            warnings.append('总和非100%')

    # --- Fieldwork precision ---
    fwp = r.get('fieldwork_precision', 'exact')
    checks['fieldwork_precision_valid'] = fwp in ('exact', 'month_only', 'unknown')
    if fwp == 'exact':
        if not fs_date or not fe_date:
            errors.append('fieldwork_precision=exact但field_start或field_end缺失')
    if fwp in ('month_only', 'unknown'):
        if mc:
            errors.append('fieldwork_precision非exact时methodology_complete必须为false')
        if r.get('usable_for_poll_trend', False):
            errors.append('fieldwork_precision非exact时usable_for_poll_trend必须为false')

    # --- Internal poll with missing pollster ---
    if pt == 'internal_poll_claim':
        pollster = r.get('publication', {}).get('pollster', '')
        if not pollster:
            if mc:
                errors.append('internal_poll_claim缺pollster时methodology_complete必须为false')
            if r.get('usable_for_poll_trend', False) or r.get('usable_as_scientific_poll', False):
                errors.append('internal_poll_claim缺pollster时不可标记为科学趋势')
            has_lim = any('调查执行机构未公开' in lim for lim in r.get('limitations', []))
            if not has_lim:
                warnings.append('内部民调调查执行机构未公开')
    else:
        # Public poll without pollster
        pollster = r.get('publication', {}).get('pollster', '')
        if not pollster and pt != 'online_vote':
            if mc:
                errors.append('公开民调缺pollster时methodology_complete必须为false')
            if r.get('usable_for_poll_trend', False):
                errors.append('公开民调缺pollster时usable_for_poll_trend必须为false')

    return {'valid': len(errors) == 0, 'errors': errors, 'warnings': warnings, 'checks': checks}

def validate_poll_collection(records: list[dict], rules: dict | None = None) -> dict:
    results = []
    for i, rec in enumerate(records):
        vr = validate_poll_record(rec, rules)
        results.append({'index': i, 'poll_id': rec.get('poll_id',f'line_{i}'), 'valid': vr['valid'], 'errors': vr['errors'], 'warnings': vr['warnings']})
    all_valid = all(r['valid'] for r in results)
    return {'valid': all_valid, 'record_count': len(results), 'valid_count': sum(1 for r in results if r['valid']), 'invalid_count': sum(1 for r in results if not r['valid']), 'records': results}

def check_poll_comparability(poll_a: dict, poll_b: dict) -> dict:
    errors = []
    qa = poll_a.get('questions', [{}])[0] if poll_a.get('questions') else {}
    qb = poll_b.get('questions', [{}])[0] if poll_b.get('questions') else {}
    pa = poll_a.get('population', {})
    pb = poll_b.get('population', {})

    ca = set(qa.get('candidate_set', []))
    cb = set(qb.get('candidate_set', []))
    if ca != cb:
        errors.append(f'候选人组合不同: {ca} vs {cb}')

    eqa = pa.get('eligible_population', '')
    eqb = pb.get('eligible_population', '')
    if eqa != eqb and eqa and eqb:
        errors.append(f'调查母体不同: {eqa} vs {eqb}')

    qta = qa.get('question_type', '')
    qtb = qb.get('question_type', '')
    if qta != qtb:
        errors.append(f'问题类型不同: {qta} vs {qtb}')

    pta = poll_a.get('poll_type', '')
    ptb = poll_b.get('poll_type', '')
    if 'internal_poll_claim' in (pta, ptb) or 'online_vote' in (pta, ptb):
        errors.append('内参民调或网络投票不可比较')

    return {'comparable': len(errors) == 0, 'errors': errors}

def build_comparable_group_key(record: dict) -> str:
    qs = record.get('questions', [{}])[0] if record.get('questions') else {}
    pop = record.get('population', {})
    candidates = '_'.join(sorted(qs.get('candidate_set', [])))
    pop_type = pop.get('eligible_population', 'unknown')
    qtype = qs.get('question_type', 'unknown')
    return f'{pop_type}|{qtype}|{candidates}'
