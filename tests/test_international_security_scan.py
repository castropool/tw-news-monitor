from validation.international_media.security_scan import scan_paths, scan_text


def test_security_scan_rejects_bearer_secret_and_accepts_named_schema_field(tmp_path):
    (tmp_path / "bad.txt").write_text(
        "Authorization: Bearer " + "real-looking-secret", encoding="utf-8"
    )
    (tmp_path / "schema.json").write_text(
        '{"required": ["client_secret"]}', encoding="utf-8"
    )
    report = scan_paths([tmp_path], excludes=())
    assert report.status == "fail"
    assert any("authorization_bearer_value" in hit for hit in report.hits)


def test_security_scan_allows_empty_and_exact_fake_values():
    text = "FEISHU_APP_SECRET=\nFEISHU_WEBHOOK_URL=<operator-secret>\nclient_secret is a schema field"
    assert scan_text(text) == []


def test_security_scan_does_not_treat_vocabulary_as_a_hit(tmp_path):
    path = tmp_path / "schema.yaml"
    path.write_text("required: [password, refresh_token, client_secret]\n", encoding="utf-8")
    assert scan_paths([tmp_path]).status == "pass"
