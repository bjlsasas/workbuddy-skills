---
name: 3dsmax-mcp-setup
description: 在 Windows 上安装并把 3dsmax-mcp（Autodesk 3ds Max 的 MCP 连接器）接入 WorkBuddy，让 AI agent 直接读写 3ds Max 场景。当用户需要为 3ds Max 2023-2027 配置 MCP、连接/排查 bridge、更新版本或卸载时使用。触发词：3ds Max MCP、3dsmax-mcp、连接 3ds Max、maxmcp。
agent_created: true
---

# 3dsmax-mcp 安装接入（Windows）

把 Autodesk 3ds Max 接成 MCP 服务，让 agent 通过 175 个工具读写场景、建模、改材质、跑 MAXScript。

## 核心结论（先读这条）

**不要用 `git clone` + `uv sync` + `install.py` 的路线。** 源码仓库的 `bundle/Contents/bin/` 里只有 `.gitkeep`，**不含预编译的 native bridge（.gup）**。从源码安装只能得到 MAXScript 回退路径，能力与性能都打折。

正确做法：**下载 GitHub Release 里的 `3dsmax-mcp-<版本>-Setup.exe`**，它内置了 Max 2023–2027 的全部预编译 GUP。

## 前置检查

```bash
# 1. 找 Max 安装位置——注意不一定在 C 盘
ls -d /c/"Program Files"/Autodesk/"3ds Max"* /d/"Program Files"/Autodesk/"3ds Max"* 2>/dev/null

# 2. 确认 Max 未运行（GUP 靠 Max 启动时加载，装的时候不必退出，但重启才能生效）
tasklist | grep -i 3dsmax || echo "未运行"

# 3. 确认没有外部客户端会被误改，先备份
cp ~/.cursor/mcp.json <备份目录>/ 2>/dev/null
cp ~/.gemini/settings.json <备份目录>/ 2>/dev/null
cp ~/.workbuddy/mcp.json <备份目录>/
```

## 步骤一：下载 Setup.exe

先取 release 列表里的下载地址（`api.github.com` 可能不通，用 WebFetch 抓 releases 页面）：

```
https://github.com/cl0nazepamm/3dsmax-mcp/releases/download/v<版本>/3dsmax-mcp-<版本>-Setup.exe
```

```bash
# 必须加 --ssl-no-revoke，否则 Windows schannel 报 CRYPT_E_REVOCATION_OFFLINE
curl -L --ssl-no-revoke --retry 2 -o "3dsmax-mcp-Setup.exe" "<上面的URL>"

# 校验 SHA256（对得上官方 release 页面标注才算可信）
sha256sum "3dsmax-mcp-Setup.exe"
```

## 步骤二：静默安装

安装包是 Inno Setup 打包（用 `strings` 找 `Inno Setup Setup Data (版本)` 可确认），manifest 是 `asInvoker`——**不会弹 UAC**，可以放心静默跑。

```bash
# MSYS2_ARG_CONV_EXCL 必须加，否则 /VERYSILENT 会被 Git Bash 改写成磁盘路径
MSYS2_ARG_CONV_EXCL='*' ./3dsmax-mcp-Setup.exe \
  /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- \
  '/LOG=<绝对路径>\setup_install.log'
```

安装结果：
| 内容 | 路径 |
|---|---|
| native bridge | `C:\ProgramData\Autodesk\ApplicationPlugins\3dsmax-mcp\Contents\bin\mcp_bridge_<年份>.gup` |
| MAXScript 回退 | `...\Contents\scripts\mcp_server.ms` |
| 包清单 | `...\PackageContents.xml` |
| 服务端 | `C:\Program Files\3dsmax-mcp\runtime\python.exe`（内嵌 Python，无需外部环境） |
| 依赖 | `...\runtime\Lib\site-packages\maxmcp` |

> bridge 装在 `%ProgramData%`，是**全局 ApplicationPlugins 机制**，与 Max 装在 C 盘还是 D 盘无关，不用管 Max 路径对不对得上。

## 步骤三：生成服务端配置（跳过客户端污染）

安装器最后一步会去改 **Claude Desktop / Claude Code / Codex / Cursor / Gemini** 的 MCP 配置，并可能在日志里报 `CLIENT CONFIGURATION FAILED`。

那个报错的常见根因是**当前 shell 没导出 `APPDATA` 环境变量**（`KeyError: 'APPDATA'`），不是安装包的问题。

手工调用，用**空的 `--clients=`** 只生成服务端配置，完全不碰其他客户端：

```bash
export APPDATA='C:\Users\<用户>\AppData\Roaming'
export LOCALAPPDATA='C:\Users\<用户>\AppData\Local'

MSYS2_ARG_CONV_EXCL='*' "C:\Program Files\3dsmax-mcp\runtime\python.exe" \
  "C:\Program Files\3dsmax-mcp\configure.py" --clients=
```

产出 `%LOCALAPPDATA%\3dsmax-mcp\` 下的 `mcp_config.ini`（`safe_mode=true`、`tool_profile=full`）和 `mcp-client.json`。

> 若要顺带给 Cursor 等客户端配上，用 `--clients=cursor,gemini`，但务必先备份。写入前它会做原子写 + 回滚，但仍建议自己留一份。

## 步骤四：写入 WorkBuddy 的 MCP 配置

编辑 `~/.workbuddy/mcp.json`（**不是** `~/.workbuddy/.mcp.json`），与已有条目并列新增：

```json
"3dsmax-mcp": {
  "type": "stdio",
  "command": "C:\\Program Files\\3dsmax-mcp\\runtime\\python.exe",
  "args": ["-m", "maxmcp.server"],
  "env": {
    "NO_PROXY": "localhost,127.0.0.1,::1"
  },
  "disabled": false
}
```

配置**不会自动生效**。告诉用户：到连接器管理页右上角「自定义连接器」入口，对新出现的 `3dsmax-mcp` 点**信任**。

## 步骤五：验证

服务端可以脱机验证（不需要 Max 开着）：

```python
# 启动 python.exe -m maxmcp.server，向 stdin 发换行分隔的 JSON-RPC
# {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}
# 然后 {"jsonrpc":"2.0","method":"notifications/initialized"}
# 再   {"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

期望：`serverInfo.name == "3dsmax-mcp"`，`tools/list` 返回 **175** 个工具（不同版本数量会变），且含 `execute_maxscript` / `query_scene` / `get_bridge_status`。

bridge 侧的验证必须在 **Max 重启之后**做：先 `get_bridge_status` 探活，再 `get_session_context` 取场景概要。

> **首次调用会明显偏慢**——实测 bridge 冷加载后的第一个请求耗时约 **55 秒**（内含 native 插件加载与命名管道握手），同一会话内后续请求降到 **亚毫秒级**（0.24 ms）。不要把首次的长等待误判成卡死或超时，也别急着换 TCP 回退通道。

典型返回值（Max 2025 实测）：

```json
{ "connected": true, "maxVersion": 2025, "server": "3dsmax-mcp-native",
  "transport": "namedpipe", "protocolVersion": 2 }
```

capabilities 位：149 几何类 / 98 材质类 / 164 修改器类。
若 `get_bridge_status` 返回未连接，先确认 Max 确实在运行且版本在 2023–2027 之间。

**开工前先看插件能力再定方案**：`get_plugin_capabilities` 会报告 Forest Pack / RailClone / tyFlow / PhoenixFD 是否安装。这几个未装时，`scatter_forest_pack`、`*_tyflow_*`、`*_railclone_*` 系列工具调了也不会有结果，别白试。

## 版本更新

关掉 Max 和客户端 → 下新版 Setup.exe → 重跑静默安装（会自动迁移并清理旧安装）→ 重启 Max。

## 卸载

```bash
MSYS2_ARG_CONV_EXCL='*' "/c/Program Files/3dsmax-mcp/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES
```

再手工删掉 `~/.workbuddy/mcp.json` 里的 `3dsmax-mcp` 条目。

## 排错速查

| 症状 | 原因 / 处理 |
|---|---|
| `curl: (35) CRYPT_E_REVOCATION_OFFLINE` | 加 `--ssl-no-revoke` |
| `can't open file 'd:\c\Program Files\...'` | Git Bash 路径改写，加 `MSYS2_ARG_CONV_EXCL='*'` 并改用 `C:\...` 形式 |
| 安装日志 `CLIENT CONFIGURATION FAILED` | 缺 `APPDATA` 环境变量；或直接忽略，用手工 `--clients=` 路线 |
| `install.py` 报 "No 3ds Max installations detected" | Max 装在非 C 盘。设 `ADSK_3DSMAX_x64_<年份>` 环境变量指向实际路径（仅影响检测，不影响 GUP 加载） |
| 工具连上但 bridge 不通 | Max 没重启；或 Max 版本不在 2023–2027 |
| 视口卡顿 | `mcp_config.ini` 里调大 `tcp_idle_poll_interval_ms` |
