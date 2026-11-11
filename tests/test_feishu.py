import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestFeishuAuth:
    @patch("app.feishu.httpx.post")
    def test_get_tenant_access_token_success(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"code": 0, "tenant_access_token": "test_token_abc"}
        )
        from app.feishu import get_tenant_access_token
        token = get_tenant_access_token("app_id", "app_secret")
        assert token == "test_token_abc"

    @patch("app.feishu.httpx.post")
    def test_get_tenant_access_token_failure(self, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"code": 999, "msg": "invalid app_id"}
        )
        from app.feishu import get_tenant_access_token
        with pytest.raises(RuntimeError, match="Feishu auth failed"):
            get_tenant_access_token("bad", "bad")


class TestFeishuListChats:
    @patch("app.feishu.httpx.post")
    @patch("app.feishu.httpx.get")
    def test_list_chats_success(self, mock_get, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"code": 0, "tenant_access_token": "token"}
        )
        mock_get.return_value = MagicMock(
            json=lambda: {
                "code": 0,
                "data": {
                    "items": [
                        {"name": "测试群A", "chat_id": "oc_test_a"},
                        {"name": "台湾新闻监测", "chat_id": "oc_news_b"},
                    ],
                    "has_more": False,
                },
            }
        )
        from app.feishu import list_bot_chats
        chats = list_bot_chats("app_id", "app_secret")
        assert len(chats) == 2
        assert chats[0]["name"] == "测试群A"
        assert chats[1]["chat_id"] == "oc_news_b"

    @patch("app.feishu.httpx.post")
    @patch("app.feishu.httpx.get")
    def test_pagination(self, mock_get, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"code": 0, "tenant_access_token": "token"}
        )

        def page_responses(url, *args, **kwargs):
            if "page_token" in str(kwargs.get("params", {})):
                return MagicMock(
                    json=lambda: {
                        "code": 0,
                        "data": {
                            "items": [{"name": "群B", "chat_id": "oc_b"}],
                            "has_more": False,
                        },
                    }
                )
            return MagicMock(
                json=lambda: {
                    "code": 0,
                    "data": {
                        "items": [{"name": "群A", "chat_id": "oc_a"}],
                        "has_more": True,
                        "page_token": "next_page",
                    },
                }
            )

        mock_get.side_effect = page_responses
        from app.feishu import list_bot_chats
        chats = list_bot_chats("app_id", "app_secret")
        assert len(chats) == 2
        assert mock_get.call_count == 2

    @patch("app.feishu.httpx.post")
    @patch("app.feishu.httpx.get")
    def test_empty_chats(self, mock_get, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"code": 0, "tenant_access_token": "token"}
        )
        mock_get.return_value = MagicMock(
            json=lambda: {
                "code": 0,
                "data": {"items": [], "has_more": False},
            }
        )
        from app.feishu import list_bot_chats
        chats = list_bot_chats("app_id", "app_secret")
        assert chats == []

    @patch("app.feishu.httpx.post")
    @patch("app.feishu.httpx.get")
    def test_api_error(self, mock_get, mock_post):
        mock_post.return_value = MagicMock(
            json=lambda: {"code": 0, "tenant_access_token": "token"}
        )
        mock_get.return_value = MagicMock(
            json=lambda: {"code": 999, "msg": "permission denied"}
        )
        from app.feishu import list_bot_chats
        with pytest.raises(RuntimeError, match="Feishu list chats failed"):
            list_bot_chats("app_id", "app_secret")


class TestFeishuSendText:
    @patch("app.feishu.httpx.post")
    def test_send_text_success(self, mock_post):
        from app.feishu import send_text

        def side_effect(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(
                    json=lambda: {"code": 0, "tenant_access_token": "token"}
                )
            elif "im/v1/messages" in url:
                return MagicMock(
                    json=lambda: {"code": 0, "data": {"message_id": "om_xxx"}}
                )
            return MagicMock(json=lambda: {})

        mock_post.side_effect = side_effect
        send_text("test", "app_id", "app_secret", "oc_chat_id")
        assert mock_post.call_count == 2

    @patch("app.feishu.httpx.post")
    def test_send_text_failure(self, mock_post):
        from app.feishu import send_text

        def side_effect(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(
                    json=lambda: {"code": 0, "tenant_access_token": "token"}
                )
            elif "im/v1/messages" in url:
                return MagicMock(
                    json=lambda: {"code": 999, "msg": "permission denied"}
                )
            return MagicMock(json=lambda: {})

        mock_post.side_effect = side_effect
        with pytest.raises(RuntimeError, match="Feishu send message failed"):
            send_text("test", "app_id", "app_secret", "oc_chat_id")

    @patch("app.feishu.httpx.post")
    def test_content_is_json_string(self, mock_post):
        """Verify the content field is a JSON string, not a dict."""
        from app.feishu import send_text

        captured = {}

        def side_effect(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(
                    json=lambda: {"code": 0, "tenant_access_token": "token"}
                )
            elif "im/v1/messages" in url:
                captured["body"] = kwargs.get("json", {})
                return MagicMock(
                    json=lambda: {"code": 0, "data": {"message_id": "om_xxx"}}
                )
            return MagicMock(json=lambda: {})

        mock_post.side_effect = side_effect
        send_text("\u6d4b\u8bd5\u6587\u5b57", "app_id", "app_secret", "oc_chat_id")

        body = captured.get("body", {})
        assert "content" in body
        assert isinstance(body["content"], str), "content must be a JSON string"
        import json
        parsed = json.loads(body["content"])
        assert parsed["text"] == "\u6d4b\u8bd5\u6587\u5b57"


class TestUploadFile:
    @patch("app.feishu.httpx.post")
    def test_upload_success(self, mock_post):
        from app.feishu import upload_file
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "test.docx"
        tmp_path.write_bytes(b"test word content")

        def side_effect(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(json=lambda: {"code": 0, "tenant_access_token": "token"})
            elif "im/v1/files" in url:
                return MagicMock(status_code=200, json=lambda: {"code": 0, "data": {"file_key": "file_key_abc"}})
            return MagicMock(json=lambda: {})

        mock_post.side_effect = side_effect
        file_key = upload_file(tmp_path, "id", "secret")
        assert file_key == "file_key_abc"
        shutil.rmtree(tmp_dir)

    def test_file_not_found(self):
        from app.feishu import upload_file
        with pytest.raises(FileNotFoundError):
            upload_file(Path("/nonexistent/file.docx"), "id", "secret")

    def test_empty_file(self):
        from app.feishu import upload_file
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "empty.docx"
        tmp_path.write_bytes(b"")
        with pytest.raises(ValueError, match="\u6587\u4ef6\u4e3a\u7a7a"):
            upload_file(tmp_path, "id", "secret")
        shutil.rmtree(tmp_dir)

    def test_file_too_large(self):
        from app.feishu import upload_file
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "big.docx"
        tmp_path.write_bytes(b"x" * (31 * 1024 * 1024))
        with pytest.raises(ValueError, match="30MB"):
            upload_file(tmp_path, "id", "secret")
        shutil.rmtree(tmp_dir)

    @patch("app.feishu.httpx.post")
    def test_upload_multipart_params(self, mock_post):
        from app.feishu import upload_file
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "test_upload.docx"
        tmp_path.write_bytes(b"test content")
        captured = {}

        def side_effect(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(json=lambda: {"code": 0, "tenant_access_token": "token"})
            elif "im/v1/files" in url:
                captured["files"] = kwargs.get("files", {})
                return MagicMock(status_code=200, json=lambda: {"code": 0, "data": {"file_key": "k1"}})
            return MagicMock(json=lambda: {})

        mock_post.side_effect = side_effect
        file_key = upload_file(tmp_path, "id", "secret")
        assert file_key == "k1"
        files = captured["files"]
        assert files.get("file_type") == (None, "stream")
        assert files.get("file_name") == (None, "test_upload.docx")
        assert files.get("file")[0] == "test_upload.docx"
        shutil.rmtree(tmp_dir)

    @patch("app.feishu.httpx.post")
    def test_upload_retry_on_401(self, mock_post):
        from app.feishu import upload_file
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "retry.docx"
        tmp_path.write_bytes(b"test")
        call_log = []

        def side_effect(url, *args, **kwargs):
            call_log.append(url)
            if "auth/v3" in url:
                return MagicMock(json=lambda: {"code": 0, "tenant_access_token": f"token_{len([u for u in call_log if "auth/v3" in u])}"})
            elif "im/v1/files" in url:
                auth_calls = len([u for u in call_log if "auth/v3" in u])
                if auth_calls == 1:
                    resp = MagicMock(status_code=401)
                    resp.json = lambda: {"code": 99991663, "msg": "token expired"}
                    return resp
                return MagicMock(status_code=200, json=lambda: {"code": 0, "data": {"file_key": "retry_key"}})
            return MagicMock(json=lambda: {})

        mock_post.side_effect = side_effect
        file_key = upload_file(tmp_path, "id", "secret")
        assert file_key == "retry_key"
        assert len(call_log) == 4  # auth1, upload1(fail), auth2, upload2(success)
        shutil.rmtree(tmp_dir)


class TestSendDocument:
    @patch("app.feishu.send_text")
    @patch("app.feishu.upload_file")
    @patch("app.feishu.httpx.post")
    def test_send_document_with_caption(self, mock_post, mock_upload, mock_text):
        from app.feishu import send_document
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "test.docx"
        tmp_path.write_bytes(b"test")
        mock_upload.return_value = "file_key_test"

        def auth_side(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(json=lambda: {"code": 0, "tenant_access_token": "token"})
            return MagicMock(json=lambda: {"code": 0, "data": {}})
        mock_post.side_effect = auth_side

        send_document(tmp_path, "id", "secret", "chat_id", caption="\u6d4b\u8bd5\u8bf4\u660e")
        mock_text.assert_called_once_with("\u6d4b\u8bd5\u8bf4\u660e", "id", "secret", "chat_id")
        mock_upload.assert_called_once()
        shutil.rmtree(tmp_dir)

    @patch("app.feishu.send_text")
    @patch("app.feishu.upload_file")
    @patch("app.feishu.httpx.post")
    def test_send_document_no_caption(self, mock_post, mock_upload, mock_text):
        from app.feishu import send_document
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "test.docx"
        tmp_path.write_bytes(b"test")
        mock_upload.return_value = "fk1"

        def auth_side(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(json=lambda: {"code": 0, "tenant_access_token": "token"})
            return MagicMock(json=lambda: {"code": 0, "data": {}})
        mock_post.side_effect = auth_side

        send_document(tmp_path, "id", "secret", "chat_id")
        mock_text.assert_not_called()
        shutil.rmtree(tmp_dir)

    @patch("app.feishu.send_text")
    @patch("app.feishu.upload_file")
    @patch("app.feishu.httpx.post")
    def test_send_document_content_is_json(self, mock_post, mock_upload, mock_text):
        from app.feishu import send_document
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "test.docx"
        tmp_path.write_bytes(b"test")
        mock_upload.return_value = "fk_json"
        captured = {}

        def side_effect(url, *args, **kwargs):
            if "auth/v3" in url:
                return MagicMock(json=lambda: {"code": 0, "tenant_access_token": "token"})
            elif "im/v1/messages" in url:
                captured["body"] = kwargs.get("json", {})
                return MagicMock(json=lambda: {"code": 0, "data": {}})
            return MagicMock(json=lambda: {})

        mock_post.side_effect = side_effect
        send_document(tmp_path, "id", "secret", "oc_chat", caption="cap")

        body = captured["body"]
        assert body["msg_type"] == "file"
        assert body["receive_id"] == "oc_chat"
        assert isinstance(body["content"], str)
        parsed = json.loads(body["content"])
        assert parsed["file_key"] == "fk_json"
        shutil.rmtree(tmp_dir)

    @patch("app.feishu.send_text")
    @patch("app.feishu.upload_file")
    def test_upload_failure_stops_send(self, mock_upload, mock_text):
        from app.feishu import send_document
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp()
        tmp_path = Path(tmp_dir) / "test.docx"
        tmp_path.write_bytes(b"test")
        mock_upload.side_effect = RuntimeError("upload failed")

        with pytest.raises(RuntimeError, match="upload failed"):
            send_document(tmp_path, "id", "secret", "chat_id", caption="cap")
        shutil.rmtree(tmp_dir)
