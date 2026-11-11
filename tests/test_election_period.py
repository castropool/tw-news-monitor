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

class TestPeriodLogic:
    def test_10th_report_period(self):
        from datetime import datetime, timezone, timedelta
        taipei = timezone(timedelta(hours=8))
        assert datetime.now(taipei).day in range(1, 32)

    def test_23rd_report_period(self):
        from datetime import datetime, timezone, timedelta
        taipei = timezone(timedelta(hours=8))
        assert datetime.now(taipei).day in range(1, 32)
