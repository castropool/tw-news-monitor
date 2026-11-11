"""International sources config contract — Phase I.

Locks the registration + config contract for the three international
sources: collector types registered, access_level conventions, enabled
flags, language/category.
"""

from pathlib import Path

import yaml

from app.main import COLLECTOR_MAP
from app.collectors.reuters import ReutersCollector
from app.collectors.ft_alphaville import FTAlphavilleCollector
from app.collectors.wsj import WSJRSSCollector

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"


def _sources() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def _by_id(sid: str) -> dict:
    return next(s for s in _sources() if s["id"] == sid)


def test_international_types_registered_in_collector_map():
    assert COLLECTOR_MAP["reuters"] is ReutersCollector
    assert COLLECTOR_MAP["ft_alphaville"] is FTAlphavilleCollector
    assert COLLECTOR_MAP["wsj_rss"] is WSJRSSCollector


def test_reuters_source_config_contract():
    s = _by_id("reuters_international")
    assert s["type"] == "reuters"
    assert s["category"] == "international"
    assert s["name"] == "Reuters"
    assert s["language"] == "en"
    # collector 代码显式赋值为 metadata_only；config 必须一致
    assert s["access_level"] == "metadata_only"
    # Phase I 功能完成后仍由人工决定是否启用生产来源。
    assert s["enabled"] is False


def test_ft_alphaville_source_config_contract():
    s = _by_id("ft_alphaville")
    assert s["type"] == "ft_alphaville"
    assert s["category"] == "international"
    assert s["name"] == "Financial Times"
    assert s["language"] == "en"
    assert s["access_level"] == "public"  # FT 保持 public
    # 真实网络门禁通过前不得自动启用生产来源。
    assert s["enabled"] is False


def test_wsj_source_config_contract():
    s = _by_id("wsj_international")
    assert s["type"] == "wsj_rss"
    assert s["category"] == "international"
    assert s["name"] == "Wall Street Journal"
    assert s["language"] == "en"
    assert s["access_level"] == "metadata_only"
    assert s["enabled"] is False  # 停用但实现完整，供未来启用


def test_full_config_validates_with_registered_types():
    from app.main import validate_sources_config
    sources = _sources()
    # 不应抛 SystemExit
    validate_sources_config(sources, COLLECTOR_MAP)
