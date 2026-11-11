"""2026 九合一选举专题的统一输出对象。"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class ElectionAnnotation:
    """一条新闻的九合一选举专题标注结果。

    is_election=False 时不保证其余字段有语义（scope/region 为空串/None）。
    """

    is_election: bool = False
    # national（全局动向）/ local（单一县市）/ multi_region（多县市）
    scope: str = ""
    # 主归属县市标准名（local 时有值）
    region: str | None = None
    # 涉及的全部县市标准名（multi_region 时有值；local 时为其单元素）
    regions: list[str] = field(default_factory=list)
    # nomination / polling / campaign / party_coordination / alliance /
    # faction / attack_defense / controversy / legal / policy / personnel / other
    event_type: str = ""
    # 0-100 置信分
    confidence: int = 0
    # 命中的强/辅助词（审计用）
    matched_terms: list[str] = field(default_factory=list)
    # 兼容单一 event_type 的次级事件标签。
    event_tags: list[str] = field(default_factory=list)
    # 判定原因：matched / review / rejected:<负向词>
    reason: str = ""
