#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import assistant_ai
import kio_executive_agent as agent
import search_archive


SERVER_INFO = {"name": "kio-executive-agent", "version": "0.2.0"}


TOOLS = [
    {
        "name": "kio_create_case",
        "description": "Knowledge Archiveを検索し、KPSの非公開台帳に案件・期限・未検証の根拠候補を登録します。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type": "string"},
                "project": {"type": "string", "default": ""},
                "project_id": {"type": "string", "default": "PRJ-001"},
                "due": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
                "execute_safe": {"type": "boolean", "default": True},
            },
            "required": ["request"],
            "additionalProperties": False,
        },
        "annotations": {"title": "KIO案件を作成", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "kio_case_status",
        "description": "KPS非公開台帳の案件、または期限順の未完了案件一覧を取得します。",
        "inputSchema": {
            "type": "object",
            "properties": {"case_id": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"title": "KIO案件状態", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "kio_verify_evidence",
        "description": "原本確認済みの根拠候補だけを事実へ昇格します。検索結果だけでは検証済みにしません。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "evidence_id": {"type": "string"},
                "fact": {"type": "string", "description": "確認した原本が直接支持する事実"},
                "reason": {"type": "string"},
                "verifier": {"type": "string"},
                "sha256": {"type": "string"},
            },
            "required": ["case_id", "evidence_id", "fact", "reason", "verifier"],
            "additionalProperties": False,
        },
        "annotations": {"title": "根拠を検証", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "kio_record_decision",
        "description": "判断、理由、状態、再評価日を案件のDecisionとして記録します。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "decision": {"type": "string"},
                "reason": {"type": "string"},
                "status": {"type": "string", "enum": sorted(agent.DECISION_STATUSES), "default": "accepted"},
                "review_date": {"type": "string"},
            },
            "required": ["case_id", "decision", "reason"],
            "additionalProperties": False,
        },
        "annotations": {"title": "KIO判断を記録", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "kio_run_safe_action",
        "description": "明示されたallowlist内のローカル作業だけを実行し、結果を案件へ記録します。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "action": {"type": "string", "enum": sorted(agent.SAFE_ACTIONS)},
            },
            "required": ["case_id", "action"],
            "additionalProperties": False,
        },
        "annotations": {"title": "安全な作業を実行", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "kio_record_action_result",
        "description": "ChatGPTの接続機能または担当者が実行した作業結果をCaseへ記録します。対外効果がある場合はAccepted Decision IDが必須です。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "action_type": {"type": "string"},
                "status": {"type": "string", "enum": ["completed", "failed", "blocked"]},
                "result": {"type": "string"},
                "external_effect": {"type": "boolean", "default": False},
                "decision_id": {"type": "string"},
            },
            "required": ["case_id", "action_type", "status", "result"],
            "additionalProperties": False,
        },
        "annotations": {"title": "作業結果を記録", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "kio_complete_case",
        "description": "検証済み根拠があり、失敗・停止中Actionがない案件を完了します。",
        "inputSchema": {
            "type": "object",
            "properties": {"case_id": {"type": "string"}, "outcome": {"type": "string"}},
            "required": ["case_id", "outcome"],
            "additionalProperties": False,
        },
        "annotations": {"title": "KIO案件を完了", "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
]


class KioMcpServer:
    def __init__(self) -> None:
        self.db = os.environ.get("KIO_ARCHIVE_DB", search_archive.DEFAULT_DB)
        self.profiles = os.environ.get("KIO_PROJECT_PROFILES", assistant_ai.DEFAULT_PROFILES)
        self.kps_root = Path(os.environ.get("KPS_ROOT", agent.DEFAULT_KPS_ROOT)).expanduser().resolve()
        self.state_dir = agent.resolve_state_dir(os.environ.get("KIO_STATE_DIR", ""), self.kps_root)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                requested = (message.get("params") or {}).get("protocolVersion", "2025-06-18")
                return response(request_id, {
                    "protocolVersion": requested,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "検索結果は未検証候補です。原本確認後だけ検証済み事実に昇格し、対外・不可逆作業は最終判断として提示してください。",
                })
            if method == "ping":
                return response(request_id, {})
            if method == "tools/list":
                return response(request_id, {"tools": TOOLS})
            if method == "tools/call":
                params = message.get("params") or {}
                result = self.call_tool(str(params.get("name", "")), params.get("arguments") or {})
                return response(request_id, tool_result(result))
            return error_response(request_id, -32601, f"Method not found: {method}")
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            return error_response(request_id, -32000, str(exc))

    def call_tool(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        if name == "kio_create_case":
            args = argparse.Namespace(
                request=values["request"],
                project=values.get("project", ""),
                project_id=values.get("project_id", "PRJ-001"),
                due=values.get("due", ""),
                limit=min(50, max(1, int(values.get("limit", 12)))),
                execute_safe=bool(values.get("execute_safe", True)),
                db=self.db,
                profiles=self.profiles,
            )
            return agent.create_case(args, self.state_dir, self.kps_root)
        if name == "kio_case_status":
            return agent.case_status(self.state_dir, values.get("case_id"))
        if name == "kio_verify_evidence":
            return agent.verify_evidence(
                self.state_dir, values["case_id"], values["evidence_id"], values["fact"], values["reason"], values["verifier"], values.get("sha256", "")
            )
        if name == "kio_record_decision":
            args = argparse.Namespace(
                case_id=values["case_id"], decision=values["decision"], reason=values["reason"],
                status=values.get("status", "accepted"), review_date=values.get("review_date", ""),
            )
            return agent.record_decision(self.state_dir, args)
        if name == "kio_run_safe_action":
            return agent.run_case_action(self.state_dir, values["case_id"], values["action"], self.kps_root)
        if name == "kio_record_action_result":
            return agent.record_action_result(
                self.state_dir, values["case_id"], values["action_type"], values["status"], values["result"],
                bool(values.get("external_effect", False)), values.get("decision_id", ""),
            )
        if name == "kio_complete_case":
            return agent.complete_case(self.state_dir, values["case_id"], values["outcome"])
        return {"ok": False, "error": f"Unknown tool: {name}"}


def response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def tool_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
        "structuredContent": result,
        "isError": not bool(result.get("ok", True)),
    }


def serve(stdin: Any = sys.stdin, stdout: Any = sys.stdout) -> int:
    server = KioMcpServer()
    for raw_line in stdin:
        if not raw_line.strip():
            continue
        try:
            message = json.loads(raw_line)
            reply = server.handle(message)
        except json.JSONDecodeError as exc:
            reply = error_response(None, -32700, f"Parse error: {exc}")
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
