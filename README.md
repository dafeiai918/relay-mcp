# relay-mcp · 中转站账户管理 MCP

**让你的 AI 直接回答：「我还剩多少钱？」「这个站靠谱吗？」「它有哪些模型？」**

零依赖 · 单文件 · stdio MCP 服务器 · 中文

---

## 它让 AI 能做什么

装上之后，在 Claude Desktop / Cherry Studio / Cursor / Cline 里直接说人话：

> 「帮我看看我的中转站还剩多少钱」
> → AI 调用 `check_balance`，返回余额、延迟、可用状态

> 「测试一下这个站 https://api.example.com/v1，密钥 sk-xxx」
> → AI 调用 `test_site`，返回延迟、模型数、地址该怎么填

> 「列出所有 claude 开头的模型」
> → AI 调用 `list_models`，按关键词过滤返回

> 「推荐一个中转站」
> → AI 调用 `recommended_site`

---

## 提供的工具

| 工具 | 作用 | 触发说法 |
|---|---|---|
| `list_sites` | 列出本机已配置的中转站 | 「我配了哪些站」 |
| `check_balance` | 查余额 + 可用性 | 「还剩多少钱」「哪个站能用」 |
| `test_site` | 测延迟、可用模型数 | 「这个站能用吗」 |
| `list_models` | 列出可用模型（可过滤） | 「有哪些模型」 |
| `recommended_site` | 推荐可用中转站 | 「推荐一个中转站」 |

---

## 安装

### 方式一：uvx 一行（推荐）

```bash
uvx --from git+https://github.com/dafeiai918/relay-mcp relay-mcp
```

客户端配置（Claude Desktop `claude_desktop_config.json`、Cherry Studio MCP 设置等）：

```json
{
  "mcpServers": {
    "relay-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/dafeiai918/relay-mcp", "relay-mcp"]
    }
  }
}
```

### 方式二：本地文件

```bash
git clone https://github.com/dafeiai918/relay-mcp
```

```json
{
  "mcpServers": {
    "relay-mcp": {
      "command": "python",
      "args": ["/绝对路径/relay-mcp/relay_mcp.py"]
    }
  }
}
```

不需要 `uv`，任意 Python 3.9+ 即可，**零第三方依赖**。

---

## 配置站点

`relay-mcp` 会读两个位置的站点配置：

**1. 自己的配置** `~/.relay-mcp/config.json`：

```json
{
  "sites": [
    {"name": "我的站", "base_url": "https://api.example.com/v1", "api_key": "sk-xxxxxxxx"}
  ]
}
```

**2. 复用 [key-watch](https://github.com/dafeiai918/key-watch) 的配置** `~/.key-watch/config.json`

如果你已经用 key-watch 管理多个站点，**不用重复配置**，relay-mcp 直接读。

---

## 余额查询支持

不同中转站的余额接口不一样，本工具依次尝试：

| 接口 | 常见于 |
|---|---|
| `GET /v1/dashboard/billing/credit_grants` | 仿官方计费接口 |
| `GET /v1/dashboard/billing/subscription` | 同上 |
| `GET /api/user/self` | One API / New API |

都查不到时返回「余额查询不支持」，但可用性、延迟、模型列表仍然照常工作。

---

## 安全

- 密钥只在本机读取，**不会发送到除你配置的站点之外的任何地方**
- 返回给 AI 的密钥一律打码（`sk-abc****f2e1`）
- 服务器只监听 stdio，不开放网络端口

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [key-watch](https://github.com/dafeiai918/key-watch) | 多站余额监控 + 告警（relay-mcp 复用它的配置） |
| [relay-bench](https://github.com/dafeiai918/relay-bench) | 深度体检：延迟 / 并发 / 参数兼容 / 降智抽样 |
| [relay-converter](https://github.com/dafeiai918/relay-converter) | 一份配置转 19 种客户端格式 |
| [awesome-relay](https://github.com/dafeiai918/awesome-relay) | 中转站与客户端精选导航 |

---

## 开发

```bash
python tests/test_relay_mcp.py     # 协议 + 业务测试，不需要联网
```

新增工具：在 `TOOLS` 列表里加一个 dict（`name` / `description` / `inputSchema` / `handler`）即可。

---

## License

MIT
