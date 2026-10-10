# -*- coding: utf-8 -*-
"""relay-mcp 测试：MCP 协议 + 业务逻辑，本地假站点，不发外网"""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import relay_mcp as rm  # noqa: E402


class _Site(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.endswith("/v1/models"):
            payload = {"data": [{"id": "gpt-4o-mini"}, {"id": "deepseek-chat"}]}
        elif self.path.endswith("/credit_grants"):
            payload = {"total_available": 8.5}
        else:
            payload = {"error": "not found"}
        body = json.dumps(payload).encode()
        code = 404 if "error" in payload else 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MCPProtocolTest(unittest.TestCase):
    """不依赖网络的协议测试"""

    def setUp(self):
        self.sent = []
        self.server = rm.MCPServer("test", "1.0", rm.TOOLS)
        self.server._send = lambda msg: self.sent.append(msg)

    def _call(self, method, **kw):
        msg = {"jsonrpc": "2.0", "id": 1, "method": method}
        if kw:
            msg["params"] = kw
        self.server.handle(msg)
        return self.sent[-1]

    def test_initialize(self):
        out = self._call("initialize")
        self.assertEqual(out["result"]["serverInfo"]["name"], "test")
        self.assertIn("tools", out["result"]["capabilities"])
        self.assertIn("protocolVersion", out["result"])

    def test_notification_no_reply(self):
        before = len(self.sent)
        self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(len(self.sent), before)

    def test_ping(self):
        self.assertEqual(self._call("ping")["result"], {})

    def test_tools_list_schema(self):
        tools = self._call("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertIn("list_sites", names)
        self.assertIn("check_balance", names)
        self.assertIn("test_site", names)
        self.assertIn("list_models", names)
        self.assertIn("recommended_site", names)
        for tool in tools:
            self.assertIn("description", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_tool(self):
        out = self._call("tools/call", name="不存在", arguments={})
        self.assertEqual(out["error"]["code"], -32602)

    def test_unknown_method(self):
        out = self._call("完全不存在的方法")
        self.assertEqual(out["error"]["code"], -32601)

    def test_tool_exception_becomes_iserror(self):
        server = rm.MCPServer("t", "1", [{
            "name": "boom", "description": "d", "inputSchema": {"type": "object", "properties": {}},
            "handler": lambda a: 1 / 0,
        }])
        sent = []
        server._send = lambda m: sent.append(m)
        server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "boom", "arguments": {}}})
        self.assertTrue(sent[-1]["result"]["isError"])
        self.assertIn("ZeroDivisionError", sent[-1]["result"]["content"][0]["text"])


class BusinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        cls.port = cls.site.server_address[1]
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._orig = (rm.SELF_CONFIG, rm.KEYWATCH_CONFIG)
        rm.SELF_CONFIG = base / "relay.json"
        rm.KEYWATCH_CONFIG = base / "kw.json"

    def tearDown(self):
        rm.SELF_CONFIG, rm.KEYWATCH_CONFIG = self._orig
        self.tmp.cleanup()

    def _write_config(self, sites):
        rm.SELF_CONFIG.write_text(json.dumps({"sites": sites}, ensure_ascii=False), encoding="utf-8")

    def test_normalize(self):
        self.assertEqual(rm.normalize("https://a.com/v1"), ("https://a.com", "https://a.com/v1"))
        self.assertEqual(rm.normalize("https://a.com/"), ("https://a.com", "https://a.com/v1"))

    def test_list_sites_empty_gives_guide(self):
        text = rm.tool_list_sites({})
        self.assertIn("还没有配置", text)
        self.assertIn("key_watch", text)

    def test_list_sites_masks_key(self):
        self._write_config([{"name": "站A", "base_url": "https://a.com/v1",
                             "api_key": "sk-1234567890abcdef"}])
        text = rm.tool_list_sites({})
        self.assertIn("站A", text)
        self.assertNotIn("sk-1234567890abcdef", text)
        self.assertIn("****", text)

    def test_merges_keywatch_config(self):
        rm.KEYWATCH_CONFIG.write_text(json.dumps({"sites": [
            {"name": "来自watch", "base_url": "https://b.com/v1", "api_key": "sk-abcdefghijklmn"}]}),
            encoding="utf-8")
        names = [s["name"] for s in rm.load_sites()]
        self.assertIn("来自watch", names)

    def test_dedupe_by_name(self):
        self._write_config([{"name": "同名", "base_url": "https://a.com/v1", "api_key": "sk-aaaabbbbcccc"}])
        rm.KEYWATCH_CONFIG.write_text(json.dumps({"sites": [
            {"name": "同名", "base_url": "https://b.com/v1", "api_key": "sk-ddddeeeeffff"}]}),
            encoding="utf-8")
        self.assertEqual(len([s for s in rm.load_sites() if s["name"] == "同名"]), 1)

    def test_probe_site_ok(self):
        result = rm.probe_site(f"http://127.0.0.1:{self.port}/v1", "sk-test")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["models"]), 2)

    def test_probe_site_down(self):
        result = rm.probe_site("http://127.0.0.1:1/v1", "sk-test")
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_probe_balance(self):
        balance = rm.probe_balance(f"http://127.0.0.1:{self.port}/v1", "sk-test")
        self.assertIsNotNone(balance)
        self.assertAlmostEqual(balance["amount"], 8.5)

    def test_tool_test_site_output(self):
        text = rm.tool_test_site({"base_url": f"http://127.0.0.1:{self.port}/v1",
                                  "api_key": "sk-test"})
        self.assertIn("站点可用", text)
        self.assertIn("不带 /v1", text)

    def test_tool_list_models_filter(self):
        text = rm.tool_list_models({"base_url": f"http://127.0.0.1:{self.port}/v1",
                                    "api_key": "sk-test", "keyword": "deepseek"})
        self.assertIn("deepseek-chat", text)
        self.assertNotIn("gpt-4o-mini", text)

    def test_tool_check_balance_by_site(self):
        self._write_config([{"name": "站A", "base_url": f"http://127.0.0.1:{self.port}/v1",
                             "api_key": "sk-test"}])
        text = rm.tool_check_balance({"site": "站A"})
        self.assertIn("8.5", text)

    def test_tool_check_balance_unknown_site(self):
        self._write_config([])
        self.assertIn("未找到", rm.tool_check_balance({"site": "不存在"}))

    def test_recommended_site(self):
        text = rm.tool_recommended_site({})
        self.assertIn("推荐中转站", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
