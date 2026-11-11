import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.election_quality_check import ElectionQualityCheck

STYLE_CONFIG = {
    'title_structure': [],
    'chapter_structure': {
        'tainan': {'length_words': '2500—3000'},
        'new_taipei': {'length_words': '2500—3000'},
    },
    'sentence_style': {'avoid_overuse': ['可能', '或将', '值得关注', '不排除']},
}

class TestWordCount:
    def setup_method(self):
        self.qc = ElectionQualityCheck(STYLE_CONFIG)

    def test_tainan_within_range(self):
        report = {
            'overall_judgment': '格局判斷',
            'tainan': {'situation': '是' * 2750, 'outlook': '走势研判'},
            'new_taipei': {'situation': '是' * 2750, 'outlook': '走势研判'},
            'comparison': '综合判断',
        }
        errors = self.qc.check_report(report, 50)
        tainan_result = [e for e in errors if e['check'] == '台南字数'][0]
        assert tainan_result['status'] == 'pass'

    def test_tainan_too_short(self):
        report = {
            'overall_judgment': '格局判斷',
            'tainan': {'situation': '短', 'outlook': '走势'},
            'new_taipei': {'situation': '是' * 1300, 'outlook': '走势'},
            'comparison': '综合判断',
        }
        errors = self.qc.check_report(report, 50)
        tainan_result = [e for e in errors if e['check'] == '台南字数'][0]
        assert tainan_result['status'] == 'fail'


class TestQualityChecks:
    def setup_method(self):
        self.qc = ElectionQualityCheck(STYLE_CONFIG)

    def test_missing_overall_judgment(self):
        report = {
            'overall_judgment': '',
            'tainan': {'situation': '是' * 1300, 'outlook': '走势'},
            'new_taipei': {'situation': '是' * 1300, 'outlook': '走势'},
            'comparison': '综合判断',
        }
        errors = self.qc.check_report(report, 50)
        overall = [e for e in errors if e['check'] == '总体格局判断'][0]
        assert overall['status'] == 'fail'

    def test_model_self_talk(self):
        report = {
            'overall_judgment': '根據你提供的信息，分析如下',
            'tainan': {'situation': '是' * 1300, 'outlook': '走势'},
            'new_taipei': {'situation': '是' * 1300, 'outlook': '走势'},
            'comparison': '综合判断',
        }
        errors = self.qc.check_report(report, 50)
        self_talk = [e for e in errors if e['check'] == '模型自述'][0]
        assert self_talk['status'] == 'fail'

    def test_sufficient_facts(self):
        report = {
            'overall_judgment': '格局',
            'tainan': {'situation': '是' * 1300, 'outlook': '走势'},
            'new_taipei': {'situation': '是' * 1300, 'outlook': '走势'},
            'comparison': '综合判断',
        }
        errors = self.qc.check_report(report, 10)
        fact_check = [e for e in errors if e['check'] == '事实数量'][0]
        assert fact_check['status'] == 'pass'

    def test_all_pass(self):
        report = {
            'overall_judgment': '格局',
            'tainan': {'situation': '是' * 2750, 'outlook': '走势'},
            'new_taipei': {'situation': '是' * 2750, 'outlook': '走势'},
            'comparison': '综合判断',
        }
        errors = self.qc.check_report(report, 10)
        assert self.qc.all_pass(errors)
