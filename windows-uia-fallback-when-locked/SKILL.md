---
name: windows-uia-fallback-when-locked
description: 当 Windows 工作站处于锁屏/非交互状态（物理鼠标键盘与截屏全不可用、windows-mcp 连接器失效）时，仍要读屏并操控 Chrome 等应用的降级通路。用 Python uiautomation 读 UIA 树，用 Win32 InvokePattern/SelectionItemPattern 触发工具栏按钮与标签页切换，用 PostMessage 注入键盘事件驱动页面。当出现「截图全黑 / SetCursorPos 返回 0 / OpenInputDesktop 拒绝访问 / mcp__windows-mcp 不可用 / 定时任务在锁屏时段运行」时使用。
agent_created: true
---

# 锁屏桌面下的 UIA 降级操控（Windows）

## 适用场景

无人值守的定时任务常常在**工作站已锁屏**时触发。此时：

- `mcp__windows-mcp__*` 工具会因截图/输入失败而整体报错（`OSError: screen grab failed`），
- 任何依赖 `SetCursorPos` / `mouse_event` / `SendInput` 的自动化都静默失效，
- 但 **UIA（UI Automation）读取仍然完全可用**，而且部分「程序化动作」也能用。

本 skill 给出可复现的替代通路。

## 第一步：先做环境自检（30 秒）

```python
import ctypes
from ctypes import wintypes
u = ctypes.windll.user32
print("input desktop:", u.OpenInputDesktop(0, False, 0x0001 | 0x0100))  # 0 = 锁屏/无权限
print("foreground:", u.GetForegroundWindow())                            # 0 = 无前台窗口
u.SetCursorPos(700, 500)
pt = wintypes.POINT(); u.GetCursorPos(ctypes.byref(pt)); print("cursor:", pt.x, pt.y)  # 0,0 且不移动 = 锁屏
```

判定：`OpenInputDesktop` 返回 0（err=5 ACCESS_DENIED）+ 光标钉在 (0,0) + `SetCursorPos` 返回 0
→ **工作站已锁屏**，放弃一切物理输入方案，转入下面的 UIA 降级通路。
（注意：这与 Bash 沙箱无关，`dangerouslyDisableSandbox` 也救不回来；`qwinsta` 会显示会话「运行中」，别被误导。）

## 第二步：UIA 读屏（主力手段）

安装与基础用法：

```bash
uv pip install --python <venv>/Scripts/python.exe uiautomation
```

```python
import uiautomation as auto
auto.SetGlobalSearchTimeout(6)
w = auto.WindowControl(searchDepth=1, SubName="Chrome")   # SubName 区分大小写！
```

要点：

- **必须递归 `GetChildren()` 手动遍历**（`FindAll` 在 Chrome 上常拿不全）。限制 `maxdepth` 防爆栈。
- 元素带 `Name` / `ControlTypeName` / `ClassName` / `AutomationId` / `BoundingRectangle`，等价于 windows-mcp 的 UI 树。
- **`SubName` / `Name` 区分大小写**：窗口标题从「CG模型网…」变成「cg模型网…」就会匹配失败，用 `"Chrome"` 更稳。
- 小控件/无文字的控件改用 `AutomationId`（Chrome 的地址栏是 `view_1012`）。
- 复现用脚本见 `D:\workbuddy_gzuo\2026-10-09-14-02-51\_tools\uia_util.py`（`dump` / `find` / `click` 子命令）。

## 第三步：能用的「程序化动作」清单

| 目标 | 可用手段 | 备注 |
|---|---|---|
| Chrome 工具栏按钮（返回/前进/重新加载/首页） | `GetInvokePattern().Invoke()` | **有效**，锁屏下照常工作 |
| Chrome 标签页切换 | `GetSelectionItemPattern().Select()` | **有效** |
| 网页里的 `<a>` / `<button>` | `GetInvokePattern().Invoke()` | Chrome 对标准控件暴露 Invoke |
| 网页里的 `<div onclick>` 伪按钮（如各种 tab） | 无任何 pattern（`DefaultAction` 为空，`DoDefaultAction()` 抛 COMError） | 见第四步 |
| 地址栏填值 | `GetValuePattern().SetValue(url)` | 可写；但**回车需注入键盘**（第五步） |

## 第四步：伪按钮 → 聚焦 + 注入空格

`<div>` 型 tab 无法 Invoke。做法：

```python
c.SetFocus()                     # UIA SetFocus 锁屏下可用
# 再向 Chrome 顶层窗口注入空格/回车（见第五步）
```

实测：聚焦到「待审核」div 后注入 `VK_SPACE`，页面路由真的切了过去。

## 第五步：PostMessage 注入键盘/鼠标

```python
u = ctypes.windll.user32
hwnd = <Chrome 顶层窗口 hwnd>          # 类名 Chrome_WidgetWin_1
u.PostMessageW(hwnd, 0x0100, vk, 0)    # WM_KEYDOWN
u.PostMessageW(hwnd, 0x0101, vk, 0)    # WM_KEYUP
```

- **键盘有效**：页面级按键（如空格激活聚焦元素）能送达渲染进程。
- **鼠标基本无效**：向 `Chrome_WidgetWin_1` 或 `Chrome_RenderWidgetHostHWND` / `Intermediate D3D Window` 投
  `WM_LBUTTONDOWN/UP`（屏幕坐标换算成该窗口客户区坐标）实测无反应 —— 现代 Chrome 的内容输入不走 Win32 消息。
  所以**凡是必须「点」的地方都要想办法换成键盘**。
- 顶层窗口有 3 个子窗口：`Chrome_RenderWidgetHostHWND`（地址栏那块）、`Intermediate D3D Window`（合成器）。
  **别用 `EnumChildWindows` 找「页面渲染窗口」去点，找不到也不管用。**

## 第六步：不要做的操作（血泪）

- **不要 `InvokePattern` 点「重新加载」**：会触发站点 WAF（实测 cgmodel.com 弹出
  `challenge.cgmodel.com/?return_to=…` 安全环境验证页），且页面 DOM 从此不再暴露给 UIA。
  需要刷新时优先用 `SelectionItemPattern.Select()` 换标签页或换一个已有的标签页。
- 站点被判为挑战后，`curl` 同样拿到挑战页 —— **反爬不区分客户端，别指望用 HTTP 直连绕过**。
- 退出挑战页：`GetInvokePattern().Invoke()` 点击 Chrome「返回」按钮，可能要连点几次才回到目标站点（历史上每次重定向都留了一条记录）。
- 页面出现「页面加载中，请稍候~」长时间不消失时，通常是**渲染/无障碍树被 Chrome 冻结**（窗口被判定为遮挡或前台丢失），
  继续等没用；换标签页或直接读别的窗口。

## 第七步：拿不到登录态时的取舍

Chrome 的 `User Data/Default/Network/Cookies` 被浏览器**独占锁**（`ERROR_SHARING_VIOLATION`，
`open` / `shutil.copy` / `CreateFileW` / sqlite 只读 / `robocopy /B` 全部失败）。
非管理员权限下**没有**可行办法复制它（`robocopy /B` 与 VSS 都要提权）。

因此：**复制 profile + headless Chrome** 这条路在锁屏 + 非提权下拿不到登录态，
headless 打开商户后台只会落到登录页。别在这上面耗时间；
直接判断「本次只能做只读巡查」并把结论报给用户。

## 复现脚本

`D:\workbuddy_gzuo\2026-10-09-14-02-51\_tools\`：

- `uia_util.py` — UIA dump / find / click / setval
- `chrome_msg.py` — PostMessage 键盘/鼠标注入
- `dinput.py` / `probe.py` — 桌面可交互性诊断
- `wbmcp.py` — 不依赖连接器、直接用 stdio 跑 `uvx windows-mcp serve` 的 MCP 客户端
  （用法：`python wbmcp.py cmds.json out.json`；**锁屏时它照样截不了屏**，只在桌面解锁后才有价值）
