import json
from pathlib import Path

from app.assessment.credential_incident_audit import (
    run_incident_audit,
    scan_path,
    scan_text,
    write_audit,
)


SECRET_VALUE = "test_secret_value_abcdefghijklmnopqrstuvwxyz123456"


class TestCredentialIncidentAudit:
    def test_scan_text_detects_webhook_without_value(self):
        hits = scan_text("url=https://open.feishu.cn/open-apis/bot/v2/hook/abc123")
        assert "feishu_webhook_url" in hits
        assert "abc123" not in json.dumps(hits)

    def test_scan_text_detects_app_secret_assignment(self):
        hits = scan_text("FEISHU_APP_SECRET=real_secret_here")
        assert "feishu_app_secret_assignment" in hits

    def test_scan_text_detects_known_value(self):
        hits = scan_text("x " + SECRET_VALUE, known_secret_values=[SECRET_VALUE])
        assert "known_secret_value" in hits

    def test_empty_env_placeholders_not_flagged(self):
        hits = scan_text("FEISHU_WEBHOOK=\nFEISHU_APP_SECRET=\nFEISHU_APP_ID=")
        assert hits == []

    def test_env_names_not_flagged_as_values(self):
        hits = scan_text("FEISHU_WEBHOOK 只读环境变量名 DEEPSEEK_API_KEY")
        assert "feishu_webhook_assignment" not in hits
        assert "known_secret_value" not in hits

    def test_scan_path_redacted(self, tmp_path):
        target = tmp_path / "leak.txt"
        target.write_text("FEISHU_APP_SECRET=" + SECRET_VALUE, encoding="utf-8")
        locations = scan_path(target, known_secret_values=[SECRET_VALUE])
        assert locations
        assert SECRET_VALUE not in "".join(locations)
        assert "<redacted line>" in locations[0]

    def test_incident_audit_flags_worktree(self, tmp_path):
        (tmp_path / "secret.txt").write_text("FEISHU_APP_SECRET=" + SECRET_VALUE, encoding="utf-8")
        audit = run_incident_audit(
            tmp_path,
            known_secret_values=[SECRET_VALUE],
            rotation_acknowledged=False,
        )
        assert audit["incident_detected"] is True
        assert audit["current_worktree_exposure"] is True
        assert audit["rotation_required"] is True
        assert audit["production_delivery_blocked_until_rotation_acknowledged"] is True
        blob = json.dumps(audit, ensure_ascii=False)
        assert SECRET_VALUE not in blob

    def test_incident_audit_no_git_clean_worktree(self, tmp_path):
        audit = run_incident_audit(tmp_path, rotation_acknowledged=False)
        assert audit["current_worktree_exposure"] is False
        assert audit["git_tracked_exposure"] is False
        assert audit["git_history_exposure"] is False
        assert audit["git_history_cleanup_recommended"] is False

    def test_rotation_acknowledged_unblocks_delivery(self, tmp_path):
        audit = run_incident_audit(tmp_path, rotation_acknowledged=True)
        assert audit["production_delivery_blocked_until_rotation_acknowledged"] is False

    def test_bundle_scan(self, tmp_path):
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "good.txt").write_text("no secrets", encoding="utf-8")
        audit = run_incident_audit(
            tmp_path / "worktree" if False else tmp_path,
            bundle_root=bundle,
            rotation_acknowledged=True,
        )
        assert audit["deployment_bundle_exposure"] is False

    def test_write_audit_file(self, tmp_path):
        audit = run_incident_audit(tmp_path, rotation_acknowledged=False)
        path = write_audit(tmp_path, audit)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["incident_detected"] is True
        assert data["matched_locations_redacted"] == []

    def test_local_env_file_separated_from_worktree_exposure(self, tmp_path):
        (tmp_path / ".env").write_text(
            "FEISHU_APP_SECRET=real_secret_here\nDEEPSEEK_API_KEY=sk-test-1234567890abcdef\n",
            encoding="utf-8",
        )
        audit = run_incident_audit(tmp_path, rotation_acknowledged=False)
        assert audit["current_worktree_exposure"] is False
        assert audit["local_env_file_secrets_present"] is True
        blob = json.dumps(audit, ensure_ascii=False)
        assert "real_secret_here" not in blob
        assert "sk-test-1234567890abcdef" not in blob

    def test_git_repository_absent_reported(self, tmp_path):
        audit = run_incident_audit(tmp_path, rotation_acknowledged=False)
        assert audit["git_repository_present"] is False
        assert audit["git_scan_status"] == "repository_missing"
