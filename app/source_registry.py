SOURCE_REGISTRY = {
    "president_press": {
        "display_name": "台湾总统府",
        "section": "official",
        "source_type": "government",
        "document_type": "新闻稿",
        "display_order": 10,
        "status": "active",
    },
    "mnd_press": {
        "display_name": "台湾国防部",
        "section": "official",
        "source_type": "government",
        "document_type": "新闻稿",
        "display_order": 20,
        "status": "planned",
    },
    "cga_press": {
        "display_name": "台湾海巡署",
        "section": "official",
        "source_type": "government",
        "document_type": "新闻稿",
        "display_order": 30,
        "status": "planned",
    },
    "mofa_press": {
        "display_name": "台湾外交部",
        "section": "official",
        "source_type": "government",
        "document_type": "新闻稿",
        "display_order": 40,
        "status": "planned",
    },
    "dpp_news": {
        "display_name": "民主进步党中央",
        "section": "official",
        "source_type": "party",
        "document_type": "政党声明",
        "display_order": 50,
        "status": "planned",
    },
    "ey_cabinet_news": {
        "display_name": "行政院",
        "section": "official",
        "source_type": "government",
        "document_type": "新聞稿",
        "display_order": 60,
        "status": "active",
    },
    "ey_ministry_news": {
        "display_name": "行政院",
        "section": "official",
        "source_type": "government",
        "document_type": "新聞稿",
        "display_order": 70,
        "status": "active",
    },
    "ydn_defense_focus": {
        "display_name": "青年日报·国防焦点",
        "section": "official",
        "source_type": "official_military",
        "document_type": "军闻稿",
        "display_order": 80,
        "status": "active",
    },
    "ydn_weapons_tour": {
        "display_name": "青年日报·武备巡礼",
        "section": "official",
        "source_type": "official_military",
        "document_type": "军闻稿",
        "display_order": 81,
        "status": "active",
    },
    "ydn_military_world": {
        "display_name": "青年日报·军视界",
        "section": "official",
        "source_type": "official_military",
        "document_type": "军闻稿",
        "display_order": 82,
        "status": "active",
    },
    "mna_military": {
        "display_name": "国防部军事新闻通讯社",
        "section": "official",
        "source_type": "official_military",
        "document_type": "军闻稿",
        "display_order": 83,
        "status": "active",
    },
}

def get_source_info(source_id: str) -> dict:
    return SOURCE_REGISTRY.get(source_id, {})

def is_official_source(source_id: str) -> bool:
    return SOURCE_REGISTRY.get(source_id, {}).get("section") == "official"

def get_official_sources() -> list:
    result = []
    for sid, info in SOURCE_REGISTRY.items():
        if info["section"] == "official":
            result.append((info["display_order"], sid, info))
    result.sort(key=lambda x: x[0])
    return result


def validate_registry_against_sources(sources: list[dict]) -> list[str]:
    """Report active registry entries missing from the production source list."""
    source_ids = {
        str(source.get("id"))
        for source in sources
        if isinstance(source, dict) and source.get("id")
    }
    return [
        sid for sid, info in SOURCE_REGISTRY.items()
        if info.get("status", "active") == "active" and sid not in source_ids
    ]
