"""2026 九合一选举分类器专项测试。

样本集：正样本 >= 30 / 负样本 >= 25 / 边界样本 >= 15 / 全局多县市 >= 10。
"""

import pytest

from app.election2026.config import (
    load_election_config,
    load_entities,
    DISABLED_CONFIG,
)
from app.election2026.classifier import classify_article, classify_articles
from app.election2026.entity_loader import build_entity_index

CONFIG = load_election_config()
ENTITIES = load_entities()


@pytest.fixture(scope="module")
def cfg():
    return CONFIG


@pytest.fixture(scope="module")
def ent():
    return ENTITIES


def classify(title, summary="", cfg=CONFIG, ent=ENTITIES):
    return classify_article(title, summary, config=cfg, entities=ent)


# ============================================================
# 正样本（>=30）：须全部判为九合一
# ============================================================
POSITIVE = [
    # 需求31 明确列出的 7 条
    ("陈亭妃宣布投入台南市长选举", "local", "台南市"),
    ("谢龙介成立台南竞选团队", "local", "台南市"),
    ("苏巧慧公布新北市长选举政见", "local", "新北市"),
    ("蓝白协调竹北市长人选", "local", "新竹縣"),
    ("民进党启动2026县市长提名机制", "national", None),
    ("国民党评估六都市长布局", "national", None),
    ("2026九合一最新政党支持度民调", "national", None),
    # 繁中实际新闻风格变体
    ("陳亭妃宣布投入台南市長選舉", "local", "台南市"),
    ("謝龍介成立台南競選團隊", "local", "台南市"),
    ("蘇巧慧公布新北市長選舉政見", "local", "新北市"),
    ("藍白協調竹北市長人選", "local", "新竹縣"),
    ("民進黨啟動2026縣市長提名機制", "national", None),
    ("國民黨評估六都市長佈局", "national", None),
    ("2026九合一最新政黨支持度民調", "national", None),
    # 全局动向六类
    ("民进党公布2026县市长提名办法", "national", None),
    ("国民党启动县市长征召", "national", None),
    ("蓝白启动九合一整体协调机制", "national", None),
    ("最新民调观察六都蓝绿白选情", "national", None),
    ("赖清德要求全党备战九合一", "national", None),
    ("国民党评估六都候选人布局", "national", None),
    # 地方选举事件
    ("侯友宜表态争取连任新北市长", "local", "新北市"),
    ("蒋万安宣布争取台北市长连任", "local", "台北市"),
    ("陈其迈登记参选高雄市长", "local", "高雄市"),
    ("卢秀燕证实将投入台中市长选举", "local", "台中市"),
    ("谢国梁宣布竞选连任基隆市长", "local", "基隆市"),
    ("柯志恩表态参选高雄市长", "local", "高雄市"),
    ("蓝营协调新竹县长人选", "local", "新竹縣"),
    ("绿营整合嘉义县长人选", "local", "嘉義縣"),
    ("2026县市长选举各党初选开跑", "national", None),
    ("地方选举县市长参选爆炸", "national", None),
]

# ============================================================
# 负样本（>=25）：须全部判为非九合一
# ============================================================
NEGATIVE = [
    # 需求32 明确列出的 7 条
    "国民党主席选举进入倒计时",
    "美国州长选举结果出炉",
    "赖清德谈2028总统选举",
    "回顾2024台湾总统大选",
    "某协会举行理事长选举",
    "学校学生会选举",
    "陈亭妃质询行政院政策",
    # 外国/其他选举
    "日本参议院选举自民党获胜",
    "韩国国会选举结果揭晓",
    "美国期中选举共和党大胜",
    "法国总统选举第二轮投票",
    "德国联邦议院选举社民党领先",
    # 非选举政治
    "谢龙介参加地方公益活动",
    "陈亭妃出席台南庙宇开幕",
    "立法院召开临时会审查总预算",
    "行政院通过家电补助政策",
    "立委选举国民党提名立委候选人",
    "党主席选举朱立伦寻求连任",
    "2028总统大选赖清德民调领先",
    "回顾2024大选检讨报告",
    # 经济/社会/国际
    "台积电股价创历史新高",
    "中央银行宣布升息半码",
    "劳动部公布基本工资审议",
    "颱风来袭全台停班停课",
    "某企业举行股东会改选董事",
    "校务会议选举教师代表",
]


# ============================================================
# 边界样本（>=15）
# ============================================================
BOUNDARY = [
    # (title, summary, expect_is_election, expect_region_or_scope)
    # 无选举语境的施政批评 → 不判
    ("谢龙介批台南治水失灵", "", False, None),
    # 明确以参选攻防为议题 → 判正 台南 attack_defense/policy
    ("谢龙介批台南治水失灵", "以此作为参选市长主要攻防议题", True, "台南市"),
    # 施政满意度民调无选举语境 → 保守判负（例行施政）
    ("台南市长黄伟哲施政满意度民调", "", False, None),
    ("台南市长选举最新民调出炉", "", True, "台南市"),
    ("新北市长侯友宜视察台风应变", "", False, None),
    ("侯友宜出席市政活动不谈选举", "", False, None),
    ("柯文哲谈蓝白合作可能性", "", False, None),  # 无九合一语境
    ("柯文哲：蓝白2026县市长可协调合作", "", True, "national"),
    ("民进党中执会通过2026提名特别办法", "", True, "national"),
    ("国民党中常会讨论2026布局", "", True, "national"),
    # 疑似区样本：候选人+地区+辅助词但缺语境（保守判负）
    ("陈亭妃在台南获得支持", "", False, None),
    ("苏巧慧在新北基层受欢迎", "", False, None),
    # 语境在摘要才判正
    ("苏巧慧扩大新北地方组织", "布局2026市长选举争取提名", True, "新北市"),
    ("林俊宪台南行程满档", "", False, None),
    ("林俊宪台南行程满档", "被看好参选下届台南市长", True, "台南市"),
    # 挑战/接班类
    ("党内点名挑战侯友宜", "新北市长接班话题延烧", True, "新北市"),
]


class TestPositiveSamples:
    @pytest.mark.parametrize("title,scope,region", POSITIVE)
    def test_positive_is_election(self, title, scope, region):
        ann = classify(title)
        assert ann.is_election, f"正样本被漏判: {title} -> {ann}"
        if scope == "national":
            assert ann.scope == "national", f"应归全局动向: {title} -> {ann}"
        else:
            assert ann.region == region, (
                f"县市识别错误: {title} -> region={ann.region} (期望 {region})"
            )


class TestNegativeSamples:
    @pytest.mark.parametrize("title", NEGATIVE)
    def test_negative_not_election(self, title):
        ann = classify(title)
        assert not ann.is_election, f"负样本被误判: {title} -> {ann}"


class TestBoundarySamples:
    @pytest.mark.parametrize(
        "title,summary,expect,region",
        [b for b in BOUNDARY],
        ids=[f"{b[0][:12]}|{b[1][:10]}" for b in BOUNDARY],
    )
    def test_boundary(self, title, summary, expect, region):
        ann = classify(title, summary)
        assert ann.is_election is expect, (
            f"边界样本判定错误: {title} / {summary} -> {ann}"
        )
        if expect and region == "national":
            assert ann.scope == "national", f"应归全局动向: {title} -> {ann}"
        elif expect and region:
            assert ann.region == region, (
                f"边界样本地区错误: {title} -> {ann.region} (期望 {region})"
            )


class TestMultiRegion:
    """多县市新闻：明确两县市 → multi_region；跨>=3 且整体比较 → national。"""

    def test_hsinchu_county_city_coordination(self):
        ann = classify("蓝白新竹县市协调人选", "蓝白新竹縣市協調縣市長人選")
        assert ann.is_election
        assert ann.scope in ("multi_region", "local")
        # 至少覆盖新竹
        assert any("新竹" in r for r in ann.regions)

    def test_six_cities_comparison_is_national(self):
        ann = classify("最新民调：六都市长选情总观察")
        assert ann.is_election
        assert ann.scope == "national"

    def test_multiple_region_poll_is_national(self):
        ann = classify("台北新北桃园市长选情民调比较")
        assert ann.is_election
        assert ann.scope == "national"

    def test_hsinchu_multi_region_keeps_both(self):
        ann = classify("民进党评估新竹县新竹市市长人选")
        assert ann.is_election
        assert ann.scope == "multi_region", f"应 multi_region: {ann}"
        assert "新竹縣" in ann.regions and "新竹市" in ann.regions


class TestAntiOverreach:
    """防误判专项：不能只靠人物/关键词。"""

    def test_person_alone_not_election(self):
        assert not classify("陈亭妃质询行政院长").is_election

    def test_region_person_activity_not_election(self):
        assert not classify("谢龙介参加台南关庙活动").is_election

    def test_word_candidate_alone_negative(self):
        assert not classify("某工会理事长候选人名单公布").is_election

    def test_word_poll_alone_negative(self):
        assert not classify("市调公司公布手机民调方法").is_election

    def test_traditional_and_simplified_consistent(self):
        a1 = classify("陳亭妃宣布投入台南市長選舉")
        a2 = classify("陈亭妃宣布投入台南市长选举")
        assert a1.is_election and a2.is_election
        assert a1.region == a2.region == "台南市"


class TestDisabledConfig:
    def test_disabled_returns_false(self):
        ann = classify_article(
            "陈亭妃宣布投入台南市长选举",
            config=DISABLED_CONFIG,
            entities=ENTITIES,
        )
        assert not ann.is_election

    def test_none_config_returns_false(self):
        ann = classify_article("陈亭妃宣布投入台南市长选举", config=None)
        assert not ann.is_election

    def test_empty_title(self):
        ann = classify("")
        assert not ann.is_election


class TestBatchClassify:
    def test_classify_articles_returns_url_map(self):
        from app.models import Article
        from datetime import datetime

        arts = [
            Article(
                source_id="s", source_name="媒体", category="politics",
                title="陈亭妃宣布投入台南市长选举", url="https://e.com/1",
                published_at=datetime(2026, 9, 4), fetched_at=datetime(2026, 9, 4),
                position=1,
            ),
            Article(
                source_id="s", source_name="媒体", category="politics",
                title="立法院审查法案", url="https://e.com/2",
                published_at=datetime(2026, 9, 4), fetched_at=datetime(2026, 9, 4),
                position=2,
            ),
        ]
        result = classify_articles(arts, CONFIG, ENTITIES)
        assert "https://e.com/1" in result
        assert "https://e.com/2" not in result

    def test_batch_empty_articles(self):
        assert classify_articles([], CONFIG, ENTITIES) == {}

    def test_batch_disabled(self):
        assert classify_articles([], DISABLED_CONFIG, ENTITIES) == {}
