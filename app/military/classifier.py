"""Title-led, Taiwan-context military classification; no network or models."""
from dataclasses import dataclass
import re
import unicodedata

from ..election2026.hanzi_utils import to_traditional
from ..models import Article


def normalize_title(title):
    text = unicodedata.normalize("NFKC", title or "")
    text = re.sub(r"^(?:(?:【(?:快訊|快讯)】|(?:快訊|快讯|中央社|聯合報|联合报|台媒|自由時報|自由时报)\s*[:：/|])\s*)+", "", text)
    text = re.sub(r"(?i)(?<![a-z0-9])F\s*[-－]?\s*16\s*V(?![a-z0-9])", "F-16V", text)
    text = re.sub(r"(?i)(?<![a-z0-9])F\s*[-－]?\s*16(?![a-z0-9])", "F-16", text)
    return re.sub(r"\s+", " ", text).strip()


def folded(text):
    text = to_traditional(normalize_title(text)).replace("臺", "台").replace("发", "發")
    return re.sub(r"\s+", "", text).casefold().replace("導彈", "飛彈")


def contains(text, term):
    value = folded(term)
    if value.isascii() and any(c.isalnum() for c in value):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(value) + r"(?![a-z0-9])", text))
    return value in text


def matches(text, mapping):
    return frozenset(key for key, aliases in mapping.items() if any(contains(text, a) for a in aliases))


def any_term(text, terms):
    return any(contains(text, term) for term in terms)


@dataclass(frozen=True)
class Features:
    article: Article
    title: str
    text: str
    is_military: bool
    reason: str
    category: str
    organizations: frozenset
    weapons: frozenset
    equipment: frozenset
    named_events: frozenset
    places: frozenset
    actions: frozenset
    primary_action: str
    negative_status: bool
    us_related: bool

    @property
    def entities(self):
        return self.organizations | self.weapons | self.equipment | self.named_events


def classify(article, rules):
    title = normalize_title(article.title)
    text = folded(title)
    ent = rules["entities"]
    title_anchors = matches(text, ent["organizations"]) | matches(text, ent["weapons"]) | matches(text, ent["equipment"]) | matches(text, ent["named_events"])
    strong_signal = any_term(text, rules["scope"]["strong_signals"])
    # Existing summary only assists a title that already has a military anchor.
    # Keep event features/title scoring title-led: unrelated trailing text cannot bridge events.
    evidence = text
    if title_anchors and article.summary:
        evidence += folded(article.summary[:240])
    groups = {name: matches(text, ent[name]) for name in ent}
    actions = matches(text, rules["actions"])
    if groups["named_events"] and not actions:
        actions = frozenset({"exercise"})
    evidence_actions = actions or matches(evidence, rules["actions"])
    orgs, weapons, equipment, named = (groups[k] for k in ("organizations", "weapons", "equipment", "named_events"))
    explicit_tw = any_term(text, rules["scope"]["taiwan"])
    foreign = any_term(text, rules["scope"]["foreign"])
    domestic_org = bool(orgs - {"解放军", "美军", "中科院"})
    implicit_tw = not foreign and (domestic_org or bool(weapons) or bool(named) or "中科院" in orgs)
    in_scope = explicit_tw or implicit_tw
    if "解放军" in orgs or weapons & {"山东舰", "辽宁舰"}:
        in_scope = explicit_tw
    reason = "entity_action_context"
    accepted = bool((title_anchors or strong_signal) and evidence_actions and in_scope)
    # Dual-use technology requires military context, not just a Taiwan place name.
    if equipment and equipment <= {"卫星", "无人机", "雷达"} and not ((orgs - {"中科院"}) or weapons or named):
        accepted = accepted and any_term(evidence, rules["scope"]["military_context"])
    if orgs == {"中科院"} and not (weapons or named or equipment):
        accepted = accepted and any_term(text, rules["scope"]["military_context"])
    if not article.url or not title:
        accepted, reason = False, "empty_title_or_url"
    elif any_term(text, rules["negative"]["contexts"]):
        accepted, reason = False, "non_news_military_context"
    elif any_term(text, rules["negative"]["political_noise"]) and not weapons:
        accepted, reason = False, "political_noise_without_equipment_event"
    elif not accepted:
        reason = "insufficient_military_or_taiwan_evidence"
    primary = next((key for key in rules["action_priority"] if key in actions), "unknown")
    category = "other_military"
    us = any_term(evidence, rules["scope"]["us"])
    pla_positions = [text.find(folded(alias)) for alias in ent["organizations"]["解放军"] if contains(text, alias)]
    domestic_positions = [text.find(folded(alias)) for key in ("国军", "陆军", "海军", "空军")
                          for alias in ent["organizations"][key] if contains(text, alias)]
    pla_subject = bool(pla_positions) and (not domestic_positions or min(pla_positions) < min(domestic_positions))
    if accepted:
        if (pla_subject or weapons & {"山东舰", "辽宁舰"}) and primary in {"transit", "exercise", "deployment", "test", "unknown", "inspection"}:
            category = "pla_activity"
        elif primary == "budget":
            category = "other_military"
        elif primary == "plan" and not (named or "exercise" in actions):
            category = "other_military"
        elif primary in rules["category_actions"]["arms_sale"]:
            category = "arms_sale"
        elif any_term(text, ["美台軍事", "美軍與台軍", "美軍和國軍"]) or ("美军" in orgs and explicit_tw):
            category = "us_taiwan_military"
        else:
            for key, values in rules["category_actions"].items():
                if primary in values:
                    category = key
                    break
    return Features(article, title, text, accepted, reason, category,
                    orgs, weapons, equipment, named, groups["places"], actions, primary,
                    any_term(text, rules["negative"]["status_negative"]),
                    us)
