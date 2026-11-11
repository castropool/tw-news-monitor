from pathlib import Path


def test_operator_guide_has_readonly_scope_label_and_no_secret_examples():
    text = Path("docs/INTERNATIONAL_NEWSLETTER_OPERATOR_GUIDE.md").read_text(encoding="utf-8")
    assert "InternationalNews" in text
    assert "readonly" in text
    assert "MAILBOX_AUTH_REQUIRED" in text
    assert "official_url_registered" in text
    assert "不表示页面已在线访问成功" in text
    assert ("FEISHU_APP_SECRET" + "=") not in text
    assert ("refresh_token" + "=") not in text
