from pathlib import Path

from app.newsletter_ingestion.parser import parse_message


FIXTURES = Path(__file__).parents[1] / "fixtures" / "international" / "newsletters"


def test_four_sources_have_eight_offline_payload_classes():
    files = sorted(FIXTURES.glob("*"))
    assert len(files) >= 32
    for source in ("wsj", "bloomberg", "reuters", "ft"):
        source_files = [path for path in files if path.name.startswith(source + "_")]
        assert len(source_files) >= 8
        for path in source_files:
            payload = path.read_bytes()
            assert parse_message(payload) or "empty" in path.name or "duplicate" in path.name

