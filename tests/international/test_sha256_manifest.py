from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import validation.international_media.build_sha256_manifest as manifest_module
from validation.international_media.build_sha256_manifest import (
    _matches_exclude,
    build_manifest,
)


@pytest.fixture
def gitless_project_root(tmp_path, monkeypatch):
    """Run Wave0 probes in an explicit workspace without repository metadata."""

    monkeypatch.setattr(manifest_module, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _prepare_project_roots(root: Path) -> None:
    for name in ("app", "config", "tests", "docs", "prompts"):
        (root / name).mkdir(parents=True, exist_ok=True)
    target = root / "validation" / "international_media" / "build_sha256_manifest.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(manifest_module.__file__), target)


def test_manifest_includes_builder_and_excludes_only_generated_json(gitless_project_root):
    root = gitless_project_root / "root"
    root.mkdir()
    (root / "keep_sha256_manifest_tool.py").write_bytes(b"source")
    (root / "sha256_manifest_generated.json").write_bytes(b"generated")
    output = gitless_project_root / "handoff.json"

    result = build_manifest([root], ["**/sha256_manifest*.json"], output)
    paths = {item["path"] for item in result["files"]}

    assert any(path.endswith("keep_sha256_manifest_tool.py") for path in paths)
    assert not any(path.endswith("sha256_manifest_generated.json") for path in paths)
    assert result["git_state"] == "absent"


def test_manifest_excludes_runtime_caches_but_keeps_source_builder(gitless_project_root):
    root = gitless_project_root / "root"
    (root / "nested" / "__pycache__").mkdir(parents=True)
    (root / "nested" / ".pytest_cache").mkdir(parents=True)
    (root / "nested" / "__pycache__" / "cached.py").write_bytes(b"cache")
    (root / "nested" / ".pytest_cache" / "state").write_bytes(b"cache")
    (root / "nested" / "cached.pyc").write_bytes(b"cache")
    (root / "nested" / "build_sha256_manifest.py").write_bytes(b"source")
    (root / "nested" / "keep.py").write_bytes(b"source")

    result = build_manifest([root], ["**/sha256_manifest*.json"], gitless_project_root / "out.json")
    paths = {item["path"] for item in result["files"]}

    assert any(path.endswith("build_sha256_manifest.py") for path in paths)
    assert any(path.endswith("keep.py") for path in paths)
    assert not any("__pycache__" in path for path in paths)
    assert not any(".pytest_cache" in path for path in paths)
    assert not any(path.endswith("cached.pyc") for path in paths)


def test_manifest_exclusion_matches_windows_separators():
    assert _matches_exclude(
        r"nested\__pycache__\cached.py", ["__pycache__/**"]
    )
    assert _matches_exclude(
        r"nested\.pytest_cache\state", [".pytest_cache/**"]
    )
    assert _matches_exclude(
        r"nested\sha256_manifest_generated.json", ["**/sha256_manifest*.json"]
    )
    assert not _matches_exclude(
        r"nested\build_sha256_manifest.py", ["**/sha256_manifest*.json"]
    )


def test_manifest_includes_prompts_and_validation_roots(gitless_project_root):
    _prepare_project_roots(gitless_project_root)
    result = build_manifest(
        ["app", "config", "tests", "docs", "prompts", "validation/international_media"],
        ["**/sha256_manifest*.json"],
        gitless_project_root / "test_manifest_probe.json",
    )

    assert "prompts" in result["included_roots"]
    assert "validation/international_media" in result["included_roots"]
    assert any(
        item["path"] == "validation/international_media/build_sha256_manifest.py"
        for item in result["files"]
    )
    assert result["files"] == sorted(result["files"], key=lambda item: item["path"])


def test_manifest_records_missing_roots_and_refuses_overwrite(gitless_project_root):
    output = gitless_project_root / "manifest.json"
    result = build_manifest(["missing-root-for-wave0"], [], output)

    assert result["missing_roots"] == ["missing-root-for-wave0"]
    assert result["files"] == []
    with pytest.raises(FileExistsError):
        build_manifest([], [], output)


def test_manifest_hashes_file_with_stable_size_and_sha256(gitless_project_root):
    source = gitless_project_root / "sample.txt"
    source.write_bytes(b"wave0\n")
    result = build_manifest([source], [], gitless_project_root / "manifest.json")

    item = result["files"][0]
    assert item["size"] == 6
    assert len(item["sha256"]) == 64


def test_manifest_is_deterministic_across_repeated_outputs(gitless_project_root):
    root = gitless_project_root / "root"
    root.mkdir()
    (root / "keep.txt").write_bytes(b"stable")

    first = build_manifest([root], ["**/sha256_manifest*.json"], gitless_project_root / "one.json")
    second = build_manifest([root], ["**/sha256_manifest*.json"], gitless_project_root / "two.json")

    assert first == second


def test_manifest_excludes_same_output_inside_include_root_on_repeat(gitless_project_root):
    root = gitless_project_root / "root"
    root.mkdir()
    (root / "input.txt").write_bytes(b"stable")
    output = root / "ordinary_output.json"

    first = build_manifest([root], [], output)
    first_paths = {item["path"] for item in first["files"]}
    assert not any(path.endswith("ordinary_output.json") for path in first_paths)

    output.unlink()
    second = build_manifest([root], [], output)
    second_paths = {item["path"] for item in second["files"]}
    assert not any(path.endswith("ordinary_output.json") for path in second_paths)
    assert first == second


def test_manifest_different_outputs_exclude_their_own_path_but_keep_json_inputs(
    gitless_project_root,
):
    root = gitless_project_root / "root"
    root.mkdir()
    (root / "ordinary_input.json").write_bytes(b"input")
    first_output = root / "first_output.json"
    second_output = root / "second_output.json"

    first = build_manifest([root], [], first_output)
    second = build_manifest([root], [], second_output)
    first_paths = {item["path"] for item in first["files"]}
    second_paths = {item["path"] for item in second["files"]}

    assert any(path.endswith("ordinary_input.json") for path in first_paths)
    assert any(path.endswith("ordinary_input.json") for path in second_paths)
    assert not any(path.endswith("first_output.json") for path in first_paths)
    assert not any(path.endswith("second_output.json") for path in second_paths)
    # The first output is an ordinary JSON input to the second run; the
    # different-output manifests therefore need not be identical.
    assert any(path.endswith("first_output.json") for path in second_paths)
    assert first != second
