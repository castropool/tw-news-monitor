from pathlib import Path

from app.assessment.security_scan import scan_files, scan_text


class TestSecurityScan:
    def test_detects_app_secret_assignment(self):
        found = scan_text("FEISHU_APP_SECRET=real_secret_here")
        assert found["feishu_app_secret_exposed"] is True

    def test_empty_placeholder_not_flagged(self):
        found = scan_text("FEISHU_WEBHOOK=\nFEISHU_APP_SECRET=\nFEISHU_APP_ID=")
        assert found["feishu_webhook_exposed"] is False
        assert found["feishu_app_secret_exposed"] is False

    def test_env_var_name_not_flagged_as_value(self):
        found = scan_text("FEISHU_WEBHOOK 只读环境变量名 DEEPSEEK_API_KEY")
        assert found["feishu_webhook_exposed"] is False
        assert found["feishu_app_secret_exposed"] is False

    def test_source_code_reasoning_reference_not_persisted(self, tmp_path):
        (tmp_path / "example.py").write_text(
            'if "reasoning_content" in text: pass\n',
            encoding="utf-8",
        )
        result = scan_files(tmp_path)
        assert result["reasoning_content_persisted"] is False

    def test_persisted_reasoning_in_json_detected(self, tmp_path):
        (tmp_path / "output.json").write_text(
            '{"reasoning_content": "secret chain of thought"}',
            encoding="utf-8",
        )
        result = scan_files(tmp_path)
        assert result["reasoning_content_persisted"] is True

    def test_dev_absolute_path_detected(self, tmp_path):
        (tmp_path / "note.md").write_text(
            "D:\\WXWorkLocal\\TW News-Monitor111\\data\\x",
            encoding="utf-8",
        )
        result = scan_files(tmp_path)
        assert result["absolute_developer_path_exposed"] is True
