from pathlib import Path


def test_gmail_dependencies_have_bounded_official_ranges():
    text = Path("requirements.txt").read_text(encoding="utf-8")
    assert "google-api-python-client>=2.170,<3" in text
    assert "google-auth>=2.35,<3" in text
    assert "google-auth-httplib2>=0.2,<1" in text
    assert "google-auth-oauthlib>=1.2,<2" in text


def test_dependency_record_is_explicit_and_gitless():
    import json

    record = json.loads(Path("validation/international_media/dependency_versions.json").read_text(encoding="utf-8"))
    assert record["git_state"] == "absent"
    assert set(record["requirements"]) == {
        "google-api-python-client",
        "google-auth",
        "google-auth-httplib2",
        "google-auth-oauthlib",
    }
