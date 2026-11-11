import json
import re
from typing import Any
from pathlib import Path

class ElectionQualityCheck:
    def __init__(self, style_config: dict):
        self.style = style_config

    def check_report(self, report: dict, fact_count: int) -> list[dict]:
        errors = []
        tainan_text = report.get('tainan', {}).get('situation', '')
        new_taipei_text = report.get('new_taipei', {}).get('situation', '')
        overall = report.get('overall_judgment', '')
        comparison = report.get('comparison', '')

        china_len = len(tainan_text)
        nt_len = len(new_taipei_text)

        if china_len < 1000:
            errors.append({'check': '台南字数', 'status': 'fail', 'detail': f'{china_len}字 < 1000'})
        elif china_len > 3000:
            errors.append({'check': '台南字数', 'status': 'warn', 'detail': f'{china_len}字 > 3000'})
        else:
            errors.append({'check': '台南字数', 'status': 'pass', 'detail': f'{china_len}字'})

        if nt_len < 1000:
            errors.append({'check': '新北字数', 'status': 'fail', 'detail': f'{nt_len}字 < 1000'})
        elif nt_len > 3000:
            errors.append({'check': '新北字数', 'status': 'warn', 'detail': f'{nt_len}字 > 3000'})
        else:
            errors.append({'check': '新北字数', 'status': 'pass', 'detail': f'{nt_len}字'})

        if overall:
            errors.append({'check': '总体格局判断', 'status': 'pass'})
        else:
            errors.append({'check': '总体格局判断', 'status': 'fail', 'detail': '缺失'})

        if comparison:
            errors.append({'check': '综合判断', 'status': 'pass'})
        else:
            errors.append({'check': '综合判断', 'status': 'fail', 'detail': '缺失'})

        tainan_outlook = report.get('tainan', {}).get('outlook', '')
        nt_outlook = report.get('new_taipei', {}).get('outlook', '')
        if tainan_outlook:
            errors.append({'check': '台南走势研判', 'status': 'pass'})
        else:
            errors.append({'check': '台南走势研判', 'status': 'warn', 'detail': '缺失'})
        if nt_outlook:
            errors.append({'check': '新北走势研判', 'status': 'pass'})
        else:
            errors.append({'check': '新北走势研判', 'status': 'warn', 'detail': '缺失'})

        full_text = tainan_text + new_taipei_text + overall + comparison
        if '根據你提供的信息' in full_text or '根据您提供的' in full_text:
            errors.append({'check': '模型自述', 'status': 'fail', 'detail': '存在对话式表达'})
        else:
            errors.append({'check': '模型自述', 'status': 'pass'})

        if re.search(r'\{[\w\s]*\}', full_text):
            errors.append({'check': 'Markdown残留', 'status': 'fail'})
        else:
            errors.append({'check': 'Markdown残留', 'status': 'pass'})

        if fact_count < 5:
            errors.append({'check': '事实数量', 'status': 'warn', 'detail': f'仅{fact_count}条事实'})
        else:
            errors.append({'check': '事实数量', 'status': 'pass', 'detail': f'{fact_count}条'})

        avoided = ['可能', '或将', '值得关注', '不排除']
        overuse = [w for w in avoided if full_text.count(w) > 10]
        if overuse:
            errors.append({'check': '空泛套话', 'status': 'warn', 'detail': f'过度使用: {overuse}'})
        else:
            errors.append({'check': '空泛套话', 'status': 'pass'})

        return errors

    def all_pass(self, errors: list[dict]) -> bool:
        return all(e['status'] != 'fail' for e in errors)

    def summary(self, errors: list[dict]) -> str:
        passed = sum(1 for e in errors if e['status'] == 'pass')
        failed = sum(1 for e in errors if e['status'] == 'fail')
        warned = sum(1 for e in errors if e['status'] == 'warn')
        return f'质检: {passed}通过, {failed}失败, {warned}警告'
