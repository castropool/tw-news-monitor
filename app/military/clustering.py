"""Deterministic complete-link clustering with inspectable pair decisions."""
from difflib import SequenceMatcher
import re

from ..time_utils import TAIPEI, normalize_published_at
from .classifier import any_term


def article_time(article):
    return normalize_published_at(article.published_at or article.fetched_at, assumed_timezone=TAIPEI)


def title_tokens(text):
    result = set(re.findall(r"[a-z]+[-\d]*[a-z]*", text))
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(part[i:i + 2] for i in range(len(part) - 1))
    return result


def compare(a, b, rules):
    cfg = rules["clustering"]
    hours = abs((article_time(a.article) - article_time(b.article)).total_seconds()) / 3600
    ta, tb = title_tokens(a.text), title_tokens(b.text)
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    similarity = (jaccard + SequenceMatcher(None, a.text, b.text, autojunk=False).ratio()) / 2
    shared_entities = a.entities & b.entities
    shared_actions = a.actions & b.actions
    anchors = (a.weapons & b.weapons) | (a.named_events & b.named_events)
    shared_places = a.places & b.places
    specific_a = a.places - set(cfg["broad_places"])
    specific_b = b.places - set(cfg["broad_places"])
    precise_title = similarity >= 0.85 and len(a.text) >= 10 and len(b.text) >= 10
    secondary_anchor = bool(a.equipment & b.equipment) or (precise_title and bool(a.organizations & b.organizations))
    score = (cfg["entity_weight"] * bool(anchors)
             + cfg["entity_weight"] * 0.5 * (not anchors and secondary_anchor)
             + cfg["action_weight"] * bool(shared_actions)
             + cfg["title_weight"] * similarity
             + cfg["place_weight"] * bool(shared_places)
             + cfg["time_weight"] * max(0, 1 - hours / cfg["window_hours"])
             - cfg["time_penalty"] * hours / cfg["window_hours"])
    window = cfg["window_hours"]
    if a.article.published_at is None or b.article.published_at is None:
        window = min(window, cfg["missing_time_window_hours"])
    rejection = None
    if hours > window:
        rejection = "outside_time_window"
    elif a.category == "pla_activity" and any_term(a.text + b.text, cfg["daily_bulletins"]) and article_time(a.article).date() != article_time(b.article).date():
        rejection = "different_daily_bulletin"
    elif a.category != b.category or a.us_related != b.us_related:
        rejection = "different_category"
    elif a.negative_status != b.negative_status:
        rejection = "different_event_status"
    elif a.primary_action != b.primary_action or not shared_actions:
        rejection = "different_action"
    elif (a.places and b.places and not shared_places) or (specific_a and specific_b and not specific_a & specific_b):
        rejection = "different_location"
    elif a.weapons and b.weapons and ((not a.weapons & b.weapons) or (a.weapons - b.weapons and b.weapons - a.weapons)):
        rejection = "different_weapon_model"
    elif a.named_events and b.named_events and not (a.named_events & b.named_events):
        rejection = "different_named_event"
    elif not anchors and not (secondary_anchor and (precise_title or (a.equipment & b.equipment and similarity >= 0.62))):
        rejection = "no_specific_event_anchor"
    elif similarity < cfg["min_title_similarity"] and not (a.named_events & b.named_events):
        rejection = "insufficient_title_overlap"
    elif score < cfg["threshold"]:
        rejection = "below_threshold"
    return {
        "left_url": a.article.url, "right_url": b.article.url,
        "matched_entities": sorted(shared_entities), "matched_actions": sorted(shared_actions),
        "matched_places": sorted(shared_places), "title_similarity": round(similarity, 4),
        "token_jaccard": round(jaccard, 4), "time_distance": round(hours, 3),
        "cluster_score": round(max(0, min(100, score)), 2),
        "merged": rejection is None, "rejection_reason": rejection,
    }


def cluster(features, rules):
    # Sorting makes the partition independent of SQL ordering and caller input.
    ordered = sorted(features, key=lambda f: (article_time(f.article), f.article.url))
    groups = []
    for candidate in ordered:
        best, best_links, best_score = None, [], -1
        for index, (members, _) in enumerate(groups):
            if (article_time(candidate.article) - article_time(members[0].article)).total_seconds() > rules["clustering"]["window_hours"] * 3600:
                continue
            links = [compare(member, candidate, rules) for member in members]
            if all(link["merged"] for link in links):
                score = min(link["cluster_score"] for link in links)
                if score > best_score:
                    best, best_links, best_score = index, links, score
        if best is None:
            groups.append(([candidate], []))
        else:
            groups[best][0].append(candidate)
            groups[best][1].extend(best_links)
    return groups
