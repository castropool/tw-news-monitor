"""Poll / underground-betting topics: analyzers, gate, rendering, collector."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.collectors.google_news import GoogleNewsRSSCollector
from app.models import Article
from app.special_topics import is_dedicated_topic_article
from app.special_topics.betting import analyze_betting, format_betting_line
from app.special_topics.config import load_special_topics_config, normalize_config
from app.special_topics.gate import FILTER_REASON, apply_topic_gate, topic_rows
from app.special_topics.polls import (
    analyze_poll,
    extract_fieldwork,
    extract_margin_of_error,
    extract_sample_size,
    format_poll_line,
)
from app.special_topics.render import special_topic_line


@pytest.fixture(scope="module")
def cfg():
    return load_special_topics_config()


def _article(title, category, url, summary=None, source_name="自由時報"):
    return Article(
        source_id=f"{category}_src", source_name=source_name, category=category,
        title=title, url=url, published_at=datetime(2026, 11, 13, 9, 0),
        fetched_at=datetime(2026, 11, 13, 9, 5), position=1, summary=summary,
    )


# ---------------------------------------------------------------- polls ----

def test_poll_full_extraction(cfg):
    result = analyze_poll(
        "台灣民意基金會最新民調：賴清德聲望42.3%、不滿意46.1%",
        "本次調查於11月10日至12日進行，成功訪問1,078人，抽樣誤差約正負2.98個百分點。",
        cfg["poll"],
    )
    assert result["relevant"] is True
    assert result["pollster"] == "台灣民意基金會"
    assert result["figures"] == [
        {"label": "賴清德聲望", "value": 42.3},
        {"label": "不滿意", "value": 46.1},
    ]
    assert result["sample_size"] == 1078
    assert result["margin_of_error"] == 2.98
    assert result["fieldwork"] == "11月10日至11月12日"
    line = format_poll_line(result)
    assert line.startswith("民调机构：台灣民意基金會｜关键数据：賴清德聲望42.3%、不滿意46.1%")
    assert "样本：1078" in line and "误差：±2.98个百分点" in line


def test_poll_candidate_names_kept_as_figure_labels(cfg):
    result = analyze_poll("美麗島電子報國政民調 侯友宜支持度51.2% 蘇巧慧38.7%", "", cfg["poll"])
    assert result["pollster"] == "美麗島電子報"
    assert [f["label"] for f in result["figures"]] == ["侯友宜支持度", "蘇巧慧"]


def test_poll_running_prose_does_not_invent_subject(cfg):
    result = analyze_poll("國政民調侯友宜支持度51.2%", "", cfg["poll"])
    assert result["figures"] == [{"label": "支持度", "value": 51.2}]


def test_poll_irrelevant_and_excluded(cfg):
    assert analyze_poll("百貨週年慶業績亮眼", "", cfg["poll"])["relevant"] is False
    excluded = analyze_poll("消費者信心指數調查顯示回升", "", cfg["poll"])
    assert excluded["relevant"] is False
    assert excluded["reason"].startswith("excluded:")


def test_poll_value_guards():
    assert extract_sample_size("有效樣本12人") is None
    assert extract_sample_size("有效樣本1,070份") == 1070
    assert extract_margin_of_error("誤差±3.1%") == 3.1
    assert extract_margin_of_error("誤差45個百分點") is None
    assert extract_fieldwork("2026年11月3日至5日") == "2026年11月3日至11月5日"
    assert extract_fieldwork("沒有日期") is None


def test_poll_figures_capped(cfg):
    text = "民調 " + " ".join(f"項目{c}支持{v}%" for c, v in zip("甲乙丙丁戊己庚辛", range(10, 90, 10)))
    assert len(analyze_poll(text, "", cfg["poll"])["figures"]) == cfg["poll"]["max_figures"]


# -------------------------------------------------------------- betting ----

def test_betting_election_related(cfg):
    result = analyze_betting("九合一選舉地下賭盤開盤 警方查獲組頭", "賠率曝光", cfg["betting"])
    assert result["relevant"] is True
    assert result["election_related"] is True
    assert result["subtypes"][0] == "election_betting"
    assert "enforcement" in result["subtypes"]
    assert format_betting_line(result) == "类型：选举赌盘、查缉执法｜涉及选举：是"


def test_betting_sports_not_election(cfg):
    result = analyze_betting("職棒簽賭案起訴 檢方追查金流", "", cfg["betting"])
    assert result["relevant"] is True
    assert result["election_related"] is False
    assert {"sports_betting", "enforcement", "money_laundering"} <= set(result["subtypes"])


def test_betting_lottery_excluded(cfg):
    assert analyze_betting("威力彩頭獎槓龜 下注金額創高", "", cfg["betting"])["relevant"] is False
    assert analyze_betting("颱風動態", "", cfg["betting"])["relevant"] is False


# ---------------------------------------------------------------- config ---

def test_config_missing_file_uses_builtin_defaults(tmp_path):
    loaded = load_special_topics_config(tmp_path / "missing.yaml")
    assert loaded["poll"]["enabled"] and loaded["betting"]["enabled"]
    assert "民調" in loaded["poll"]["required_keywords"]


def test_config_normalizes_bad_values():
    loaded = normalize_config({"poll": {"pollsters": [{"name": ""}, "x", {"name": "甲民調"}], "max_figures": "bad"}})
    assert loaded["poll"]["pollsters"] == [{"name": "甲民調", "aliases": ["甲民調"]}]
    assert loaded["poll"]["max_figures"] == 6


# ------------------------------------------------------------------ gate ---

def test_gate_splits_only_dedicated_categories(cfg):
    poll_ok = _article("TVBS民調 施政滿意度40%", "poll", "https://n.test/p1")
    poll_off = _article("年終獎金調查出爐", "poll", "https://n.test/p2")
    bet_ok = _article("選舉賭盤 警方查獲", "betting", "https://n.test/b1")
    politics = _article("立法院三讀", "politics", "https://n.test/x1")
    result = apply_topic_gate([poll_ok, poll_off, bet_ok, politics], cfg)
    assert [a.url for a in result.kept] == ["https://n.test/p1", "https://n.test/b1", "https://n.test/x1"]
    assert [a.url for a in result.blocked] == ["https://n.test/p2"]
    assert set(result.annotations) == {"https://n.test/p1", "https://n.test/p2", "https://n.test/b1"}
    assert FILTER_REASON == "topic_relevance"


def test_gate_disabled_topic_passes_through(cfg):
    disabled = dict(cfg)
    disabled["poll"] = dict(cfg["poll"], enabled=False)
    off = _article("年終獎金調查出爐", "poll", "https://n.test/p2")
    result = apply_topic_gate([off], disabled)
    assert result.kept == [off] and not result.blocked


def test_topic_rows_shape(cfg):
    poll = _article("台灣指標民調 賴清德信任度50%", "poll", "https://n.test/p1")
    bet = _article("地下賭盤押注市長選舉", "betting", "https://n.test/b1")
    gate = apply_topic_gate([poll, bet], cfg)
    rows = {r["topic"]: r for r in topic_rows([poll, bet], gate.annotations, cfg)}
    assert rows["poll"]["source_type"] == "台灣指標民調"
    assert rows["poll"]["event_type"] == "poll_release"
    assert rows["poll"]["metadata"]["figures"][0]["value"] == 50.0
    assert rows["betting"]["scope"] == "election"
    assert rows["betting"]["event_type"] == "election_betting"
    assert rows["betting"]["metadata"]["filter_version"] == cfg["filter_version"]


def test_render_line_and_dedicated_flag(cfg):
    poll = _article("台灣民意基金會 民調 賴清德聲望45%", "poll", "https://n.test/p1")
    assert is_dedicated_topic_article(poll)
    assert special_topic_line(poll).startswith("民调机构：台灣民意基金會")
    politics = _article("立法院三讀", "politics", "https://n.test/x")
    assert not is_dedicated_topic_article(politics)
    assert special_topic_line(politics) == ""


# ------------------------------------------------------------- collector ---

GOOGLE_NEWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"民調" - Google News</title>
<item>
  <title>台灣民意基金會：賴清德聲望42.3% - 自由時報</title>
  <link>https://news.google.com/rss/articles/CBMiAAA?oc=5</link>
  <pubDate>Thu, 12 Nov 2026 23:30:00 GMT</pubDate>
  <description>&lt;a href="https://x"&gt;台灣民意基金會：賴清德聲望42.3%&lt;/a&gt;</description>
  <source url="https://news.ltn.com.tw">自由時報</source>
</item>
<item>
  <title> - 中央社</title>
  <link>https://news.google.com/rss/articles/EMPTY</link>
  <source url="https://www.cna.com.tw">中央社</source>
</item>
<item>
  <title>美麗島電子報國政民調出爐</title>
  <link>https://news.google.com/rss/articles/CBMiBBB</link>
  <pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate>
</item>
</channel></rss>"""


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status
        self.headers = {}


class _Client:
    def __init__(self, text, status=200):
        self.response = _Resp(text, status)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.response

    def close(self):
        pass


def _collector(text, status=200):
    collector = GoogleNewsRSSCollector({
        "id": "poll_test", "name": "民调追踪", "category": "poll",
        "type": "google_news_rss", "url": "https://news.google.com/rss/search?q=x",
    })
    collector._client = _Client(text, status)
    return collector


def test_google_news_collector_parses_publisher_and_title():
    articles = _collector(GOOGLE_NEWS_XML).collect()
    assert [a.title for a in articles] == ["台灣民意基金會：賴清德聲望42.3%", "美麗島電子報國政民調出爐"]
    first, second = articles
    assert first.source_name == "自由時報"
    assert second.source_name == "民调追踪"  # no <source>: falls back to config name
    assert first.category == "poll" and first.source_id == "poll_test"
    assert first.summary is None
    assert first.published_at.utcoffset().total_seconds() == 8 * 3600
    assert [a.position for a in articles] == [1, 2]


def test_google_news_collector_http_error_marks_outcome():
    collector = _collector("oops", status=503)
    collector.MAX_RETRIES = 0
    with pytest.raises(ValueError):
        collector.collect()
    assert collector.last_outcome.schema_valid is False


def test_google_news_title_cleanup_variants():
    from app.collectors.google_news import _clean_title

    assert _clean_title("標題 - 中央社", "中央社") == "標題"
    assert _clean_title("- 中央社", "中央社") == ""
    assert _clean_title("中央社", "中央社") == ""
    assert _clean_title("訪問中央社", "中央社") == "訪問中央社"
    assert _clean_title("標題", "") == "標題"


def test_google_news_collector_caps_items():
    item = (
        "<item><title>民調{n} - 中央社</title><link>https://news.google.com/rss/articles/{n}</link>"
        "<source url='https://www.cna.com.tw'>中央社</source></item>"
    )
    xml = "<rss version='2.0'><channel>" + "".join(item.format(n=n) for n in range(30)) + "</channel></rss>"
    assert len(_collector(xml).collect()) == 20
