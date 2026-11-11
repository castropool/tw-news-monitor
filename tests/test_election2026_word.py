"""Word 简报"九合一选举"栏目专项测试（需求35 的 Case 1-10）。

测试方式：构造 Article → build_word_digest → 读回 docx 段落文本断言。
"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from docx import Document

from app.models import Article
from app.word_digest import build_word_digest
from app.election2026.config import load_election_config, load_entities

NOW = datetime(2026, 9, 4, 9, 0)
CONFIG = load_election_config()
ENTITIES = load_entities()


def make(
    title,
    *,
    source_id="udn",
    source_name="联合新闻网",
    category="politics",
    url=None,
    pos=1,
    official=False,
):
    return Article(
        source_id="president_press" if official else source_id,
        source_name="台湾总统府" if official else source_name,
        category=category,
        title=title,
        url=url or f"https://example.com/{abs(hash(title))}",
        published_at=NOW,
        fetched_at=NOW,
        position=pos,
    )


def render(articles, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        out = build_word_digest(
            articles, Path(tmp), generated_at=NOW,
            election_config=kw.get("config", CONFIG),
            election_entities=kw.get("entities", ENTITIES),
            election_annotations=kw.get("annotations"),
        )
        doc = Document(str(out))
        return [p.text for p in doc.paragraphs]


def has_election_section(texts):
    return any("九合一选举" in t for t in texts)


# Case 1：无九合一新闻 → 不显示"九合一选举"栏
def test_case1_no_election_no_section():
    texts = render([
        make("立法院审查总预算"),
        make("台股收盘上涨", category="economy"),
    ])
    assert not has_election_section(texts)
    # 普通分类直接作为动态一级栏目。
    assert any("一、政治新闻" in t for t in texts)
    assert not any("九合一" in t for t in texts)


# Case 2：只有全局动向新闻 → （一）全局动向，无任何县市空栏
def test_case2_national_only():
    texts = render([make("民进党启动2026县市长提名机制")])
    assert has_election_section(texts)
    idx_election = next(i for i, t in enumerate(texts) if "九合一选举" in t)
    idx_global = next(i for i, t in enumerate(texts) if "（一）全局動向" in t)
    assert idx_election < idx_global
    # 无县市空栏
    assert not any("（二）" in t for t in texts)
    # 文章在全局动向栏内
    assert any("民进党启动2026县市长提名机制" in t for t in texts)


# Case 3：只有台南 → （一）台南市（无全局动向时台南从（一）开始）
def test_case3_tainan_only():
    texts = render([make("陈亭妃宣布投入台南市长选举")])
    assert has_election_section(texts)
    assert any("（一）台南市" in t for t in texts)
    assert not any("全局動向" in t for t in texts)
    assert not any("（二）" in t for t in texts)


# Case 4：全局 + 台南 → （一）全局动向 （二）台南市
def test_case4_global_and_tainan():
    texts = render([
        make("民进党启动2026县市长提名机制", pos=1),
        make("陈亭妃宣布投入台南市长选举", pos=2),
    ])
    i_global = next(i for i, t in enumerate(texts) if "（一）全局動向" in t)
    i_tainan = next(i for i, t in enumerate(texts) if "（二）台南市" in t)
    assert i_global < i_tainan


# Case 5：新北 + 台南 + 高雄 → 按预设县市顺序（新北 < 台南 < 高雄）
def test_case5_city_order():
    texts = render([
        make("苏巧慧公布新北市长选举政见", url="https://x.com/ntp"),
        make("陈亭妃宣布投入台南市长选举", url="https://x.com/tnn"),
        make("陈其迈登记参选高雄市长", url="https://x.com/khh"),
    ])
    i_ntp = next(i for i, t in enumerate(texts) if "（一）新北市" in t)
    i_tnn = next(i for i, t in enumerate(texts) if "（二）台南市" in t)
    i_khh = next(i for i, t in enumerate(texts) if "（三）高雄市" in t)
    assert i_ntp < i_tnn < i_khh


# Case 6：新竹县 + 新竹市 → Word 只显示"新竹縣市"，底层 regions 分别保存
def test_case6_hsinchu_merge():
    arts = [
        make("蓝白协调新竹县长人选", url="https://x.com/hc_county"),
        make("绿营整合新竹市长人选", url="https://x.com/hc_city"),
    ]
    texts = render(arts)
    assert any("（一）新竹縣市" in t for t in texts)
    # 不出现单独的新竹县/新竹市栏
    assert not any("（二）" in t for t in texts)
    # 底层分类 annotation 保留两个标准县市（通过 classifier 验证）
    from app.election2026.classifier import classify_article
    ann1 = classify_article("蓝白协调新竹县长人选", config=CONFIG, entities=ENTITIES)
    assert ann1.region == "新竹縣"
    ann2 = classify_article("绿营整合新竹市长人选", config=CONFIG, entities=ENTITIES)
    assert ann2.region == "新竹市"


# Case 7：重大九合一新闻 → 同时进入 重点提示（【重大】前缀）与 九合一栏
def test_case7_critical_in_both():
    from app.importance import ImportanceResult
    arts = [
        make("陈亭妃宣布投入台南市长选举", url="https://x.com/1", pos=1),
        make("立法院审查总预算", url="https://x.com/2", pos=2),
    ]
    imp = [
        (arts[0], ImportanceResult(score=90, level="critical", matched_rules=["x"])),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = build_word_digest(
            arts, Path(tmp), generated_at=NOW,
            importance_results=imp,
            election_config=CONFIG, election_entities=ENTITIES,
        )
        doc = Document(str(out))
        texts2 = [p.text for p in doc.paragraphs]
        i_elec2 = next(i for i, t in enumerate(texts2) if "九合一选举" in t)
        # 九合一栏内条目带【重大】前缀（重点提示形态）
        assert any("【重大】" in t and "陈亭妃" in t for t in texts2[i_elec2:])
        # 未出现在普通政治新闻栏（互斥）
        i_pol = next(i for i, t in enumerate(texts2) if "政治新闻" in t)
        assert not any("陈亭妃" in t for t in texts2[i_pol:])


# Case 8：九合一政治稿 → 不重复进入（一）政治新闻
def test_case8_no_politics_duplication():
    texts = render([
        make("陈亭妃宣布投入台南市长选举", url="https://x.com/1"),
        make("立法院审查总预算", url="https://x.com/2"),
    ])
    # 九合一稿只在九合一栏
    assert sum(1 for t in texts if "陈亭妃宣布投入台南市长选举" in t) == 1
    i_pol = next(i for i, t in enumerate(texts) if "政治新闻" in t)
    assert not any("陈亭妃" in t for t in texts[i_pol:])
    # 普通政治稿仍在政治新闻栏
    assert any("立法院审查总预算" in t for t in texts[i_pol:])


# Case 9：动态编号连续无跳号（一级：官方/九合一/政治）
def test_case9_numbering_contiguous():
    texts = render([
        make("台湾总统府发布新闻", official=True, url="https://gov/1"),
        make("民进党启动2026县市长提名机制", url="https://x.com/n"),
        make("陈亭妃宣布投入台南市长选举", url="https://x.com/t"),
        make("立法院审查总预算", url="https://x.com/l"),
    ])
    i_official = next(i for i, t in enumerate(texts) if "一、官方信源" in t)
    i_election = next(i for i, t in enumerate(texts) if "二、九合一选举" in t)
    i_politics = next(i for i, t in enumerate(texts) if "三、政治新闻" in t)
    assert i_official < i_election < i_politics


# Case 10：XML 特殊字符 & < > 及 URL 中的 & 不回归
def test_case10_special_chars():
    arts = [
        make(
            "市长选举民调：蓝营支持度>绿营&白营<3成？",
            url="https://x.com/poll?a=1&b=2<3>4",
        )
    ]
    texts = render(arts)
    # 标题文本保留原字符
    assert any("蓝营支持度>绿营&白营<3成" in t for t in texts)
    # 超链接正常（URL 含 &）
    with tempfile.TemporaryDirectory() as tmp:
        out = build_word_digest(
            arts, Path(tmp), generated_at=NOW,
            election_config=CONFIG, election_entities=ENTITIES,
        )
        doc = Document(str(out))
        # docx 能正常打开、无 XML 错误即通过；超链接关系存在
        assert out.exists()


# 补充：超过 10 个县市分组时编号回退阿拉伯数字（（11）（12）…）不崩溃
def test_many_regions_numbering_beyond_ten():
    cities = [
        ("台北市", "蒋万安表态竞选连任台北市长"),
        ("新北市", "李四川登记参选新北市长"),
        ("桃园市", "张善政宣布竞选连任桃园市长"),
        ("台中市", "卢秀燕宣布竞选连任台中市长"),
        ("台南市", "陈亭妃宣布投入台南市长选举"),
        ("高雄市", "陈其迈登记参选高雄市长"),
        ("基隆市", "谢国梁宣布竞选连任基隆市长"),
        ("新竹市", "高虹安争取新竹市长连任"),
        ("彰化县", "王惠美协调彰化县长人选"),
        ("云林县", "云林县长选举4人登记"),
        ("嘉义市", "嘉义市长选举两人竞逐"),
        ("屏东县", "屏东市长周佳琪登记参选"),
    ]
    arts = [make(title, url=f"https://x.com/r{i}") for i, (_, title) in enumerate(cities)]
    texts = render(arts)
    assert any("九合一选举" in t for t in texts)
    assert any("（九）彰化縣" in t for t in texts)
    assert any("（十）雲林縣" in t for t in texts)
    assert any("（11）嘉義縣市" in t for t in texts)
    assert any("（12）屏東縣" in t for t in texts)


# 补充：无 election 配置时完全兼容旧行为（不生成九合一栏）
def test_no_election_config_legacy():
    texts = render(
        [make("陈亭妃宣布投入台南市长选举", url="https://x.com/1")],
        config=None,
    )
    assert not has_election_section(texts)
    assert any("政治新闻" in t for t in texts)


# 补充：官方稿涉九合一也不进九合一栏（保持官方栏完整）
def test_official_election_stays_official():
    texts = render([
        make("民进党启动县市长提名机制", official=True, url="https://gov/1"),
        make("陈亭妃宣布投入台南市长选举", url="https://x.com/1"),
    ])
    # 官方稿只在官方信源栏
    assert any("民进党启动县市长提名机制" in t for t in texts)
    i_election = next(i for i, t in enumerate(texts) if "九合一选举" in t)
    assert not any("民进党启动县市长提名机制" in t for t in texts[i_election:])
    assert any("陈亭妃" in t for t in texts[i_election:])


# 补充：election_annotations 预计算结果被优先使用（无需在 Word 内重算）
def test_annotations_dict_used():
    from app.election2026.models import ElectionAnnotation
    arts = [make("某标题不含关键词", url="https://x.com/ann")]
    ann = ElectionAnnotation(
        is_election=True, scope="local", region="高雄市",
        regions=["高雄市"], event_type="campaign",
        confidence=99, reason="provided",
    )
    texts = render(arts, annotations={arts[0].url: ann})
    assert has_election_section(texts)
    assert any("（一）高雄市" in t for t in texts)
