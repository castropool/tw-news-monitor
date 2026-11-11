import logging

from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn"


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """Obtain a tenant_access_token from Feishu."""
    resp = httpx.post(
        f"{FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    data = resp.json()
    code = data.get("code", -1)
    if code != 0:
        msg = data.get("msg", "unknown error")
        raise RuntimeError(f"Feishu auth failed: code={code}, msg={msg}")
    return data["tenant_access_token"]


def list_bot_chats(app_id: str, app_secret: str) -> list[dict]:
    """List all group chats the current bot has joined (with pagination).

    Returns a list of dicts, each containing at least ``name`` and
    ``chat_id`` keys.
    """
    token = get_tenant_access_token(app_id, app_secret)

    chats: list[dict] = []
    page_token: str | None = None

    while True:
        params: dict = {"page_size": 50}
        if page_token:
            params["page_token"] = page_token

        resp = httpx.get(
            f"{FEISHU_BASE}/open-apis/im/v1/chats",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10,
        )
        data = resp.json()
        code = data.get("code", -1)
        if code != 0:
            msg = data.get("msg", "unknown error")
            raise RuntimeError(
                f"Feishu list chats failed: code={code}, msg={msg}"
            )

        items = data.get("data", {}).get("items", [])
        chats.extend(items)

        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token")

    return chats


def send_text(
    text: str, app_id: str, app_secret: str, chat_id: str,
) -> None:
    """Send a plain-text message to a Feishu group chat as the bot."""
    import json as _json
    token = get_tenant_access_token(app_id, app_secret)
    content = _json.dumps({"text": text}, ensure_ascii=False)
    resp = httpx.post(
        f"{FEISHU_BASE}/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": content,
        },
        timeout=10,
    )
    data = resp.json()
    code = data.get("code", -1)
    if code != 0:
        msg = data.get("msg", "unknown error")
        raise RuntimeError(
            f"Feishu send message failed: code={code}, msg={msg}"
        )


def upload_file(file_path: Path, app_id: str, app_secret: str) -> str:
    """Upload a .docx file to Feishu and return the file_key.

    Validates file existence, non-empty, size < 30 MB.
    Retries once if the token has expired.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"\u6587\u4ef6\u4e0d\u5b58\u5728: {file_path}")

    file_size = file_path.stat().st_size
    if file_size == 0:
        raise ValueError(f"\u6587\u4ef6\u4e3a\u7a7a\uff0c\u62d2\u7edd\u4e0a\u4f20: {file_path.name}")

    MAX_SIZE = 30 * 1024 * 1024
    if file_size > MAX_SIZE:
        size_mb = file_size / 1024 / 1024
        raise ValueError(
            f"\u6587\u4ef6\u8d85\u8fc730MB\u9650\u5236\uff08\u5b9e\u9645 {size_mb:.1f} MB\uff09: {file_path.name}"
        )

    token = get_tenant_access_token(app_id, app_secret)

    def _do_upload(bearer_token: str) -> tuple:
        file_bytes = file_path.read_bytes()
        resp = httpx.post(
            f"{FEISHU_BASE}/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {bearer_token}"},
            files={
                "file_type": (None, "stream"),
                "file_name": (None, file_path.name),
                "file": (file_path.name, file_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            },
            timeout=60,
        )
        return resp.status_code, resp.json()

    status_code, data = _do_upload(token)

    if status_code == 401 or data.get("code") == 99991663:
        logger.info("Feishu token expired, refreshing and retrying upload")
        token = get_tenant_access_token(app_id, app_secret)
        status_code, data = _do_upload(token)

    code = data.get("code", -1)
    msg = data.get("msg", "unknown error")
    if status_code != 200 or code != 0:
        if status_code != 200 and code == -1:
            msg = f"HTTP {status_code}"
        raise RuntimeError(f"\u98de\u4e66\u4e0a\u4f20\u6587\u4ef6\u5931\u8d25: {msg}")

    file_key = data["data"]["file_key"]
    logger.info("File uploaded successfully: file_key=%s", file_key)
    return file_key


def send_document(
    file_path: Path,
    app_id: str,
    app_secret: str,
    chat_id: str,
    caption: str | None = None,
) -> None:
    """Upload a .docx file and send it as a file message to a Feishu chat."""
    import json as _json

    if caption:
        send_text(caption, app_id, app_secret, chat_id)

    file_key = upload_file(file_path, app_id, app_secret)
    content = _json.dumps({"file_key": file_key}, ensure_ascii=False)

    token = get_tenant_access_token(app_id, app_secret)
    resp = httpx.post(
        f"{FEISHU_BASE}/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "receive_id": chat_id,
            "msg_type": "file",
            "content": content,
        },
        timeout=30,
    )
    data = resp.json()
    code = data.get("code", -1)
    if code != 0:
        msg = data.get("msg", "unknown error")
        raise RuntimeError(
            f"\u98de\u4e66\u53d1\u9001\u6587\u4ef6\u6d88\u606f\u5931\u8d25: code={code}, msg={msg}"
        )

    logger.info("File message sent successfully: file_key=%s", file_key)


def build_highlight_card(
    highlights: list,
    title: str = "本期重点新闻提示",
    show_summary: bool = False,
    show_source: bool = False,
    show_published_at: bool = False,
) -> dict:
    """Build a Feishu interactive card for important news highlights.

    Args:
        highlights: list of (article, ImportanceResult) tuples sorted by
                    select_highlights() (critical first, then by score).
        title: card header title.
        show_summary: include the "重大X条、重点Y条" summary line.
        show_source: append source name under each item.
        show_published_at: append publish time under each item.

    Returns:
        Card dict suitable for send_card().
    """
    if not highlights:
        raise ValueError("No highlights to build card")

    critical_count = sum(1 for _, r in highlights if r.level == "critical")
    important_count = sum(1 for _, r in highlights if r.level == "important")
    has_critical = critical_count > 0
    header_template = "red" if has_critical else "orange"

    item_lines = []
    for article, result in highlights:
        pfx = "【重大】" if result.level == "critical" else "【重点】"
        safe_title = article.title.replace("\n", " ").replace("\r", "")
        line = pfx + safe_title
        if show_source and getattr(article, "source_name", ""):
            line += f"\n来源：{article.source_name}"
        if show_published_at and getattr(article, "published_at", None):
            line += f"\n时间：{article.published_at.strftime('%Y-%m-%d %H:%M')}"
        item_lines.append(line)

    content_md = "\n".join(item_lines)

    summary_parts = []
    if show_summary:
        if critical_count > 0:
            summary_parts.append(f"重大{critical_count}条")
        if important_count > 0:
            summary_parts.append(f"重点{important_count}条")
    summary_line = ("、".join(summary_parts) + "，" if summary_parts else "")
    summary_line += "详情见本期Word简报。"

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": header_template,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content_md},
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": summary_line}
                ],
            },
        ],
    }
    return card


def send_card(
    card: dict,
    app_id: str,
    app_secret: str,
    chat_id: str,
) -> None:
    import json as _json
    token = get_tenant_access_token(app_id, app_secret)
    content = _json.dumps(card, ensure_ascii=False)
    resp = httpx.post(
        f"{FEISHU_BASE}/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": content,
        },
        timeout=15,
    )
    data = resp.json()
    code = data.get("code", -1)
    if code != 0:
        msg = data.get("msg", "unknown error")
        raise RuntimeError(f"飞书发送卡片失败: code={code}, msg={msg}")
    logger.info("Card message sent successfully")

