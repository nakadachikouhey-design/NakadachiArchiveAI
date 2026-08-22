from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import kio_node_agent as node
import storage_search

SERVER_NAME = "kio-executive-agent"
SERVER_VERSION = "0.4.0"
STATE_DIR = Path(os.path.expanduser("~/NakadachiArchiveAI/agent_state"))

READ_TOOLS = {
    "repo_status": {
        "description": "Read the local NakadachiArchiveAI git working-tree status.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    "heartbeat": {
        "description": "Read the latest KIO Mac mini local-node heartbeat.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    "engineering_loop_status": {
        "description": "Read the persisted KIO Engineering Loop state and handled workflow runs.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    "pr_monitor_status": {
        "description": "Read the latest monitored GitHub pull-request state collected by the local node.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    "file_search": {
        "description": (
            "Search files and folders across configured archive roots, currently mounted /Volumes disks, "
            "and macOS CloudStorage. Use this for locating real local material when archive-index search is "
            "missing or a mount/path spelling is uncertain. Read-only; no arbitrary shell commands are run."
        ),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
}

WRITE_TOOLS = {
    "github_sync": "Fetch and fast-forward the local NakadachiArchiveAI checkout when the worktree is clean.",
    "knowledge_update": "Run the allowlisted Knowledge Engine update.",
    "archive_update": "Run the allowlisted archive update.",
    "assistant_build": "Run the allowlisted Assistant Pack build.",
    "full_update": "Run the allowlisted Archive → Knowledge Engine → Assistant Pack full update.",
    "engineering_loop": "Run one KIO Engineering Loop cycle using the configured monitored repositories.",
}


def _json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _read_tool_schema(name: str) -> dict[str, Any]:
    if name == "file_search":
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                "include_files": {"type": "boolean", "default": True},
                "include_directories": {"type": "boolean", "default": True},
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional extension filter such as ['mov','mp4','m4v'].",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }
    return {"type": "object", "properties": {}, "additionalProperties": False}


def tool_definitions() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for name, metadata in READ_TOOLS.items():
        tools.append(
            {
                "name": name,
                "title": name.replace("_", " ").title(),
                "description": metadata["description"],
                "inputSchema": _read_tool_schema(name),
                "annotations": metadata["annotations"],
            }
        )
    for name, description in WRITE_TOOLS.items():
        tools.append(
            {
                "name": name,
                "title": name.replace("_", " ").title(),
                "description": description,
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": name in {"github_sync", "knowledge_update", "archive_update", "assistant_build", "full_update"},
                },
            }
        )
    return tools


def _tool_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    if isinstance(value, dict):
        result["structuredContent"] = value
    return result


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    arguments = arguments or {}

    if name == "file_search":
        allowed = {"query", "max_results", "include_files", "include_directories", "extensions"}
        extras = sorted(set(arguments) - allowed)
        if extras:
            return _tool_result(
                {"status": "rejected", "message": f"Unsupported arguments: {', '.join(extras)}"},
                is_error=True,
            )
        query = str(arguments.get("query") or "").strip()
        if not query:
            return _tool_result({"status": "rejected", "message": "query is required"}, is_error=True)
        result = storage_search.search_local_storage(
            query,
            max_results=int(arguments.get("max_results", 100)),
            include_files=bool(arguments.get("include_files", True)),
            include_directories=bool(arguments.get("include_directories", True)),
            extensions=arguments.get("extensions"),
        )
        return _tool_result(result, is_error=result.get("status") == "rejected")

    if arguments:
        return _tool_result({"status": "rejected", "message": "This tool accepts no arguments."}, is_error=True)

    if name == "repo_status":
        return _tool_result(node.repo_status())
    if name == "heartbeat":
        return _tool_result(_json_file(STATE_DIR / "heartbeat.json", {"status": "not_started"}))
    if name == "engineering_loop_status":
        return _tool_result(_json_file(STATE_DIR / "engineering_loop.json", {"status": "not_started"}))
    if name == "pr_monitor_status":
        return _tool_result(_json_file(STATE_DIR / "pr_monitor.json", {"status": "not_started"}))
    if name in WRITE_TOOLS:
        result = node.execute_action(name)
        failed = result.get("status") not in {
            "ok",
            "baseline_created",
            "partial",
            "rejected",
            "skipped_dirty_worktree",
        }
        return _tool_result(result, is_error=failed)

    raise KeyError(name)


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method") or "")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        requested_version = str(params.get("protocolVersion") or "2025-06-18")
        return _result(
            request_id,
            {
                "protocolVersion": requested_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "KIO Executive Agent exposes fixed local-node operations plus a read-only local file/folder search. "
                    "The search auto-discovers mounted storage so mount-name spelling does not need to be guessed. "
                    "It never accepts arbitrary shell commands and does not auto-merge pull requests."
                ),
            },
        )

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": tool_definitions()})

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "Tool arguments must be an object.")
        try:
            return _result(request_id, call_tool(name, arguments))
        except KeyError:
            return _error(request_id, -32602, f"Unknown KIO tool: {name}")
        except Exception as exc:
            print(f"KIO MCP tool error ({name}): {exc}", file=sys.stderr, flush=True)
            return _result(request_id, _tool_result({"status": "failed", "error": str(exc)}, is_error=True))

    if request_id is None:
        return None
    return _error(request_id, -32601, f"Method not found: {method}")


def serve() -> int:
    # MCP stdio transport: one JSON-RPC object per line. stdout is reserved
    # exclusively for protocol messages; diagnostics go to stderr.
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = handle_message(message)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, "Parse error", str(exc))
        except Exception as exc:
            response = _error(None, -32600, "Invalid Request", str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
