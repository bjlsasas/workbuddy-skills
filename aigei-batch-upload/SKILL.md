---
name: aigei-batch-upload
description: 在爱给网（aigei.com）批量上传 3D 模型作品包的标准操作流程（windows-mcp 真机操控）。当用户要把本地 zip 模型包批量上传到爱给网个人中心（/home/work/upload），或需要排查「提交后遮罩不消失 / 计数不更新 / 疑似重复上传」类异常时使用。
agent_created: true
---

# 爱给网批量上传 SOP

## 前置准备
- 批次目录：`...\_batches\bXX\`（内含硬链 zip）
- 预览图目录：`bXXpv\`（图片按 zip 基名重命名，如 `模型名.jpg`、`模型名_1.jpg` …，用于同名自动匹配）

## 固定参数（按已确认的模板）
- 分类：3D → 3D模型；作品集：地铁
- 发布类型：**商用授权售卖**（选中后页面会展开说明区：认证签约 / 自由定价 / 收益 5 成 → 可据此确认「已选中」）
- 标题 = zip 基名（不要含品牌字样，防侵权）
- 描述后缀：`，轨道交通车站建筑模型，3ds Max 2025 制作，标准材质，贴图相对路径引用可直接打开；压缩包内含 max + fbx + 贴图（平级结构）。`
- 关键词（最多 5 个）：`地铁站 车站模型 轨道交通 站厅 建筑模型`
- 注意：上传页**没有**价格字段；定价在作品管理侧设置。

## 操作步骤（windows-mcp）
1. 打开 `https://www.aigei.com/home/work/upload`
2. 点 3D 类型入口 → 点「商用授权售卖」
3. 点上传区打开文件对话框 → 地址栏填批次目录 → **点文件列表区聚焦** → `ctrl+a` → 回车 → 生成 N 条
4. 点「批量选取预览图/视频」→ 填 bXXpv 目录 → 全选回车 → 弹窗「匹配成功 N 个预览」点确定
5. 点「批量修改描述和关键词」→ 描述「添加后缀」填后缀→应用；关键词输入框填空格分隔关键词 →「批量添加」
6. `ctrl+End` 滚到底 → 点「提交」
7. **成功判据：页面弹出「共 N 个作品上传成功！审核员将于…人工审核」弹窗** → 点「关闭」

## 定位技巧（推荐，比截图估坐标可靠）
用 PowerShell + UIAutomationClient 直接读元素屏幕坐标：
```powershell
Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::RootElement
$c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,'个人中心 - Google Chrome')
$win=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,$c)
# 按 Name / ControlType 找元素 → $e.Current.BoundingRectangle（已是屏幕物理坐标，无缩放）
```
参考坐标（1920x1080，随滚动变化，务必现查）：发布类型「商用授权售卖」文字 ≈ (428,177)；滚到底后「提交」≈ (952,710)；成功弹窗「关闭」≈ (1119,691)。

## 上传框/批量按钮超出视口且页面滚不动时的解法（高频坑）
选中「商用授权售卖」会展开说明区，把上传框和批量操作按钮推到视口下方，而页面此时**已无法继续滚动**（滚轮 / PageDown / ctrl+End 都无效）。
解法（已验证）：把页面缩到 80% —— `Set-Clipboard` 无关，直接用
```powershell
Add-Type -AssemblyName System.Windows.Forms
$ws=New-Object -ComObject wscript.shell; $ws.AppActivate('个人中心 - Google Chrome') | Out-Null
[System.Windows.Forms.SendKeys]::SendWait('^-')   # 执行 2 次到 80%
```
元素随之上移全部可见；全部操作完成后 `ctrl+0` 恢复 100%。
**缩放后必须用 UIA 重新现查坐标**（缩放前后坐标不同）。

## 文件对话框多选（中文路径用剪贴板，勿用 Type 中文）
```powershell
Set-Clipboard -Value 'E:\...\_batches\bXX'      # 目录路径
# 点对话框「文件名」框 (694,477) → SendKeys '^a' '^v' '{ENTER}'  → 导航到目录
# 点文件列表区 (600,250) → SendKeys '^a' '{ENTER}'              → 全选并确认
```
对话框控件定位：枚举 `#32770` → `Edit`(文件名框, rect 206,469,1182,486)、`ToolbarWindow32`(地址栏，文本含当前目录)、`Button`(打开/取消)。

## 已实测关键坐标（1920x1080，80% 缩放态，仍以 UIA 现查为准）
「点击上传」(1003,510) / (1003,944 未缩放态)｜批量选取预览图/视频 (411,625)｜批量修改描述和关键词 (645,625，**有时要点两次才展开**)｜添加后缀框 (593,720)｜应用 (482,792)｜关键词框 (1171,744)｜批量添加 (1003,766)｜提交 (952,768)｜匹配成功弹窗「继续」(1086,450)｜上传成功弹窗「关闭」(1086,546)。
提交成功后页面**保留**分类/发布类型/作品集配置，仅清空文件列表 → 可连续跑下一批。
预览图按同名自动匹配，实测 b07=119 / b08=120 / b09=80 张。

## 坑 / 教训（务必遵守）
1. **不要用「提交后立刻核验审计计数」判断成功** —— 计数有延迟（提交时锁定、转码排队后才计数），会把成功误判为失败 → 整批重做 → 作品重复。2026-10-10 b06 事故：审计 70→100（净 +30，多传一遍），即因误判所致。以「共 N 个作品上传成功！」弹窗为唯一准据。
2. 提交后出现「提交中」遮罩长时间不消失 **≠** 失败；先等弹窗。
3. 文件对话框粘贴路径后不自动关闭：先点文件列表区获得焦点，再 `ctrl+a` / 回车。
4. 同一个 zip 不要重复选中（会产生重复条目）。
5. 用户要求：不来回改线上字段、不反复试错（消耗积分）。

## 入站核验（非成功判据，仅事后确认）
左侧菜单「转码处理中 (n)」「人工审核中 (n)」计数 —— 提交后等数分钟再看。
