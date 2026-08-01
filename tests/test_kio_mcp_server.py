from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import kio_mcp_server as mcp


class McpServerTests(unittest.TestCase):
    def test_initialize_and_tools_list(self) -> None:
        server = mcp.KioMcpServer()
        initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "test-version"}})
        self.assertEqual(initialized["result"]["protocolVersion"], "test-version")
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertIn("kio_create_case", names)
        self.assertIn("kio_verify_evidence", names)
        self.assertIn("kio_run_safe_action", names)
        self.assertIn("kio_record_action_result", names)

    def test_stdio_transport(self) -> None:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}) + "\n"
        output = StringIO()
        self.assertEqual(mcp.serve(StringIO(request), output), 0)
        self.assertEqual(json.loads(output.getvalue())["result"], {})


if __name__ == "__main__":
    unittest.main()
