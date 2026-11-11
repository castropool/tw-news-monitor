"""候选人实体库加载与查询。

实体命中只是九合一识别的辅助证据；本模块不参与判定，只提供
"文本中出现哪些实体 + 实体关联县市"的查询能力。
"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class CandidateEntity:
    """一个候选人实体（含别名展开后的全部称呼）。"""

    canonical_name: str
    party: str = ""
    regions: list[str] = field(default_factory=list)
    status: str = ""
    # canonical + aliases 的完整匹配词列表
    names: list[str] = field(default_factory=list)


def build_entity_index(entities_config: dict) -> list[CandidateEntity]:
    """把 entities yaml 的 candidates 块展开为实体索引。

    每个实体的 names 包含标准名与全部别名（保留原样，不做繁简转换，
    配置负责繁简并列）。
    """
    candidates = (entities_config or {}).get("candidates", {})
    index: list[CandidateEntity] = []
    for canonical, info in candidates.items():
        if not isinstance(info, dict):
            continue
        aliases = info.get("aliases") or []
        names = [canonical] + [a for a in aliases if isinstance(a, str)]
        index.append(
            CandidateEntity(
                canonical_name=canonical,
                party=info.get("party", ""),
                regions=list(info.get("regions") or []),
                status=info.get("status", ""),
                names=names,
            )
        )
    return index


def find_matched_entities(text: str, entity_index: list[CandidateEntity]) -> list[CandidateEntity]:
    """返回文本中命中的实体（去重，保持实体库顺序）。"""
    if not text or not entity_index:
        return []
    matched: list[CandidateEntity] = []
    for entity in entity_index:
        if any(name and name in text for name in entity.names):
            matched.append(entity)
    return matched
