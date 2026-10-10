# -*- coding: utf-8 -*-
"""stdio 集成测试：真的把服务器当子进程跑起来，走 stdin/stdout"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "relay_mcp.py"


class StdioTest(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ROOT))

    def tearDown(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    def send(self, msg: dict) -> None:
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def read_response(self, timeout_reader=None) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            self.fail("服务器没有返回任何内容")
        return json.loads(line.decode("utf-8"))

    def test_initialize_first(self):
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "t", "version": "1"}}})
        resp = self.read_response()
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "relay-mcp")
        self.assertIn("protocolVersion", resp["result"])

    def test_full_handshake_sequence(self):
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "t", "version": "1"}}})
        init = self.read_response()
        self.assertEqual(init["id"], 1)

        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listing = self.read_response()
        self.assertEqual(listing["id"], 2)
        self.assertGreaterEqual(len(listing["result"]["tools"]), 5)

    def test_tools_call_roundtrip(self):
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        self.read_response()
        self.send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                   "params": {"name": "list_sites", "arguments": {}}})
        resp = self.read_response()
        self.assertEqual(resp["id"], 9)
        self.assertFalse(resp["result"]["isError"])
        self.assertIn("text", resp["result"]["content"][0])

    def test_bad_json_does_not_crash(self):
        self.proc.stdin.write("这不是json\n".encode("utf-8"))
        self.proc.stdin.flush()
        self.send({"jsonrpc": "2.0", "id": 5, "method": "ping"})
        resp = self.read_response()
        self.assertEqual(resp["id"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
