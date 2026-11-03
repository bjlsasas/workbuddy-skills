---
name: localize-web-ui-zh
description: 把本地工具/服务的硬编码英文界面汉化（Web 前端静态标签 + JS 动态文案 + 控制台输出），并保证可回滚、可验证。当用户抱怨「界面是英文的」「怎么是英文界面」「汉化一下」「改成中文界面」，且目标是本地部署的 Web UI / CLI 工具（没有 i18n 机制、文案硬编码在源码里）时使用。
agent_created: true
---

# 硬编码英文界面汉化（Web UI + 控制台）

适用场景：本地跑的开源工具/服务，界面文案硬编码在 `index.html` / `app.js` / `server.py` 里，
**没有 i18n 开关**，用户想要中文界面。

## 核心原则

1. **先备份，再动手**：`index.html.en.bak` / `app.js.en.bak` / `server.py.en.bak`，改动可一键还原。
2. **先摸清渲染方式**：静态文件是"每次请求实时读盘"还是"启动时缓存"？决定改完要不要重启服务。
   - 判定：看服务端代码里是 `f.read_bytes()`（实时）还是启动时载入内存；有 `Cache-Control: no-cache` 更稳。
3. **替换用脚本，不用手工逐条改**：几十上百处文案，用 Python 脚本做**精确整串替换 + 逐条命中计数 +
   全中才写盘（fail-closed）**。任何一条匹配不到就不写盘，避免半成品状态。
4. **改完必须校验**：JS 用 `node --check`，Python 用 `python -m py_compile`。
5. **验证要看服务实际返回的内容**，不是看本地文件：fetch `/` 和 `/web/app.js`，断言中文关键标记命中。

## 执行步骤

### 1. 定位所有用户可见文案
- 静态标签：`index.html`（按钮、标题、placeholder、`aria-label`、`title`）
- 动态文案：`app.js`（状态 pill、指标名、toast、表格表头、折叠标题、aria-label）
- 控制台输出：服务端 `print(...)`（加载中 / 就绪 / 进度 / 完成 / 警告）
- **纯后端逻辑文件不用动**（只有 docstring 的那种，用 grep 确认没有 HTML/UI 字符串）

### 2. 写替换脚本（关键技巧）
```python
PAIRS = [ (old_exact, new_text), ... ]
missing = []
for old, new in PAIRS:
    if src.count(old) == 0:
        missing.append(old[:70])
    else:
        src = src.replace(old, new)
if missing:                     # fail-closed：不写盘，先修 old 串
    print("MISSING:", missing)
else:
    io.open(PATH, "w", encoding="utf-8").write(src)
```
再加一份"--per-pair hit counts"报告，检查是否有意外的 0 或 >1。

**踩坑清单（照抄就少走弯路）**：
- old 串含反斜杠/引号时，用 raw 三引号 `r'''...'''`，否则 `\"` 会被 Python 解释掉
- 多行片段要**连缩进一起照抄**
- 结尾标点要精确：`"";` 与 `"");` 差别就会导致匹配不到
- 常见误伤：`aria-label="Copy"` 这类会命中多处 → 故意用 `replace_all` 或加长上下文

### 3. 翻译时的取舍（中文产品惯例）
- **保留技术单位**：`tokens`、`tok/s`、`GB`、`MB/s`、`°C`、`W`、`Top-p`、`Top-k`、`Seed`、`GPU/CPU/RAM`
- **保留内部状态值**：如 `thinking` / `answering` / `reading`——前端靠它判断状态，只在**显示层**映射：
  `{thinking: "思考中", answering: "作答中"}[live.phase] || "生成中"`
- 不要动 CSS 类名、`data-*` 属性值（如 `st-badge--queued`、`data-s="queued"`）——它们不是文案
- `<html lang="en">` → `lang="zh-CN"`

### 4. Windows 控制台中文乱码防护
给启动脚本（.bat）加：
```bat
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
```
否则中文在部分终端/重定向下会乱码。

### 5. 验证
```python
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机有代理时必须绕过！
html = opener.open("http://127.0.0.1:<port>/").read().decode()
assert all(k in html for k in ["对话", "设置", ...])
```
- 本机若存在系统代理（`http_proxy=http://127.0.0.1:xxxxx`），访问 localhost 会拿到 **502**，
  必须 `ProxyHandler({})` 绕过
- 再跑一次真实请求（如对话），确认功能没被文案改动破坏

### 6. 收尾
- 通知用户「刷新浏览器即可」(Ctrl+F5)，不需要重启服务（前提：静态文件实时读盘）
- 说明备份文件位置，方便还原
- 若用户给结构化 UI，最好用浏览器预览把页面直接展示给用户看

### 7. 重要：检查首页是否发了 Cache-Control（不做这步可能"汉化无效"）

**现象**：用户改完反馈「怎么还是英文界面」，甚至截图里是完全不相干的另一套 UI，
外加 `Server unavailable` / `not found`。而磁盘上的文件明明已经是中文。

**根因**：应用外壳（首页 `/`）**没发 `Cache-Control`**。HTTP/1.1 对无缓存头的响应用
**启发式缓存**（约 `(Date - Last-Modified) * 10%` 的保鲜期），浏览器于是复用历史上同一
`host:port` 缓存过的页面。若这个端口以前跑过别的服务（典型：`llama-server` 默认 8080），
用户看到的就是**上一个程序的界面**；新程序不认旧 UI 调的接口（如 `/props`），
返回自己的 404（如 `{"error":{"message":"not found"}}`），旧 UI 就渲染成「服务器不可用」。

**处理**：
1. 先按"改完的磁盘文件 vs 服务实际返回"比对，再看响应头。缺头就是它。
2. 给应用外壳补上禁缓存头（比 `/web/*` 的 `no-cache` 更狠，用 `no-store`）：
   ```python
   self.send_header("Cache-Control", "no-store")   # 首页/HTML 外壳专用
   ```
3. 已缓存的旧条目不会自己失效 → 让用户 **Ctrl+Shift+R 强刷**，或换无痕窗口。
   **最省事的办法：直接用内建浏览器预览（全新上下文，不共享用户浏览器缓存）。**
4. 重启服务后 fetch 一次确认头已生效。

**顺带须查**：本机是否另有服务占着**同一个端口**（`Get-NetTCPConnection -State Listen`
列出所有监听端口 + 所属进程）。同端口的两套东西会互抢，用户很容易点错、看错。
在交付说明里写清「哪个端口是哪套」。

## 不适用
- 有正式 i18n 机制的框架（React/Vue 的 i18n、Flask-Babel 等）→ 直接加 `zh-CN` 语言包，别改源码
- 需要长期跟随上游更新的项目 → 改源码会被覆盖，应改为注入式（如反向代理替换、用户脚本）
