#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""relay-mcp · 中转站账户管理 MCP 服务器
让 AI 直接查余额、测活、列模型。零依赖，stdio JSON-RPC。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

__version__ = "1.0.0"

PROTOCOL_VERSION = "2025-06-18"

KEYWATCH_CONFIG = Path.home() / ".key-watch" / "config.json"
SELF_CONFIG = Path.home() / ".relay-mcp" / "config.json"
REMOTE_URL = "https://raw.githubusercontent.com/dafeiai918/oneclickrelay-remote/main/remote.json"
FALLBACK_RECOMMEND = {"name": "好同根", "url": "https://haotogen.com/",
                      "note": "OpenAI 兼容接口，支持主流模型"}
TIMEOUT = 15


# ================= MCP 内核（零依赖 stdio JSON-RPC） =================

class MCPServer:
    def __init__(self, name: str, version: str, tools: list):
        self.name = name
        self.version = version
        self.tools = tools

    def _send(self, msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _result(self, id_, result) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "result": result})

    def _error(self, id_, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})

    def handle(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        id_ = msg.get("id")

        if method == "initialize":
            self._result(id_, {"protocolVersion": PROTOCOL_VERSION,
                               "capabilities": {"tools": {"listChanged": False}},
                               "serverInfo": {"name": self.name, "version": self.version}})
        elif method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            return
        elif method == "ping":
            self._result(id_, {})
        elif method == "tools/list":
            self._result(id_, {"tools": [
                {"name": t["name"], "description": t["description"],
                 "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}}
                for t in self.tools]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            tool = next((t for t in self.tools if t["name"] == name), None)
            if tool is None:
                self._error(id_, -32602, f"未知工具: {name}")
                return
            try:
                text = tool["handler"](args)
                self._result(id_, {"content": [{"type": "text", "text": str(text)}], "isError": False})
            except Exception as exc:
                self._result(id_, {"content": [{"type": "text",
                                                "text": f"错误: {type(exc).__name__}: {exc}"}],
                                   "isError": True})
        elif id_ is not None:
            self._error(id_, -32601, f"不支持的方法: {method}")

    def serve(self) -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if isinstance(msg, list):
                for item in msg:
                    self.handle(item)
            else:
                self.handle(msg)


# ================= 业务逻辑 =================

def load_sites() -> list[dict]:
    """站点来源：relay-mcp 自己的配置 + key-watch 的配置"""
    sites: list[dict] = []
    for path in (SELF_CONFIG, KEYWATCH_CONFIG):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in (data.get("sites") or []):
            if isinstance(item, dict) and item.get("base_url") and item.get("api_key"):
                sites.append({"name": item.get("name") or item["base_url"],
                              "base_url": item["base_url"], "api_key": item["api_key"]})
    seen, unique = set(), []
    for site in sites:
        if site["name"] in seen:
            continue
        seen.add(site["name"])
        unique.append(site)
    return unique


def find_site(name: str) -> dict | None:
    for site in load_sites():
        if site["name"] == name:
            return site
    low = (name or "").lower()
    for site in load_sites():
        if low and low in site["name"].lower():
            return site
    return None


def normalize(base: str) -> tuple[str, str]:
    root = (base or "").strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    return root, root + "/v1"


def http_get(url: str, key: str, timeout: float = TIMEOUT) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": f"relay-mcp/{__version__}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def probe_site(base_url: str, api_key: str) -> dict:
    root, full = normalize(base_url)
    result = {"ok": False, "latency_ms": None, "models": [], "error": ""}
    started = time.perf_counter()
    try:
        data = http_get(f"{full}/models", api_key)
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        result["models"] = [m.get("id", "") for m in (data.get("data") or [])
                            if isinstance(m, dict) and m.get("id")]
        result["ok"] = True
    except urllib.error.HTTPError as exc:
        exc.close()
        result["error"] = f"HTTP {exc.code}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}"
    return result


def probe_balance(base_url: str, api_key: str) -> dict | None:
    root, _ = normalize(base_url)
    attempts = [
        ("credit_grants", f"{root}/v1/dashboard/billing/credit_grants"),
        ("subscription", f"{root}/v1/dashboard/billing/subscription"),
    ]
    for kind, url in attempts:
        try:
            data = http_get(url, api_key)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if kind == "credit_grants" and isinstance(data.get("total_available"), (int, float)):
            return {"amount": round(float(data["total_available"]), 2), "currency": "USD",
                    "source": "credit_grants"}
        if kind == "subscription" and isinstance(data.get("hard_limit_usd"), (int, float)):
            used = data.get("used_usd") or 0
            return {"amount": round(float(data["hard_limit_usd"]) - float(used), 2),
                    "currency": "USD", "source": "subscription"}
    try:
        data = http_get(f"{root}/api/user/self", api_key)
        quota = (data.get("data") or {}).get("quota")
        if isinstance(quota, (int, float)):
            return {"amount": round(quota / 500000, 2), "currency": "USD", "source": "one-api"}
    except Exception:
        pass
    return None


def fetch_recommend(timeout: float = 5.0) -> dict:
    try:
        req = urllib.request.Request(REMOTE_URL,
                                     headers={"User-Agent": f"relay-mcp/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        rec = data.get("recommend")
        if isinstance(rec, dict) and isinstance(rec.get("url"), str):
            return {"name": rec.get("name", ""), "url": rec["url"]}
    except Exception:
        pass
    return dict(FALLBACK_RECOMMEND)


# ================= 工具实现 =================

def tool_list_sites(args: dict) -> str:
    sites = load_sites()
    if not sites:
        lines = ["还没有配置中转站。", "",
                 "方式一：运行 key-watch 添加",
                 "  python key_watch.py add -n 站点名 -u https://api.example.com/v1 -k sk-xxx",
                 "方式二：直接编辑 " + str(SELF_CONFIG),
                 '  {"sites": [{"name": "站点名", "base_url": "https://...", "api_key": "sk-..."}]}']
        rec = fetch_recommend()
        lines += ["", f"还没有中转站？推荐：{rec['name']} {rec['url']}"]
        return "\n".join(lines)
    lines = [f"已配置 {len(sites)} 个中转站：", ""]
    for site in sites:
        key = site["api_key"]
        masked = f"{key[:6]}****{key[-4:]}" if len(key) > 10 else "****"
        lines.append(f"- {site['name']}  {site['base_url']}  {masked}")
    return "\n".join(lines)


def tool_check_balance(args: dict) -> str:
    name = (args.get("site") or "").strip()
    targets = []
    if name:
        site = find_site(name)
        if not site:
            return f"未找到站点「{name}」。先运行 list_sites 看看有哪些。"
        targets = [site]
    else:
        targets = load_sites()
    if not targets:
        return "还没有配置中转站。先运行 list_sites 看配置方式。"
    lines = []
    for site in targets:
        probe = probe_site(site["base_url"], site["api_key"])
        if not probe["ok"]:
            lines.append(f"- {site['name']}：不可用（{probe['error']}）")
            continue
        balance = probe_balance(site["base_url"], site["api_key"])
        if balance and balance["amount"] is not None:
            lines.append(f"- {site['name']}：可用，余额 ${balance['amount']} "
                         f"（{balance['source']}），延迟 {probe['latency_ms']}ms")
        else:
            lines.append(f"- {site['name']}：可用，余额查询不支持，"
                         f"延迟 {probe['latency_ms']}ms，{len(probe['models'])} 个模型")
    return "\n".join(lines)


def tool_test_site(args: dict) -> str:
    base_url = (args.get("base_url") or "").strip()
    api_key = (args.get("api_key") or "").strip()
    if not base_url:
        site = find_site((args.get("site") or "").strip())
        if not site:
            return "需要 base_url + api_key，或用 site 指定已配置的站点名。"
        base_url, api_key = site["base_url"], site["api_key"]
    if not api_key:
        return "缺少 api_key。"
    probe = probe_site(base_url, api_key)
    if not probe["ok"]:
        return f"站点不可用：{probe['error']}\n地址：{base_url}"
    root, full = normalize(base_url)
    lines = [f"站点可用 ✓",
             f"- 根地址：{root}",
             f"- 接口地址：{full}",
             f"- 延迟：{probe['latency_ms']}ms",
             f"- 可用模型：{len(probe['models'])} 个"]
    if probe["models"][:5]:
        lines.append("- 示例模型：" + ", ".join(probe["models"][:5]))
    lines += ["", "配置客户端时注意：Cherry Studio 填根地址（不带 /v1），"
                  "其他多数客户端填接口地址（带 /v1）。"]
    return "\n".join(lines)


def tool_list_models(args: dict) -> str:
    base_url = (args.get("base_url") or "").strip()
    api_key = (args.get("api_key") or "").strip()
    if not base_url:
        site = find_site((args.get("site") or "").strip())
        if not site:
            return "需要 base_url + api_key，或用 site 指定已配置的站点名。"
        base_url, api_key = site["base_url"], site["api_key"]
    probe = probe_site(base_url, api_key)
    if not probe["ok"]:
        return f"无法获取模型列表：{probe['error']}"
    keyword = (args.get("keyword") or "").lower()
    models = [m for m in probe["models"] if keyword in m.lower()] if keyword else probe["models"]
    if not models:
        return f"没有匹配的模型（共 {len(probe['models'])} 个）"
    lines = [f"共 {len(models)} 个模型：", ""]
    lines += [f"- {m}" for m in models[:100]]
    if len(models) > 100:
        lines.append(f"…还有 {len(models) - 100} 个")
    return "\n".join(lines)


def tool_recommended_site(args: dict) -> str:
    rec = fetch_recommend()
    lines = [f"推荐中转站：{rec.get('name', '')}",
             f"地址：{rec['url']}",
             "",
             "使用前建议先测一下质量和速度：",
             "把 base_url 和 api_key 交给 test_site 工具即可。"]
    if rec.get("note"):
        lines.insert(2, rec["note"])
    return "\n".join(lines)


TOOLS = [
    {
        "name": "list_sites",
        "description": "列出本机已配置的中转站（含地址和打码后的密钥）。"
                       "用户问「我配了哪些站」「有哪些中转站」时使用。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_list_sites,
    },
    {
        "name": "check_balance",
        "description": "查询中转站余额和可用性。用户问「还剩多少钱」「哪个站还能用」时使用。"
                       "不传 site 则检查全部站点。",
        "inputSchema": {
            "type": "object",
            "properties": {"site": {"type": "string", "description": "站点名，留空查全部"}},
        },
        "handler": tool_check_balance,
    },
    {
        "name": "test_site",
        "description": "测试中转站是否可用、延迟多少、有多少模型。"
                       "用户给出地址和密钥想知道能不能用时使用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "站点地址，如 https://api.example.com/v1"},
                "api_key": {"type": "string", "description": "API 密钥"},
                "site": {"type": "string", "description": "或用已配置的站点名"},
            },
        },
        "handler": tool_test_site,
    },
    {
        "name": "list_models",
        "description": "列出某站点所有可用模型。用户问「这个站有哪些模型」时使用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {"type": "string"},
                "api_key": {"type": "string"},
                "site": {"type": "string", "description": "或用已配置的站点名"},
                "keyword": {"type": "string", "description": "按关键词过滤，如 gpt、claude"},
            },
        },
        "handler": tool_list_models,
    },
    {
        "name": "recommended_site",
        "description": "推荐一个可用的中转站。用户问「推荐中转站」「哪里能买到 API」"
                       "或还没有任何站点时使用。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_recommended_site,
    },
]


def main() -> int:
    if os.name == "nt":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    MCPServer("relay-mcp", __version__, TOOLS).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
