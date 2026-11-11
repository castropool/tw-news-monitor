"""Synthetic news fixtures: algorithm contracts, not claims about real events."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from app.models import Article
from app.military.config import load_rules
from app.military.classifier import classify, normalize_title
from app.military.events import build_events

NOW = datetime(2026, 9, 5, 9)


def article(title, index=1, hours=0, source="中央社", summary=None):
    return Article("cna" if source == "中央社" else "udn", source, "politics",
                   title, f"https://example.com/{index}", NOW + timedelta(hours=hours),
                   NOW + timedelta(hours=hours), 1, summary=summary)


@pytest.fixture
def rules():
    return load_rules()


@pytest.mark.parametrize("title,category", [
    ("台军汉光演习今日展开", "taiwan_exercise"),
    ("國軍漢光演習模擬機場遭襲", "taiwan_exercise"),
    ("陆军进行实弹射击训练", "taiwan_exercise"),
    ("海军陆战队展开立即备战操演", "taiwan_exercise"),
    ("后备部队展开战备演训", "taiwan_exercise"),
    ("空军测试F16V新型导弹", "taiwan_equipment"),
    ("F-16V进行新型飞弹挂载测试", "taiwan_equipment"),
    ("海鯤號潛艦進行潛航測試", "taiwan_equipment"),
    ("台湾海军沱江舰完成海试", "taiwan_equipment"),
    ("玉山舰正式成军", "taiwan_equipment"),
    ("陆军接收M1A2T战车", "arms_sale"),
    ("美国批准F-16零件对台军售", "arms_sale"),
    ("国防部公布海马斯采购交付进度", "arms_sale"),
    ("台湾军援弹药交付国军", "arms_sale"),
    ("中科院研发军用无人机系统", "defense_technology"),
    ("台湾推动潜舰自造建案", "defense_technology"),
    ("中科院展示AI军事应用研发成果", "defense_technology"),
    ("解放军山东舰通过台湾海峡", "pla_activity"),
    ("共军军机越过海峡中线", "pla_activity"),
    ("解放军展开大规模围台军演", "pla_activity"),
    ("美军与台军举行联合演习", "us_taiwan_military"),
    ("美台军事交流代表团抵台", "us_taiwan_military"),
    ("台湾宪兵查获营区机密外泄", "other_military"),
    ("海鲲号潜舰预算遭立委质疑", "other_military"),
    ("台军电子战系统完成雷达测试", "taiwan_equipment"),
    ("台军防空卫星系统完成试验", "taiwan_equipment"),
])
def test_positive_categories(rules, title, category):
    result = classify(article(title), rules)
    assert result.is_military, result.reason
    assert result.category == category


@pytest.mark.parametrize("title", [
    "战车游戏推出新版本", "农业无人机喷药提升效率", "电影飞弹特效幕后介绍",
    "历史回顾：汉光演习的由来", "国防预算蓝绿互批政治口水",
    "国防部举办摄影比赛", "中科院发布天文研究成果", "爱国者球迷庆祝胜利",
    "以色列IDF部署战车", "美国空军测试F-16V新装备", "山东舰在南海进行舰载机训练",
    "日本农业无人机研发成功", "台北首次举行无人机灯光秀", "台湾民航客机试飞",
    "台股军工概念股大涨", "首次大规模电影实弹特效宣传", "国防预算引发政党口水战",
    "台湾推出M1A2T战车模型玩具", "青年日报举办官兵家庭日",
    "乌克兰军队收到海马斯", "F-16V游戏首度挂载飞弹测试", "台湾商业卫星研发计划",
])
def test_negative_contexts(rules, title):
    assert not classify(article(title), rules).is_military


def test_summary_cannot_turn_unrelated_title_into_military(rules):
    assert not classify(article("台北美食节登场", summary="延伸阅读：台军汉光演习"), rules).is_military
    assert classify(article("国防部说明装备进度", summary="空军F-16V进行飞弹挂载测试"), rules).is_military


def test_normalization():
    assert normalize_title("【快訊】中央社：Ｆ１６Ｖ  完成测试") == "F-16V 完成测试"
    assert normalize_title("台媒：F－16V　测试") == "F-16V 测试"


@pytest.mark.parametrize("titles,expected", [
    (["F-16V进行新型飞弹挂载测试", "空军测试F16V新型导弹"], 1),
    (["F-16V新型飞弹测试", "美国批准F-16零件对台军售"], 2),
    (["国防部公布汉光演习规划", "国防部公布潜舰预算"], 2),
    (["海鲲号潜舰进行潜航测试", "海鲲号潜舰预算遭立委质疑"], 2),
    (["台军F-16V在花莲测试飞弹", "台军F-16V在台东测试飞弹"], 2),
    (["空军F-16V测试飞弹", "空军幻象2000测试飞弹"], 2),
    (["台军汉光演习今日展开", "汉光演习进入第二日", "国军汉光演习模拟机场遭袭"], 1),
    (["解放军山东舰通过台湾海峡", "共军山东舰航经台海"], 1),
    (["台军天弓导弹取消试射", "台军天弓导弹完成试射"], 2),
])
def test_event_boundaries(rules, titles, expected):
    events = build_events([article(t, i) for i, t in enumerate(titles)], rules)
    assert len(events) == expected, [e["title"] for e in events]


def test_cross_day_and_window(rules):
    a = article("台军汉光演习今日展开")
    assert len(build_events([a, article("汉光演习进入第二日", 2, 24)], rules)) == 1
    assert len(build_events([a, article(a.title, 2, 100)], rules)) == 2


def test_no_transitive_time_chain(rules):
    rows = [article("海鲲号潜舰进行潜航测试", i, i * 40) for i in range(3)]
    assert len(build_events(rows, rules)) >= 2


def test_order_invariance_and_url_dedup(rules):
    rows = [article("空军F16V新型导弹测试", 1), article("F-16V进行新型飞弹挂载测试", 2)]
    assert build_events(rows, rules) == build_events(list(reversed(rows)), rules)
    assert build_events(rows, rules) == build_events(rows + [rows[0]], rules)


def test_explanations_and_db_ids(rules):
    rows = [article("空军F16V新型导弹测试", 1), article("F-16V进行新型飞弹挂载测试", 2)]
    event = build_events(rows, rules, {rows[0].url: 5, rows[1].url: 9})[0]
    assert event["article_ids"] == [5, 9]
    reason = event["cluster_explanations"][0]
    assert reason["matched_entities"] and reason["matched_actions"]
    assert 0 <= reason["title_similarity"] <= 1
    assert reason["time_distance"] == 0
    assert reason["cluster_score"] >= rules["clustering"]["threshold"]


def test_representative_prefers_information_over_earliest(rules):
    early = article("F16V测试", 1, source="未知网站")
    detailed = article("空军F-16V进行新型飞弹挂载测试", 2, 2, summary="飞弹挂载测试的报道摘要。" * 10)
    event = build_events([early, detailed], rules)[0]
    assert event["representative"]["url"] == detailed.url


def test_event_importance_and_source_dedup(rules):
    high = article("台军F-16V首次实弹试射新型飞弹", 1)
    low = article("台军后备战备检查", 2)
    events = build_events([low, high], rules)
    assert events[0]["importance"] > events[1]["importance"]
    assert len(events[0]["importance_reasons"]) >= 3
    duplicate_publisher = article(high.title, 3, source="中央通訊社")
    assert build_events([high, duplicate_publisher], rules)[0]["importance"] == events[0]["importance"]
    independent = article(high.title, 4, source="联合新闻网")
    combined = build_events([high, independent], rules)[0]
    assert combined["importance"] > events[0]["importance"]


def test_no_unrelated_or_negated_intensity_bonus(rules):
    base = build_events([article("空军F-16V测试飞弹")], rules)[0]
    unrelated = build_events([article("空军F-16V测试飞弹；台北首次举办实弹电影展")], rules)
    negated = build_events([article("空军F-16V测试飞弹，并非首次且未进行实弹射击")], rules)[0]
    assert not unrelated or unrelated[0]["importance"] == base["importance"]
    assert negated["importance"] == base["importance"]


def test_us_policy_is_factual_unscored(rules):
    e = build_events([article("美国批准F-16零件对台军售")], rules)[0]
    assert e["importance"] is None
    assert e["level"] == "factual"


def test_config_changes_clustering_and_scoring(rules):
    rows = [article("F-16V进行新型飞弹挂载测试", 1), article("空军测试F16V新型导弹", 2, 24)]
    changed = deepcopy(rules)
    changed["clustering"]["window_hours"] = 6
    assert len(build_events(rows, changed)) == 2
    changed = deepcopy(rules)
    changed["importance"]["category_base"]["taiwan_equipment"] = 1
    assert build_events(rows, changed)[0]["importance"] < build_events(rows, rules)[0]["importance"]


@pytest.mark.parametrize("content", ["[]", "enabled: yes", "clustering: {window_hours: -1}"])
def test_invalid_config_rejected(tmp_path, content):
    p = tmp_path / "invalid.yaml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(p)


def test_missing_config_explicit_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_rules(tmp_path / "missing.yaml")


@pytest.mark.parametrize("title", [
    "美陆军采购防空雷射系统", "美海军接收第六艘基地舰", "德国空军战机实弹试射",
    "中国试射飞弹引忧 太平洋岛国关切", "中科院研发民用雷达系统",
    "采购网突然发送过期资讯 中科院回应网站遭破解", "希腊爱国者飞弹拦截无人机",
])
def test_foreign_or_civilian_news_not_rescued_by_taiwan_summary(rules, title):
    assert not classify(article(title, summary="相关背景：台湾军方关注这些消息。"), rules).is_military


def test_pla_mentioned_as_background_does_not_override_main_actor(rules):
    f = classify(article("首批MQ-9B无人机抵台 空军组装测试强化监控共军动态"), rules)
    assert f.is_military and f.category == "taiwan_equipment"


def test_distinct_missile_payloads_not_merged(rules):
    data = [article("台军F-16V测试天弓飞弹", 1), article("台军F-16V测试雄风飞弹", 2)]
    assert len(build_events(data, rules)) == 2


def test_shared_broad_location_does_not_hide_specific_conflict(rules):
    data = [article("台海战备：F-16V在花莲测试", 1), article("台海战备：F-16V在台东测试", 2)]
    assert len(build_events(data, rules)) == 2


def test_daily_bulletins_merge_same_day_but_not_next_day(rules):
    title = "中共解放军台海周边活动 国军严密监控应处"
    assert len(build_events([article(title, 1), article(title, 2, 1)], rules)) == 1
    assert len(build_events([article(title, 1), article(title, 2, 24)], rules)) == 2


def test_explicit_arms_sale_without_named_weapon_is_retained(rules):
    f = classify(article("美国批准对台军售"), rules)
    assert f.is_military and f.category == "arms_sale" and f.us_related


def test_invalid_yaml_syntax(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("version: [", encoding="utf-8")
    with pytest.raises(ValueError, match="syntax"):
        load_rules(p)


def test_legislative_notice_not_exercise_plan(rules):
    assert classify(article("国防部预告修法调整逃兵处罚"), rules).category == "other_military"
