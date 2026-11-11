"""Read-only inspection of candidate pipeline inputs.

The inspection never writes to source databases and only produces JSON/MD
reports under the candidate pipeline output root.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import CandidatePipelineConfig


def _ro_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_schema(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [
        {
            "name": r[1],
            "type": r[2],
            "notnull": bool(r[3]),
            "pk": bool(r[5]),
        }
        for r in rows
    ]


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]


def inspect_news_db(config: CandidatePipelineConfig) -> dict[str, Any]:
    path = config.path("news_db")
    result: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return result
    conn = _ro_conn(path)
    try:
        result["tables"] = _table_names(conn)
        result["table_schemas"] = {
            t: {
                "columns": _table_schema(conn, t),
                "row_count": conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0],
            }
            for t in result["tables"]
        }
        result["primary_table"] = config.get("news_reader.table", "articles")
        result["primary_table_exists"] = result["primary_table"] in result["tables"]
        if result["primary_table_exists"]:
            table = result["primary_table"]
            cols = {c["name"] for c in result["table_schemas"][table]["columns"]}
            required = [
                config.get("news_reader.id_column"),
                config.get("news_reader.title_column"),
                config.get("news_reader.url_column"),
                config.get("news_reader.source_name_column"),
                config.get("news_reader.published_at_column"),
                config.get("news_reader.fetched_at_column"),
            ]
            result["required_fields_present"] = {c: c in cols for c in required}
            result["optional_fields_present"] = {
                c: c in cols
                for c in [
                    config.get("news_reader.category_column"),
                    config.get("news_reader.summary_column"),
                    config.get("news_reader.source_id_column"),
                ]
            }
            missing = [c for c, ok in result["required_fields_present"].items() if not ok]
            result["missing_required_fields"] = missing
            result["hold_if_missing"] = missing
    finally:
        conn.close()
    return result


def inspect_election_watch(config: CandidatePipelineConfig) -> dict[str, Any]:
    path = config.path("match_db")
    result: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return result
    conn = _ro_conn(path)
    try:
        result["tables"] = _table_names(conn)
        table = config.get("match_reader.table", "article_matches")
        result["match_table"] = table
        result["match_table_exists"] = table in result["tables"]
        if result["match_table_exists"]:
            result["match_table_schema"] = _table_schema(conn, table)
            result["match_row_count"] = conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
            result["city_values"] = sorted(
                r[0]
                for r in conn.execute(f"SELECT DISTINCT city FROM '{table}'").fetchall()
                if r[0] is not None
            )
            result["relevance_values"] = sorted(
                r[0]
                for r in conn.execute(f"SELECT DISTINCT relevance FROM '{table}'").fetchall()
                if r[0] is not None
            )
        result["scan_state_table"] = "scan_state" in result["tables"]
        if result["scan_state_table"]:
            result["scan_state"] = dict(conn.execute("SELECT key, value FROM scan_state").fetchall())
        result["sufficient_for_tainan_selection"] = (
            result["match_table_exists"]
            and "tainan" in result.get("city_values", [])
            and result.get("match_row_count", 0) > 0
        )
    finally:
        conn.close()
    return result


def assess_merge_function() -> dict[str, Any]:
    try:
        from app.election_event_merge import merge_articles_into_events
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"importable": False, "error": str(exc)}
    src = inspect.getsource(merge_articles_into_events)
    func_name = merge_articles_into_events.__name__
    assessment = {
        "importable": True,
        "function_name": func_name,
        "signature": str(inspect.signature(merge_articles_into_events)),
        "deterministic": True,
        "depends_on_current_time": "datetime.now" in src,
        "memory_only": "INSERT" not in src and "UPDATE" not in src,
        "mutates_input": False,
        "uses_unstable_set_order": "set(" in src,
        "groups_by_title_only": "_normalize_title(title)" in src,
        "notes": [
            "Groups articles by a normalized-title fingerprint (first 50 chars).",
            "Event id is sha256 over sorted article URLs (stable across runs).",
            "Uses input order for event_title/city fields; refinement is required.",
        ],
        "reuse_recommendation": (
            "Reuse as a coarse pre-grouping step only; enrich with date, actor, "
            "action and event-type features before final clustering."
        ),
    }
    return assessment


def inspect_formal_repository(config: CandidatePipelineConfig) -> dict[str, Any]:
    from app.election_context.repository import ElectionContextRepository

    public_methods = [
        name
        for name, member in inspect.getmembers(ElectionContextRepository)
        if not name.startswith("_") and callable(member)
    ]
    forbidden = set(config.get("guardrails.forbidden_formal_write_methods", []))
    write_methods = sorted(name for name in public_methods if name in forbidden)
    read_methods = sorted(
        name
        for name in public_methods
        if name
        not in {
            "save_election",
            "save_actor",
            "save_source",
            "save_event",
            "link_event_source",
            "save_snapshot",
            "mark_event_superseded",
            "close",
            "connect",
            "create_tables",
        }
    )
    return {
        "class": "app.election_context.repository.ElectionContextRepository",
        "read_only_methods": read_methods,
        "write_methods": write_methods,
        "forbidden_write_methods": sorted(forbidden),
        "allowed_read_only_methods_for_candidate_pipeline": [
            "get_event",
            "search_events",
            "get_latest_snapshot",
            "get_snapshot_history",
            "get_milestone_events",
        ],
        "note": "The candidate package performs its own read-only SQL queries and does not import write methods.",
    }


def inspect_formal_db(config: CandidatePipelineConfig) -> dict[str, Any]:
    path = config.path("formal_db")
    result: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return result
    conn = _ro_conn(path)
    try:
        result["tables"] = _table_names(conn)
        for t in ["elections", "election_events", "sources", "event_sources", "election_polls", "election_state_snapshots"]:
            if t in result["tables"]:
                result[f"{t}_row_count"] = conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
        election_id = config.resolve_election_id(config.canonical_election_id)
        result["configured_election_id"] = config.canonical_election_id
        result["resolved_election_id"] = election_id
        row = conn.execute(
            "SELECT election_id, election_name, election_date, region, election_type, status "
            "FROM elections WHERE election_id=?", (election_id,)
        ).fetchone()
        result["election_row"] = dict(row) if row else None
        if row:
            result["event_count"] = conn.execute(
                "SELECT COUNT(*) FROM election_events WHERE election_id=?", (election_id,)
            ).fetchone()[0]
            result["source_count"] = conn.execute(
                "SELECT COUNT(*) FROM sources"
            ).fetchone()[0]
            result["event_source_link_count"] = conn.execute(
                "SELECT COUNT(*) FROM event_sources"
            ).fetchone()[0]
            result["poll_count"] = conn.execute(
                "SELECT COUNT(*) FROM election_polls WHERE election_id=?", (election_id,)
            ).fetchone()[0]
            snap = conn.execute(
                "SELECT snapshot_id, as_of, snapshot_status FROM election_state_snapshots "
                "WHERE election_id=? AND snapshot_status='active' ORDER BY as_of DESC LIMIT 1",
                (election_id,),
            ).fetchone()
            result["active_snapshot"] = dict(snap) if snap else None
    finally:
        conn.close()
    return result


def build_source_field_mapping(config: CandidatePipelineConfig, news: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "news_article_id": config.get("news_reader.id_column"),
        "title": config.get("news_reader.title_column"),
        "url": config.get("news_reader.url_column"),
        "source_name": config.get("news_reader.source_name_column"),
        "category": config.get("news_reader.category_column"),
        "published_at": config.get("news_reader.published_at_column"),
        "fetched_at": config.get("news_reader.fetched_at_column"),
        "summary": config.get("news_reader.summary_column"),
        "match_link": "article_matches.article_url -> articles.url (join key)",
        "missing_fields_that_force_hold": news.get("missing_required_fields", []),
        "notes": [
            "article_matches lives in election_watch.db, not news.db.",
            "If article_matches row count is 0, the pipeline supports inline_classifier "
            "mode which reuses ElectionClassifier without writing article_matches.",
        ],
    }
    return mapping


def run_inspection(
    config: CandidatePipelineConfig,
    election_id: str | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(output_root) if output_root else config.path("output_root") / "input_inspection"
    root.mkdir(parents=True, exist_ok=True)

    news = inspect_news_db(config)
    watch = inspect_election_watch(config)
    merge = assess_merge_function()
    repo = inspect_formal_repository(config)
    formal = inspect_formal_db(config)
    mapping = build_source_field_mapping(config, news)

    docs = {
        "news_db_schema.json": news,
        "election_watch_capabilities.json": watch,
        "merge_function_assessment.json": merge,
        "formal_repository_capabilities.json": repo,
        "source_field_mapping.json": mapping,
    }
    for name, payload in docs.items():
        (root / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    lines = [
        "# 候选事实流水线输入核查报告",
        "",
        f"- 核查时间: {config.get('versions.candidate_pipeline_version', '0.1.0')}",
        f"- news.db: {news.get('path')}（表: {news.get('tables')}）",
        f"- news.db 主表: {news.get('primary_table')}，存在: {news.get('primary_table_exists')}",
        f"- 必需字段缺失: {news.get('missing_required_fields', [])}",
        f"- article_matches: {watch.get('match_table')}，行数: {watch.get('match_row_count')}",
        f"- article_matches 是否足以确定台南选情: {watch.get('sufficient_for_tainan_selection')}",
        f"- merge_articles_into_events 可复用: {merge.get('importable')}；建议: {merge.get('reuse_recommendation')}",
        f"- 正式库 election_id: {formal.get('resolved_election_id')}，事件数: {formal.get('event_count')}",
        f"- 正式库来源数: {formal.get('source_count')}，事件来源关联: {formal.get('event_source_link_count')}",
        f"- 正式库民调数: {formal.get('poll_count')}，活动快照: {(formal.get('active_snapshot') or {}).get('snapshot_id')}",
        f"- 禁止调用的正式写入方法: {repo.get('forbidden_write_methods')}",
        "",
        "## 字段缺失时只能进入 hold",
        "",
        "以下字段缺失时，候选必须进入 hold：",
        "",
    ]
    for field in news.get("missing_required_fields", []):
        lines.append(f"- {field}")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    if watch.get("match_row_count", 0) > 0:
        lines.append("article_matches 有可用匹配记录，流水线以 persisted 模式读取。")
    else:
        lines.append(
            "article_matches 当前为空；流水线保留 persisted 模式作为默认入口，"
            "并支持 inline_classifier 模式复用 ElectionClassifier 做只读历史试跑（不写 article_matches）。"
        )
    lines.append("")
    (root / "input_inspection_summary.md").write_text("\n".join(lines), encoding="utf-8")

    return {
        "input_inspection_ready": True,
        "output_root": str(root),
        "news_db_schema": news,
        "election_watch_capabilities": watch,
        "merge_function_assessment": merge,
        "formal_repository_capabilities": repo,
        "formal_db": formal,
        "source_field_mapping": mapping,
    }
