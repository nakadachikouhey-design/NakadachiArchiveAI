from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import kio_mcp_server as mcp


class KioMcpServerTests(unittest.TestCase):
    def test_initialize_echoes_client_protocol_version(self) -> None:
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        assert response is not None
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual(response["result"]["serverInfo"]["name"], "kio-executive-agent")

    def test_initialized_notification_has_no_response(self) -> None:
        self.assertIsNone(
            mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_tools_list_contains_read_and_allowlisted_write_tools(self) -> None:
        response = mcp.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert response is not None
        tools = {item["name"]: item for item in response["result"]["tools"]}
        self.assertIn("heartbeat", tools)
        self.assertIn("engineering_loop_status", tools)
        self.assertIn("github_sync", tools)
        self.assertIn("knowledge_update", tools)
        self.assertTrue(tools["heartbeat"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["github_sync"]["annotations"]["readOnlyHint"])
        self.assertNotIn("shell", tools)

    def test_unknown_tool_is_protocol_error(self) -> None:
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "arbitrary_shell", "arguments": {}},
            }
        )
        assert response is not None
        self.assertEqual(response["error"]["code"], -32602)

    def test_ping(self) -> None:
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 4, "method": "ping"})
        assert response is not None
        self.assertEqual(response["result"], {})


if __name__ == "__main__":
    unittest.main()
