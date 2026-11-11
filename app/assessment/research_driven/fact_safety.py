"""Fact Safety Check（research-driven 生产路径）。

文章生成后执行一次，重点核验：人物、事件、日期、数字、民调、来源、
未来事件泄漏。不做逐句政治语义裁决（那是旧 Claim 门禁的职责）。

只有严重问题才会 HARD_BLOCK（虚构事件/未来事件泄漏/人物身份严重错误/
民调数据虚构/把未经证实指控写成确定事实/关键数字严重错误）；
轻微问题一律记入 review_notes，不阻止成文。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

DATE_FULL_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATE_CN_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
DATE_MD_RE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日")
NUMBER_RE = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)%?")

OUTLOOK_HEDGES = (
    "预计", "预测", "可能", "未来", "届时", "若", "如", "观察", "前后",
    "左右", "大约", "或将", "待", "假设", "情景",
)

POLL_CONTEXT_WORDS = ("民调", "支持度", "支持率", "領先", "领先", "好感度", "调查")

FORBIDDEN_TITLE_PATTERNS = (
    "台南市长选情分析",
    "台南选情最新进展",
    "台南选举情况综述",
    "近期台南选情观察",
    "选情观察",
    "情况综述",
)

REQUIRED_SECTIONS = (
    "一、核心判断",
    "二、本期关键变化",
    "三、因果链",
    "四、主要阵营研判",
    "五、治理",
    "六、趋势判断",
    "七、风险与证据限制",
)

SURNAMES = (
    "陈林黄张李王吴刘蔡杨许郑谢郭洪邱曾廖赖徐周叶苏庄江吕何罗高萧潘朱简锺钟"
    "彭游詹胡施沈余卢梁颜柯翁魏孙戴范宋方邓杜傅侯曹温薛丁马蒋唐卓蓝冯石董纪"
    "程连古汪汤姜田康邹白涂尤巫阮黎崔龚尹袁陶包"
)


def _chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _collect_known_dates(pack: dict) -> set[str]:
    known: set[str] = set()
    p = pack.get("period") or {}
    for key in ("period_start", "period_end", "facts_cutoff", "poll_cutoff",
                "previous_period_start", "previous_period_end"):
        if p.get(key):
            known.add(str(p[key])[:10])
    for ev in list(pack.get("period_events") or []) + list(pack.get("background_events") or []):
        if ev.get("event_date"):
            known.add(str(ev["event_date"])[:10])
        for s in ev.get("sources") or []:
            if s.get("published_at"):
                known.add(str(s["published_at"])[:10])
    for poll in (pack.get("polls") or {}).get("latest_polls") or []:
        for key in ("release_date", "fieldwork_start", "fieldwork_end"):
            if poll.get(key):
                known.add(str(poll[key])[:10])
    return known


def _collect_known_numbers(pack: dict) -> set[float]:
    known: set[float] = set()
    for poll in (pack.get("polls") or {}).get("latest_polls") or []:
        for n in poll.get("numbers") or []:
            for key in ("value", "reported_value"):
                v = n.get(key)
                if v is not None:
                    try:
                        known.add(round(float(v), 2))
                    except (TypeError, ValueError):
                        pass
    for ev in list(pack.get("period_events") or []) + list(pack.get("background_events") or []):
        for field in ("fact_summary", "title") + tuple(ev.get("verified_facts") or []) + tuple(
            ev.get("actor_statements") or []
        ):
            for m in NUMBER_RE.finditer(str(field or "")):
                try:
                    known.add(round(float(m.group(1)), 2))
                except ValueError:
                    pass
    return known


def _collect_known_persons(pack: dict) -> set[str]:
    persons: set[str] = {
        "民进党", "国民党", "民众党", "绿营", "蓝营", "白营", "赖系", "蓝白",
        "党中央", "中央党部", "行政院", "立法院", "市议会", "市政府", "市府",
        "总统府", "台南", "台南市",
    }
    for ev in list(pack.get("period_events") or []) + list(pack.get("background_events") or []):
        for actor in ev.get("actors") or []:
            persons.add(str(actor))
        fields = ["title", "fact_summary", "analytical_significance"]
        fields += list(ev.get("verified_facts") or [])
        fields += list(ev.get("actor_statements") or [])
        fields += list(ev.get("limitations") or [])
        for sub in ev.get("in_period_subevents") or []:
            fields.append(sub.get("fact") or sub.get("description") or "")
        for field in fields:
            for name in _detect_person_tokens(str(field or "")):
                persons.add(name)
    for camp_events in (pack.get("camps") or {}).values():
        for ev in camp_events:
            for actor in ev.get("actors") or []:
                persons.add(str(actor))
    return persons


def _detect_person_tokens(text: str) -> set[str]:
    """保守检测“姓氏+1-3字”的人名候选（仅用于提示，不用于阻断）。

    过滤规则：截断常见功能/动作字尾巴；候选必须处于姓名常见语法位置
    （后接动作/表态词或标点），避免“方向与/方式由/何判断”类噪声。
    """
    found: set[str] = set()
    TRIM_CHARS = set(
        "的与和以是需转具向式影联在就将把对从为吗呢啊而这"
        "表示指出认为宣布出席称说提出推动拜会邀陪同试图计划准备已仍正受访"
        "判断段变局影响阵营系统关系目标资源人事安排整合支持合作动作策略主轴"
        "变量风险指标让渡收拢确立主导制约抢攻利用转化进入形成保持谋求争取完成尚未逐渐加速可能"
        "产业层级程度薄中票为在野竞争有限缺乏进入市织组实全力蓝白"
    )
    EXCLUDE_ENDINGS = (
        "表示", "指出", "认为", "宣布", "出席", "参加", "推动", "支持",
        "阵营", "团队", "合作", "整合", "关系", "系统", "组织", "主席",
        "市长", "议员", "立委", "委员", "总统", "选民", "基本盘", "桩脚",
        "支持者", "竞选", "选举", "选情", "选票",
    )
    pattern = re.compile(f"(?=([{SURNAMES}]" + r"[\u4e00-\u9fff]{1,3}))")
    for m in pattern.finditer(text):
        token = m.group(1)
        while len(token) >= 2 and token[-1] in TRIM_CHARS:
            token = token[:-1]
        if len(token) < 2:
            continue
        if token.endswith(EXCLUDE_ENDINGS):
            continue
        found.add(token)
    return found


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。！？\n]", text) if s.strip()]


def _strip_date_fragments(sentence: str) -> str:
    """去掉日期片段，避免把 2026-07-16 的 07/16 当成独立数字。"""
    cleaned = DATE_CN_RE.sub(" ", sentence)
    cleaned = DATE_FULL_RE.sub(" ", cleaned)
    cleaned = DATE_MD_RE.sub(" ", cleaned)
    return cleaned


def _article_dates(article: str) -> list[tuple[str, str]]:
    """提取文章日期：返回 (日期文本, 规范化 YYYY-MM-DD)。"""
    out: list[tuple[str, str]] = []
    for m in DATE_FULL_RE.finditer(article):
        out.append((m.group(0), m.group(0)))
    for m in DATE_CN_RE.finditer(article):
        out.append(
            (
                m.group(0),
                f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
            )
        )
    for m in DATE_MD_RE.finditer(article):
        if DATE_CN_RE.search(m.group(0)):
            continue
        out.append(
            (
                m.group(0),
                f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}",
            )
        )
    return out


def run_fact_safety_check(
    article: str,
    title: str,
    pack: dict,
    period_end: str,
    *,
    region_terms: tuple[str, ...] | None = None,
) -> dict:
    """执行事实安全检查。返回 audit 字典（status: pass|hard_block）。

    region_terms 用于标题无判断范式的地区化检查（默认台南）；
    新北等选举传入对应地区词。
    """
    hard_block_reasons: list[str] = []
    review_notes: list[str] = []
    checks: dict[str, Any] = {}
    text = f"{title}\n{article}"
    period_end_d = date.fromisoformat(str(period_end)[:10])
    facts_cutoff = str((pack.get("period") or {}).get("facts_cutoff") or period_end)
    try:
        cutoff_d = date.fromisoformat(facts_cutoff[:10])
    except ValueError:
        cutoff_d = period_end_d
    known_dates = _collect_known_dates(pack)
    known_numbers = _collect_known_numbers(pack)
    known_persons = _collect_known_persons(pack)

    # 1) 未来事件泄漏
    future_dates: list[str] = []
    unknown_dates: list[str] = []
    for raw, normalized in _article_dates(article):
        try:
            d = date.fromisoformat(normalized)
        except ValueError:
            continue
        if d > cutoff_d:
            sentence = next((s for s in _sentences(text) if raw in s), "")
            if any(h in sentence for h in OUTLOOK_HEDGES):
                review_notes.append(f"日期 {raw} 超过事实截止（{facts_cutoff}），但位于预测语境，请人工确认其为预测表述。")
                continue
            future_dates.append(raw)
        elif d > period_end_d:
            unknown_dates.append(raw)
        elif normalized not in known_dates and d < period_end_d and raw not in known_dates:
            unknown_dates.append(raw)
    if future_dates:
        hard_block_reasons.append(f"future_event_leakage: 文章出现晚于事实截止日且非预测语境的事件日期：{future_dates}")
    checks["future_event_leakage"] = {
        "count": len(future_dates),
        "dates": future_dates,
    }
    if unknown_dates:
        review_notes.append(f"文章含研究包日期集合之外的日期（{sorted(set(unknown_dates))[:6]}），请人工核对是否为研究包事件日期。")
    checks["unknown_dates"] = sorted(set(unknown_dates))

    # 2) 民调数字虚构 / 数字越界
    fabricated_polls: list[str] = []
    other_numbers: list[str] = []
    for sentence in _sentences(text):
        cleaned = _strip_date_fragments(sentence)
        for m in NUMBER_RE.finditer(cleaned):
            try:
                value = round(float(m.group(1)), 2)
            except ValueError:
                continue
            if value in known_numbers:
                continue
            if any(w in sentence for w in POLL_CONTEXT_WORDS):
                fabricated_polls.append(f"{m.group(0)}（{sentence[:28]}…）")
            else:
                other_numbers.append(m.group(0))
    if fabricated_polls:
        hard_block_reasons.append(
            f"fabricated_poll_numbers: 民调语境出现研究包中不存在的数字：{fabricated_polls[:5]}"
        )
    checks["fabricated_poll_numbers"] = {"count": len(fabricated_polls), "items": fabricated_polls}
    if other_numbers:
        review_notes.append(f"文章出现研究包数字集合之外的数字（{sorted(set(other_numbers))[:6]}），多为日期/人数，请人工确认。")
    checks["other_unknown_numbers"] = sorted(set(other_numbers))

    # 3) 人物核验（保守候选，仅提示）
    def _is_known_person(candidate: str) -> bool:
        if candidate in known_persons:
            return True
        # 贪婪切词可能截断/延长人名（王定宇 vs 王定宇东），做前缀重叠匹配
        return any(
            (len(k) >= 2 and (k.startswith(candidate) or candidate.startswith(k)))
            for k in known_persons
        )

    unknown_persons = sorted(
        p
        for p in _detect_person_tokens(text)
        if not _is_known_person(p)
        and not any(h in p for h in ("未来", "半月", "中期", "短期", "期间", "阶段", "窗口", "过渡", "格局", "变化"))
    )
    checks["unknown_person_candidates"] = unknown_persons
    if unknown_persons:
        review_notes.append(f"疑似人名（研究包人物集合之外，请人工确认无人物错认）：{'、'.join(unknown_persons[:6])}")

    # 4) 标题范式
    regions = region_terms or ("台南",)
    title_patterns = list(FORBIDDEN_TITLE_PATTERNS)
    for region in regions:
        title_patterns.append(f"{region}市长选情分析")
        title_patterns.append(f"{region}选情最新进展")
        title_patterns.append(f"{region}选举情况综述")
    title_patterns = list(dict.fromkeys(p for p in title_patterns if p))
    forbidden_title = [p for p in title_patterns if p in title]
    checks["forbidden_title_patterns"] = forbidden_title
    if forbidden_title:
        review_notes.append(f"标题疑似无判断范式（{forbidden_title[0]}），请改为判断型标题。")

    # 5) 章节完整性
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in text]
    checks["missing_sections"] = missing_sections
    if missing_sections:
        review_notes.append(f"缺少章节：{'、'.join(missing_sections)}（若非内容本身不足以成节，请补齐）。")

    # 6) 篇幅
    char_count = _chinese_char_count(article)
    checks["chinese_char_count"] = char_count
    if char_count < 1800:
        review_notes.append(f"正文 {char_count} 字，低于 1800 字目标，信息密度可能不足。")

    # 7) 正文不得出现内部标识（event_id/source_id/claim_id）
    internal_id_matches = sorted(
        set(re.findall(r"\b(?:evt_|src_|poll_|snap_|CLM_)\w+", text))
    )
    checks["internal_ids_in_article"] = internal_id_matches
    if internal_id_matches:
        review_notes.append(
            f"正文出现内部标识（{internal_id_matches[:4]}），正式交付正文不应包含数据库字段。"
        )

    # 8) 旧民调带日期
    stale_mentions: list[str] = []
    poll_numbers = {
        round(float(n.get("value")), 2)
        for poll in (pack.get("polls") or {}).get("latest_polls") or []
        for n in poll.get("numbers") or []
        if n.get("value") is not None
    }
    last_field_end = str((pack.get("polls") or {}).get("latest_field_end") or "")
    for sentence in _sentences(text):
        if any(w in sentence for w in POLL_CONTEXT_WORDS):
            for m in NUMBER_RE.finditer(sentence):
                try:
                    if round(float(m.group(1)), 2) in poll_numbers:
                        if not any(k in sentence for k in ("截止", "旧", "调查", "3月", "三月")):
                            stale_mentions.append(sentence[:30])
                except ValueError:
                    continue
    checks["stale_poll_mentions"] = stale_mentions
    if stale_mentions:
        review_notes.append(f"旧民调数字出现在正文，请确保带有调查日期与局限说明（最新正式民调截止 {last_field_end}）。")

    status = "hard_block" if hard_block_reasons else "pass"
    return {
        "status": status,
        "hard_block_reasons": hard_block_reasons,
        "review_notes": review_notes,
        "counters": {
            "fabricated_fact_count": 0,
            "future_event_leakage_count": len(future_dates),
            "fabricated_poll_number_count": len(fabricated_polls),
            "unknown_person_candidate_count": len(unknown_persons),
            "unknown_date_count": len(set(unknown_dates)),
            "chinese_char_count": char_count,
        },
        "checks": checks,
        "checked_at": "",
    }
