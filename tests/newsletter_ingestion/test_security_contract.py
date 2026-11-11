from pathlib import Path


def test_oauth_material_patterns_are_ignored():
    text = Path(".gitignore").read_text(encoding="utf-8")
    for pattern in ("*credentials*.json", "*token*.json", "*.pickle", "*.p12", ".oauth/", "oauth/", "secrets/"):
        assert pattern in text
