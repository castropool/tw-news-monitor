import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def _run_build(bundle_dir: Path):
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "build_tainan_assessment_deployment_bundle.ps1"),
            "-OutputDir",
            str(bundle_dir),
            "-Force",
        ],
        capture_output=True,
        cwd=str(PROJECT_ROOT),
        timeout=300,
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def _run_validate(bundle_dir: Path):
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "validate_tainan_assessment_deployment.ps1"),
            "-BundleDir",
            str(bundle_dir),
        ],
        capture_output=True,
        cwd=str(PROJECT_ROOT),
        timeout=300,
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


class TestDeploymentBundle:
    def test_build_bundle(self, tmp_path):
        code, stdout, stderr = _run_build(tmp_path)
        assert code == 0, stderr
        for rel in (
            "MANIFEST.json",
            "SHA256SUMS",
            "VERSION",
            "README_DEPLOYMENT.md",
            "requirements.txt",
            "config/election_assessment.yaml",
            "config/election_assessment_deployment.example.yaml",
            "config/feishu_delivery.example.yaml",
            "config/election_candidate_pipeline.yaml",
            "config/coverage_acceptance_rules.yaml",
            "config/sources.yaml",
            "scripts/install_tainan_assessment_tasks.ps1",
            "scripts/run_tainan_assessment.bat",
            "app/assessment/run_assessment_pipeline.py",
            "app/election_context/formal_state_validator.py",
            "app/election_context/coverage_builder.py",
            "app/election_context/snapshot_pipeline.py",
            "app/election_candidates/publication_pipeline.py",
            "app/election_candidates/publication_recovery.py",
            "data/election_seed/tainan_2026/actors.yaml",
            "data/election_seed/tainan_2026/poll_questions.jsonl",
            "data/election_seed/tainan_2026/poll_results.jsonl",
            "data/election_seed/tainan_2026/seed_manifest.json",
            "data/election_seed/tainan_2026/schema_versions.json",
            "run_monitor.bat",
            "install_scheduled_task.ps1",
        ):
            assert (tmp_path / rel).exists(), rel

    def test_no_secret_files(self, tmp_path):
        _run_build(tmp_path)
        assert not (tmp_path / ".env").exists()
        assert not (tmp_path / ".git").exists()
        assert not list(tmp_path.rglob("__pycache__"))
        assert not list(tmp_path.rglob("*.pyc"))
        assert not list(tmp_path.rglob("*.log"))

    def test_manifest_hashes_match(self, tmp_path):
        _run_build(tmp_path)
        manifest = json.loads((tmp_path / "MANIFEST.json").read_text(encoding="utf-8"))
        assert manifest["file_count"] == len(manifest["files"])
        actual = {
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
            and path.name not in {"MANIFEST.json", "SHA256SUMS", "validation.json"}
        }
        assert set(manifest["files"]) == actual
        sample = list(manifest["files"].items())[:10]
        for rel, expected in sample:
            data = (tmp_path / rel).read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            assert actual == expected, rel

    def test_sha256sums_complete(self, tmp_path):
        _run_build(tmp_path)
        lines = [
            line for line in (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").splitlines() if line
        ]
        assert lines
        for line in lines:
            assert re.match(r"^[0-9a-f]{64} \*", line)

    def test_no_dev_absolute_paths(self, tmp_path):
        _run_build(tmp_path)
        bad = re.compile(r"D:\\WXWorkLocal\\TW News-Monitor111|C:\\Users\\User\\", re.IGNORECASE)
        hits = []
        for path in tmp_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if bad.search(text):
                hits.append(path)
        assert hits == []

    def test_no_secret_values(self, tmp_path):
        _run_build(tmp_path)
        patterns = [
            re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
            re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/"),
            re.compile(r"Authorization\s*[:=]\s*(Bearer\s+)?[A-Za-z0-9._\-]{16,}"),
        ]
        for path in tmp_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern in patterns:
                assert not pattern.search(text), f"{path}: secret pattern"

    def test_validate_script_passes(self, tmp_path):
        _run_build(tmp_path)
        code, stdout, stderr = _run_validate(tmp_path)
        assert code == 0, stdout + stderr
        validation = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
        assert validation["bundle_valid"] is True
        assert validation["errors"] == []

    def test_validate_script_detects_tamper(self, tmp_path):
        _run_build(tmp_path)
        (tmp_path / "app" / "assessment" / "run_assessment_pipeline.py").write_text(
            "tampered", encoding="utf-8"
        )
        code, stdout, stderr = _run_validate(tmp_path)
        assert code == 1
        assert "sha256_mismatch" in stdout

    def test_bundle_contains_schema_1_1(self, tmp_path):
        _run_build(tmp_path)
        schema = json.loads(
            (tmp_path / "app" / "assessment" / "schemas" / "tainan_assessment_report_v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        assert schema["properties"]["schema_version"]["const"] == "1.1"
        assert "data_context" in schema["required"]
        assert "data_context" in schema["properties"]

    def test_bundle_contains_audit_tool(self, tmp_path):
        _run_build(tmp_path)
        assert (
            tmp_path / "app" / "assessment" / "credential_incident_audit.py"
        ).exists()

    def test_bundle_readme_contains_rotation_steps(self, tmp_path):
        _run_build(tmp_path)
        readme = (tmp_path / "README_DEPLOYMENT.md").read_text(encoding="utf-8")
        assert "重置 App Secret" in readme or "重新生成 Webhook" in readme
        assert "feishu_credentials_rotated_after_incident" in readme

    def test_bundle_templates_contain_no_real_values(self, tmp_path):
        _run_build(tmp_path)
        for rel in (
            "config/election_assessment_deployment.example.yaml",
            "config/feishu_delivery.example.yaml",
            "config/election_assessment.yaml",
        ):
            text = (tmp_path / rel).read_text(encoding="utf-8")
            assert "FEISHU_WEBHOOK=" not in text or "FEISHU_WEBHOOK= " in text or "\nFEISHU_WEBHOOK=" not in text
            assert "app_secret_env: FEISHU_APP_SECRET" in text

    def test_bundle_contains_no_old_secret_files(self, tmp_path):
        _run_build(tmp_path)
        assert not (tmp_path / ".env").exists()
        assert not (tmp_path / ".env.example").exists()

    def test_bundle_contains_updated_scripts_and_preflight(self, tmp_path):
        _run_build(tmp_path)
        assert (tmp_path / "scripts" / "install_tainan_assessment_tasks.ps1").exists()
        assert (
            tmp_path / "app" / "assessment" / "deployment_preflight.py"
        ).exists()
