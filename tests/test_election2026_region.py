"""地区别名归一专项测试（需求34 + 繁简/合并/多县市展示）。"""

import pytest

from app.election2026.config import load_election_config
from app.election2026.region_resolver import (
    normalize_region,
    distinct_regions_in,
    is_multi_city_national,
    resolve_regions,
)
from app.election2026.hanzi_utils import to_traditional

CONFIG = load_election_config()
REGIONS = CONFIG["regions"]


@pytest.mark.parametrize(
    "text,expected",
    [
        # 需求34：台北/北市 → 台北市
        ("台北市长选举", "台北市"),
        ("北市议员", "台北市"),
        ("臺北市長", "台北市"),
        # 新北 → 新北市
        ("新北市长", "新北市"),
        # 桃园/桃市 → 桃园市
        ("桃园市长", "桃園市"),
        ("桃市府", "桃園市"),
        # 台中/中市 → 台中市
        ("台中市长", "台中市"),
        ("中市府", "台中市"),
        # 台南/南市 → 台南市
        ("台南市长", "台南市"),
        ("南市议会", "台南市"),
        # 高雄/高市 → 高雄市
        ("高雄市长", "高雄市"),
        ("高市府", "高雄市"),
        # 竹市 → 新竹市 / 竹县 → 新竹县 / 竹北 → 新竹县
        ("竹市议长", "新竹市"),
        ("竹县议长", "新竹縣"),
        ("竹北市长", "新竹縣"),
        # 繁体标准名直接命中
        ("新北市長選舉", "新北市"),
        ("基隆市长", "基隆市"),
        # 简中标题（经繁简归一）
        ("嘉义市长选举", "嘉義市"),
        ("嘉义县长选举", "嘉義縣"),
        ("云林县长", "雲林縣"),
        ("苗栗县长", "苗栗縣"),
        ("彰化县长", "彰化縣"),
        ("屏东县长", "屏東縣"),
        ("宜兰县长", "宜蘭縣"),
        ("花莲县长", "花蓮縣"),
        ("台东县长", "台東縣"),
        ("澎湖县长", "澎湖縣"),
        ("金门县长", "金門縣"),
        ("连江县长", "連江縣"),
        # 别名不误吞：竹北市 → 新竹县（不是台北市"北市"）
        ("竹北市长协调", "新竹縣"),
        # 无地区
        ("立法院审查法案", None),
    ],
)
def test_normalize_region(text, expected):
    assert normalize_region(text, REGIONS) == expected


class TestMultiCityDetection:
    def test_three_cities_alias_national(self):
        assert is_multi_city_national("台北新北桃园市长选情", REGIONS) is True

    def test_three_cities_standard_national(self):
        assert (
            is_multi_city_national("台北市新北市桃園市長選舉比較", REGIONS) is True
        )

    def test_two_cities_not_national(self):
        assert is_multi_city_national("新竹县新竹市协调", REGIONS) is False

    def test_single_city_not_national(self):
        assert is_multi_city_national("台南市长选举", REGIONS) is False

    def test_distinct_regions_hsinchu(self):
        regions = distinct_regions_in("新竹县新竹市协调人选", REGIONS)
        assert "新竹縣" in regions and "新竹市" in regions


class TestResolveRegions:
    def test_local_single_standard(self):
        scope, region, regions = resolve_regions("苏巧慧公布新北市长政见", "", REGIONS)
        assert scope == "local" and region == "新北市"

    def test_local_alias_anchor(self):
        # 标题无标准名直现但有别名（竹北→新竹县），经 region_anchor 锚定
        scope, region, regions = resolve_regions(
            "蓝白协调竹北市长人选", "", REGIONS, region_anchor="新竹縣"
        )
        assert scope == "local" and region == "新竹縣"

    def test_multi_region_two_standard(self):
        scope, region, regions = resolve_regions("新竹县新竹市协调人选", "", REGIONS)
        assert scope == "multi_region"
        assert regions == ["新竹縣", "新竹市"]

    def test_national_three_standard(self):
        scope, region, regions = resolve_regions(
            "台北市新北市桃園市長選舉比較", "", REGIONS
        )
        assert scope == "national"

    def test_entity_region_fallback(self):
        scope, region, regions = resolve_regions(
            "陈亭妃成立竞选团队", "", REGIONS, entity_regions=["台南市"]
        )
        assert scope == "local" and region == "台南市"

    def test_empty(self):
        scope, region, regions = resolve_regions("无地区新闻", "", REGIONS)
        assert scope == "" and region is None and regions == []


class TestHanziNormalization:
    def test_to_traditional_key_chars(self):
        assert to_traditional("陈亭妃宣布投入台南市长选举") == "陳亭妃宣布投入台南市長選舉"
        assert to_traditional("绿营整合嘉义县长人选") == "綠營整合嘉義縣長人選"
        assert to_traditional("国民党中执会通过2026提名特别办法") == "國民黨中執會通過2026提名特別辦法"

    def test_taiwanese_convention_kept(self):
        # 台式"台"不转"臺"
        assert "台北市" in to_traditional("台北市长")
        assert "台" in to_traditional("台东县长")
