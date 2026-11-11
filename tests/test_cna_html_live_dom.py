import pytest
from pathlib import Path
from app.collectors.cna_html import CNAHtmlCollector, CATEGORY_SECTIONS

FIXTURES = Path(__file__).resolve().parent / "fixtures"

class TestCNAHtmlLiveDOM:
    def test_section_mapping_politics(self):
        assert "aipl" in CATEGORY_SECTIONS["politics"]
    def test_section_mapping_economy(self):
        assert "afe" in CATEGORY_SECTIONS["economy"]
    def test_section_mapping_international(self):
        assert "aopl" in CATEGORY_SECTIONS["international"]
    def test_politics_sections_not_in_economy(self):
        p = CATEGORY_SECTIONS["politics"]
        e = CATEGORY_SECTIONS["economy"]
        assert p.isdisjoint(e)
    def test_unknown_category_allows_all(self):
        src = {"id":"t","name":"t","type":"cna_list_html","category":"unknown","url":"https://x.com"}
        c = CNAHtmlCollector(src)
        assert c._get_allowed_sections() == frozenset()
