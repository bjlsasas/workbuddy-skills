---
name: dsh-workbuddy-connect-install
description: Install or debug the dsh-workbuddy-connect plugin for DSH (DeepSeek Harness) so WorkBuddy desktop-app models appear inside DSH. Covers version matching, the broken npm node-path workaround, profile layout, and the WorkBuddy credential-file discovery that actually decides whether the model group shows up. Use when the user pastes the corrinehu/dsh-workbuddy-connect repo or asks to connect WorkBuddy models to DSH.
---

# dsh-workbuddy-connect 安装 / 排查

`dsh-workbuddy-connect` 是 **DSH（DeepSeek Harness）的插件**，不是 WorkBuddy 的 skill。
它把本机已登录的 WorkBuddy 桌面 App 的模型接入 DSH。装了 ≠ 一定显示分组，
真正的开关是 WorkBuddy 桌面 App 的登录凭据文件。

## 0. 先读 README，确认界面与内核
- 仓库：`corrinehu/dsh-workbuddy-connect`。安装前务必读 README 的版本兼容表与 Desktop 说明。
- 任何本地文件/配置改动先备份、先征得同意（README 明确要求）。

## 1. 探测 DSH 内核版本（决定装哪个插件版）
读取全局 dsh CLI 包的版本 —— 它就是 DSH 内核版本：
- Windows：`%APPDATA%\npm\node_modules\@deepseek-ai\dsh\package.json` 的 `version`
- 也可用 `dsh --version`（若 node 在 PATH 上）。

## 2. 版本必须匹配，错配会让 DSH 启动失败
仓库 README 兼容表（以实际内核为准，npm latest 不一定合适）：

| DSH 内核 | 推荐插件版 |
|---|---|
| 0.2.0-rc.2 | 0.7.4（npm latest） |
| 0.2.0-rc.1 | 0.7.0 |
| 0.1.5 – 0.1.7 | 0.6.5 |
| 0.1.1-rc.2 | 0.2.6 |
| 0.1.2-rc.1 | 0.3.1 |
| 0.1.1-rc.2 | 0.2.6 |

例：内核 `0.1.1-rc.2` → 装 `0.2.6`，**绝不可装 0.7.4**。

## 3. 看现有 profile，避免重复/错装
DSH profiles 在 `~/.dsh/profiles/{desktop,web,headless,...}/`，每个有 `package.json`：
- `dsh` 字段里 `profile.bundles` 列出已启用的 bundle（插件名在此即已启用）。
- `dependencies` 里若已有 `dsh-workbuddy-connect` 且版本匹配 → 已装好，无需动。
- 注意桌面版由 App 管理的 profile 可能拒绝 CLI 的 `--profile desktop`（`dsh plugin add` 静默无效），
  此时改 `package.json` 后手动跑 pnpm install（见下）。

## 4. node 路径陷阱（本机实测）
某些机器 `%APPDATA%\npm\node.exe` **不存在**，导致 `dsh.ps1` / `pnpm.ps1` 包装脚本
fallback 到 PATH 里的 node（也没有）→ **静默失败、日志全空**。
绕过：用完整路径的 node 直接调 JS 入口：
- pnpm：`<node> %APPDATA%\npm\node_modules\pnpm\bin\pnpm.mjs install`（cwd = profile 目录）
- dsh：`<node> %APPDATA%\npm\node_modules\@deepseek-ai\dsh\lib\bin.js <args>`
- 本机可用的 node：`C:\Users\Administrator.USER-20250812IR\.workbuddy\binaries\node\versions\22.22.2-3\node.exe`
  （优先用 WorkBuddy 托管的 node；没有再退回系统 node）。
- PowerShell 写日志用 `2>&1 | Out-File -Encoding ascii`；`*>` 会写成 UTF-16 被 Read 当二进制。

## 5. 往某 profile 装（手动，最稳）
1. 备份该 profile 的 `package.json` / `pnpm-lock.yaml` / `cordis.yml` 等到时间戳目录。
2. 编辑 `package.json`：在 `dependencies` 加 `"dsh-workbuddy-connect": "<匹配版本>"`，
   并在 `dsh.profile.bundles` 数组里加 `"dsh-workbuddy-connect"`。
3. 在该 profile 目录跑 `pnpm install`（见第 4 节的 node 调用）。
   - 若同机其它 profile 已装过同版本，会从 pnpm store 复用，0 下载、秒级完成。
4. 验证：确认 `node_modules/dsh-workbuddy-connect/package.json` 版本、`lib/index.js`、`cordis.patch.yml` 都在，
   且 `package.json` 的 bundles 含该插件。

## 6. ★ 真正决定分组是否出现的：WorkBuddy 桌面 App 凭据文件
插件**只复用 WorkBuddy 桌面 App 的登录态**。凭据文件探测路径（读插件 `lib/host-heartbeat-*.js` 确认）：
- Windows：`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`
  以及 `%APPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`
  （产品底层框架目录叫 `CodeBuddyExtension`，不是 `WorkBuddy`）。
- macOS：`~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`
- Linux：`~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`
- 可用环境变量 `WORKBUDDY_AUTH_FILE=<绝对路径>` 覆盖。

若这些都没有 / 无可登录凭据，模型分组**不会出现** —— 与插件是否装好无关。
（README：某版 App 从未登录、也无插件自留副本时，该版分组不再显示。）

加密凭据解密需要 WorkBuddy 的 electron 可执行文件：
- 默认查 `%LOCALAPPDATA%\Programs\WorkBuddy\WorkBuddy.exe`，再查卸载注册表。
- 若 WorkBuddy 装在非默认位置（本机在 `D:\WorkBuddy\WorkBuddy.exe`），解密会失败 →
  设置 `WORKBUDDY_ELECTRON_BIN=<WorkBuddy.exe 绝对路径>`，**完全退出并重启 DSH** 才生效。
- 国际版（WorkBuddy AI）对应 `WORKBUDDY_AI_ELECTRON_BIN` / `WORKBUDDY_AI_AUTH_FILE`。

## 7. 验证（让用户自己看，因为涉及凭据，agent 不应直接读凭据文件）
- 完全退出并重启 DSH Desktop（不是 reload）→ 设置 → 插件 → workbuddy-connect 卡片：
  显示账号、令牌有效期、剩余积分即正常；若提示 “no signed-in WorkBuddy account” 即为凭据缺失。
- 模型选择器出现 “WorkBuddy” 分组（只装国内版就只验这一组），选模型发一句话能回复即成功。
- 命令行诊断（desktop profile 经 CLI 常不可用；CLI 自管 profile 可试）：
  `dsh plugin --profile <web|desktop> exec dsh-workbuddy-connect doctor`
  `dsh plugin --profile <web|desktop> exec dsh-workbuddy-connect status [--provider workbuddy-ai]`

## 8. 安全 / 沙箱提醒
- WorkBuddy 凭据文件路径**受沙箱保护**，agent 直接读取会被拦截（属正常，不要绕过）。
  让插件自己去读，不要自己去 stat/read 那个 `.info` 文件。
- 在用户 profile 目录里**删除文件**常被沙箱拦截（删除守卫），需要用户批准或让用户手动删。
- 在 profile 目录里临时放的诊断脚本（如 `peercheck.js`）务必清理，避免留在 DSH 配置目录。

## 9. 判断 peer 依赖是否满足（排查“文件在但加载失败”）
pnpm 把 `@deepseek-ai/*` 等 hoist 到共享 `.dsh/profiles/node_modules/` 或全局
`%APPDATA%\npm\node_modules\@deepseek-ai\dsh\node_modules\`。
权威验证：在目标 profile 目录放一个脚本用 `require.resolve('@deepseek-ai/dsh-llm')` 等逐个解析
（cwd = profile 目录，node 会沿 `profile/node_modules` → `profile/../node_modules` 向上找）。
`ERR_PACKAGE_PATH_NOT_EXPORTED`（如 `@earendil-works/pi-ai`）通常是裸导入被 exports 限制，非缺失；
插件用子路径 import，不影响。
