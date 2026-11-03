# workbuddy-skills

跨平台 AI 技能库，同时兼容 **WorkBuddy** 与 **opencode**（以及 Claude Code / Codex 等 Agent CLI）。

一共 35 个技能，涵盖 3D 建模、网文写作、通用写作、浏览器自动化、多机同步、Windows 系统辅助等场景。

## 目录结构

每个技能一个目录，目录内以 `SKILL.md`（WorkBuddy 约定）或 `skill.md`（opencode 约定）作为技能入口，两者内容一致，Windows 下为同一文件。

```
skills-shared/
├── 3dsmax-mcp/            # 3ds Max MCP 桥接
├── blender-mcp__skillhub/ # Blender MCP 桥接
├── story/                 # 网文工具箱总入口
├── story-long-write/      # 长篇写作
├── write/                 # 通用润色/去 AI 味
├── browser-cdp/           # 浏览器 CDP 自动化
└── ...
```

## 安装使用

### WorkBuddy

把本仓库内容放到（或 junction 指向）`C:\Users\<用户名>\.workbuddy\skills`：

```powershell
# 克隆
git clone <本仓库地址> skills
# 或用目录联接（保持单份、多端共享）
cmd /c mklink /J "C:\Users\<用户名>\.workbuddy\skills" "<本仓库路径>"
```

重启 WorkBuddy 后，输入 `/技能名` 或直接描述需求即可触发。

### opencode

放到（或 junction 指向）`C:\Users\<用户名>\.config\opencode\skills`：

```powershell
cmd /c mklink /J "C:\Users\<用户名>\.config\opencode\skills" "<本仓库路径>"
```

之后用 `/story`、`/story-long-write` 等斜杠命令触发。

## 技能清单

### 3D / 建模（9）

| 技能 | 用途 |
|------|------|
| `3dsmax-mcp` / `-setup` / `-dev` | 3ds Max 的 MCP 桥接、安装、开发调试 |
| `blender-mcp__skillhub` | Blender MCP 桥接（建对象、上材质） |
| `lcc2-blender-loader` / `lcc2-to-blender` | LCC2 3DGS 扫描载入 Blender |
| `cgmodel-upload-package` / `cgmodel-web-publish` | CG模型网打包上传 / 后台发布 |
| `aigei-batch-upload` | 爱给网批量上传 3D 模型 |

### 网文写作（12）

| 技能 | 用途 |
|------|------|
| `story` | 网文工具箱总入口，自动路由 |
| `story-setup` / `story-import` | 写作环境部署 / 逆向导入小说 |
| `story-long-analyze` / `-scan` / `-write` | 长篇拆文 / 扫榜 / 写作 |
| `story-short-analyze` / `-scan` / `-write` | 短篇拆文 / 扫榜 / 写作 |
| `story-deslop` / `-cover` / `-publish` / `-review` | 去 AI 味 / 封面 / 发布 / 审查 |

### 通用写作（5）

`write`、`writing-dna-skill`、`lieflat-less-ai-tone`、`impeccable`、`taste-skill`

### 同步 / 系统（3）

`workbuddy-multi-pc-sync`、`workbuddy-expert-sync`、`dsh-workbuddy-connect-install`

### Windows（3）

`windows-safe-delete-bypass`、`windows-uia-fallback-when-locked`、`localize-web-ui-zh`

### 浏览器 / 工具（2）

`browser-cdp`、`totorosir-workbuddy-score`

## 来源说明

部分技能源自开源项目（如网文系列 `zenstory-ai/oh-story-claudecode`、`writing-dna-skill` 来自 moxt.ai），具体见各技能目录内的说明文件。
