"""Tests for the local Command Code gateway (OpenAI-compatible shim).

The gateway spawns the real CLI per request; tests stub ``run_headless`` so
the HTTP contract is verified without a model call.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import pytest

import app.cmd_gateway as gateway


@pytest.fixture()
def server(monkeypatch):
    """A gateway server bound to an ephemeral port with a stubbed CLI."""
    monkeypatch.setattr(gateway, "run_headless", _fake_headless)
    srv = gateway.make_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def _fake_headless(prompt: str, model: str | None = None, timeout: float = 300.0) -> str:
    assert model == "deepseek/deepseek-v4-flash"
    assert "使用者資料" in prompt
    return '{"https://x.test/1": "這是摘要。"}'


def test_health(server):
    r = httpx.get(f"{server}/health", timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == gateway.SERVICE_NAME
    assert r.json()["model"] == "deepseek/deepseek-v4-flash"


def test_non_loopback_bind_requires_explicit_guard():
    with pytest.raises(ValueError, match="loopback"):
        gateway.make_server(host="0.0.0.0", port=0)


def test_request_body_limit_returns_413(monkeypatch):
    monkeypatch.setattr(gateway, "run_headless", _fake_headless)
    srv = gateway.make_server(host="127.0.0.1", port=0, max_body_bytes=1024)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        body = {"messages": [{"role": "user", "content": "x" * 1500}]}
        response = httpx.post(
            f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions",
            json=body,
            timeout=5,
        )
        assert response.status_code == 413
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli_concurrency_limit_returns_429(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking(prompt, model=None, timeout=300.0):
        started.set()
        release.wait(5)
        return "ok"

    monkeypatch.setattr(gateway, "run_headless", blocking)
    srv = gateway.make_server(host="127.0.0.1", port=0, max_concurrency=1)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
    body = {"messages": [{"role": "user", "content": "使用者資料"}]}
    first_result = {}

    def first_request():
        first_result["response"] = httpx.post(url, json=body, timeout=10)

    first = threading.Thread(target=first_request)
    first.start()
    try:
        assert started.wait(3)
        second = httpx.post(url, json=body, timeout=5)
        assert second.status_code == 429
    finally:
        release.set()
        first.join(10)
        srv.shutdown()
        srv.server_close()
    assert first_result["response"].status_code == 200


def test_chat_completion_contract(server):
    body = {
        "model": "deepseek/deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "系統提示"},
            {"role": "user", "content": "使用者資料"},
        ],
        "temperature": 0.3,
    }
    r = httpx.post(f"{server}/v1/chat/completions", json=body, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "deepseek/deepseek-v4-flash"
    content = data["choices"][0]["message"]["content"]
    assert '"https://x.test/1"' in content
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "usage" in data


def test_model_defaults_to_server_model(server):
    body = {
        "messages": [
            {"role": "user", "content": "使用者資料"},
        ],
    }
    r = httpx.post(f"{server}/v1/chat/completions", json=body, timeout=10)
    assert r.status_code == 200
    assert r.json()["model"] == "deepseek/deepseek-v4-flash"


def test_missing_user_message_rejected(server):
    body = {"messages": [{"role": "system", "content": "x"}]}
    r = httpx.post(f"{server}/v1/chat/completions", json=body, timeout=5)
    assert r.status_code == 400
    assert "user" in r.json()["error"]["message"]


def test_bad_json_rejected(server):
    r = httpx.post(
        f"{server}/v1/chat/completions",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert r.status_code == 400


def test_unknown_route_404(server):
    r = httpx.get(f"{server}/nope", timeout=5)
    assert r.status_code == 404


def test_cli_failure_maps_to_502(server, monkeypatch):
    def boom(prompt, model=None, timeout=300.0):
        raise RuntimeError("cmd headless exited 1: boom")

    monkeypatch.setattr(gateway, "run_headless", boom)
    body = {"messages": [{"role": "user", "content": "hi"}]}
    r = httpx.post(f"{server}/v1/chat/completions", json=body, timeout=5)
    assert r.status_code == 502


def test_run_headless_argv_shape():
    """run_headless builds a CLI argv with -p prompt and --model."""
    seen = {}

    class FakeProc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    import subprocess

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return FakeProc()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        out = gateway.run_headless("hello", model="deepseek/deepseek-v4-flash", timeout=30)
    finally:
        monkeypatch.undo()
    assert out == "ok"
    argv = seen["argv"]
    assert "-p" in argv
    assert "hello" in argv[argv.index("-p") + 1]
    assert "--model" in argv
    assert "deepseek/deepseek-v4-flash" in argv[argv.index("--model") + 1]
