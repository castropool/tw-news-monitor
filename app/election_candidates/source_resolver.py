"""Normalize news sources and match them read-only against formal sources."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .article_normalizer import normalize_domain, normalize_source_name


SUFFIXES = ["新聞網", "新聞", "電子報", "日報", "晚報", "網路報", "媒體", "頻道"]


def normalized_name_key(name: str) -> str:
    name = normalize_source_name(name)
    for suffix in SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    return name


def candidate_source_id(name: str, domain: str) -> str:
    raw = f"{name}|{domain}"
    return "csrc_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def resolve_sources(
    article_sources: list[dict[str, str]],
    formal_sources: list[dict[str, Any]],
    config,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    alias_map = config.get("source_resolver.alias_map", {}) or {}
    fuzzy_threshold = float(config.get("source_resolver.fuzzy_threshold", 0.85))

    formal_by_domain: dict[str, dict[str, Any]] = {}
    formal_by_name: dict[str, dict[str, Any]] = {}
    formal_by_norm_name: dict[str, list[dict[str, Any]]] = {}
    formal_list: list[dict[str, Any]] = []
    for fs in formal_sources:
        domain = normalize_domain(fs.get("url", ""))
        name = normalize_source_name(fs.get("publisher", "") or fs.get("title", ""))
        item = dict(fs)
        if domain:
            formal_by_domain.setdefault(domain, item)
        if name:
            formal_by_name.setdefault(name, item)
            formal_by_norm_name.setdefault(normalized_name_key(name), []).append(item)
        formal_list.append(item)

    result_sources: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    seen: set[str] = set()
    for src in article_sources:
        name = normalize_source_name(src.get("name", ""))
        domain = normalize_domain(src.get("url", ""))
        key = f"{name}|{domain}"
        if key in seen:
            continue
        seen.add(key)

        formal_id = ""
        status = "new_candidate_source"
        basis = "none"

        if domain and domain in formal_by_domain:
            formal_id = formal_by_domain[domain]["source_id"]
            status = "exact"
            basis = "domain_exact"
        elif name and name in formal_by_name:
            formal_id = formal_by_name[name]["source_id"]
            status = "exact"
            basis = "name_exact"
        else:
            alias = alias_map.get(name)
            if alias and alias in formal_by_name:
                formal_id = formal_by_name[alias]["source_id"]
                status = "normalized_match"
                basis = "alias_match"
            else:
                norm = normalized_name_key(name)
                if norm and norm in formal_by_norm_name:
                    formal_id = formal_by_norm_name[norm][0]["source_id"]
                    status = "normalized_match"
                    basis = "normalized_name_match"
                else:
                    best = None
                    best_ratio = 0.0
                    for fname in formal_by_name:
                        ratio = difflib.SequenceMatcher(None, name, fname).ratio()
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best = fname
                    if name and best and best_ratio >= fuzzy_threshold:
                        formal_id = formal_by_name[best]["source_id"]
                        status = "possible_match"
                        basis = f"fuzzy_name_match(ratio={best_ratio:.3f})"
                    elif not name and not domain:
                        status = "unresolved"
                        basis = "no_name_no_domain"

        if not formal_id and status == "new_candidate_source" and not name and not domain:
            status = "unresolved"
            basis = "no_name_no_domain"

        csrc_id = candidate_source_id(name, domain)
        result_sources.append(
            {
                "candidate_source_id": csrc_id,
                "normalized_source_name": name,
                "normalized_domain": domain,
                "original_source_names_json": json.dumps([src.get("name", "")], ensure_ascii=False),
                "formal_source_id": formal_id,
                "formal_match_status": status,
                "formal_match_basis": basis,
                "first_seen_at": src.get("first_seen_at", ""),
                "last_seen_at": src.get("last_seen_at", ""),
            }
        )
        links.append(
            {
                "candidate_id": src["candidate_id"],
                "candidate_source_id": csrc_id,
                "news_article_id": src["news_article_id"],
                "relationship_type": "reported_by",
            }
        )
    return result_sources, links
