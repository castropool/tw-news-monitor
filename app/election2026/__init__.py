"""2026 九合一选举专题包。

纯本地、确定性规则引擎：识别新闻是否属于 2026 九合一选举，
归类为 national / local / multi_region，输出结构化 ElectionAnnotation。

不依赖任何外部 API / LLM，可离线运行。分类结果为确定性纯函数输出，
对相同输入永远一致。
"""

from .models import ElectionAnnotation
from .classifier import classify_article, classify_articles

__all__ = [
    "ElectionAnnotation",
    "classify_article",
    "classify_articles",
]
