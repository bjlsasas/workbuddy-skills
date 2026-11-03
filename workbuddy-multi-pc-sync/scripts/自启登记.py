# -*- coding: utf-8 -*-
"""
登录自启登记 —— 检查 + 自愈（主力机 / 笔记本通用）
================================================================
本机需要三条「登录自启」。缺任何一条，同步链就有一段失效：

    A  启动文件夹\\启动同步-Syncthing.bat      拉起同步引擎 syncthing.exe
    B  注册表 HKCU\\...\\Run  ZGH-SyncHeal      拉起常驻守护 sync-watch.py
    C  注册表 HKCU\\...\\Run  ZGH-ExpertSelfHeal 拉起专家登记自愈（开机自检）

为什么要有这个模块
    「登录自启」是登记在系统里的，不是程序自己记得的。某些安全软件、
    「开机加速」「启动项优化」会把它清掉，而清掉之后不会有任何提示 ——
    表现就是「某天开始两边不同步了」，用户完全看不出原因。
    所以：**能查、能补，且由两个入口分别兜底**（周检、每次开机）。

设计纪律
    · 启动器必须是**纯 ASCII**：cmd 按字节偏移跳转，非 ASCII 会把行劈两半。
    · 注册表值写成 `"pythonw.exe" "脚本.py"`，两段都加引号（路径含中文与空格）。
    · 判「是否要修」看**指向的脚本文件是否真的存在**，而不是字符串是否等于
      某个固定值 —— 否则两台机器各自算出的「标准答案」不同，会来回改写。
    · 拿不到目标路径时（找不到 syncthing.exe 等）**只报告、绝不乱写**。

返回
    audit(dry=True) -> {"ok": [...], "fixed": [...], "issues": [...], "notes": [...]}
        dry=True   只体检，零写入
        dry=False  能补的补上（写启动器 / 写注册表）
"""

import os
import re
import sys

try:
    if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import shconfig as _shc
except Exception:
    _shc = None

APP_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 自启项名 / 启动器名：保留通用缺省，但可由配置覆盖，
# 避免同一台机器上两套无关部署撞名。
ITEM_GUARD = (_shc.get("autostart_guard_item") if _shc else None) or "ZGH-SyncHeal"
ITEM_EXPERT = (_shc.get("autostart_expert_item") if _shc else None) or "ZGH-ExpertSelfHeal"
LAUNCHER_NAME = (_shc.get("launcher_name") if _shc else None) or "启动同步-Syncthing.bat"

# 找不到 syncthing.exe 时的已知候选：由配置层给出，为空则交运行期探测。
_FALLBACK = (_shc.sync_root_candidates() if _shc else [])
FALLBACK_SYNC_ROOTS = list(_FALLBACK)


# ------------------------------------------------------------------ 基础
def startup_dir():
    return os.path.join(os.environ.get("APPDATA", ""),
                        "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def pythonw():
    """优先 pythonw.exe：开机自启不该弹黑窗"""
    exe = sys.executable or ""
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def launcher_root(bat):
    """从启动器正文里读出它写的 SYNC_ROOT"""
    try:
        with open(bat, "r", encoding="ascii", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.upper().startswith("SET SYNC_ROOT="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return None


def sync_root():
    """定位 Syncthing 安装目录：环境变量 → 已有启动器 → 已知候选 → 运行中进程"""
    v = (os.environ.get("WBSYNC") or "").strip().strip('"')
    if v and os.path.isfile(os.path.join(v, "syncthing.exe")):
        return v

    bat = os.path.join(startup_dir(), LAUNCHER_NAME)
    if os.path.isfile(bat):
        p = launcher_root(bat)
        if p and os.path.isfile(os.path.join(p, "syncthing.exe")):
            return p

    for c in FALLBACK_SYNC_ROOTS:
        if os.path.isfile(os.path.join(c, "syncthing.exe")):
            return c

    # 运行期自动探测（枚举进程拿 --home / 可执行路径）。
    # 走在 wmic 之前 —— wmic 在不少环境里已被策略拉黑，而这条路走 Win32 API，
    # 不依赖任何外部命令。
    try:
        _sd = os.path.join(os.path.dirname(os.path.abspath(__file__)))
        if _sd not in sys.path:
            sys.path.insert(0, _sd)
        import shdetect
        h = shdetect.sync_home_from_process()
        if h and os.path.isfile(os.path.join(h, "syncthing.exe")):
            return h
        for _n, _ppid, _nm, _p in shdetect.running_processes("syncthing"):
            if _p and _nm.lower() == "syncthing.exe" and os.path.isfile(_p):
                return os.path.dirname(_p)
    except Exception:
        pass

    try:
        import subprocess
        r = subprocess.run(
            ["wmic", "process", "where", "name='syncthing.exe'",
             "get", "ExecutablePath", "/value"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in (r.stdout or "").splitlines():
            k, _, val = line.partition("=")
            if k.strip().lower() == "executablepath" and val.strip():
                p = val.strip()
                if os.path.isfile(p):
                    return os.path.dirname(p)
    except Exception:
        pass
    return None


def launcher_text(root):
    """登录启动器正文 —— 必须是纯 ASCII"""
    nl = "\r\n"
    return nl.join([
        "@echo off",
        "rem ============================================================",
        "rem  Multi-PC Sync launcher (Syncthing)",
        "rem  Started automatically at logon. Keep this file. Do not move it.",
        "rem ============================================================",
        "set SYNC_ROOT=" + root,
        "",
        'if not exist "%SYNC_ROOT%\\syncthing.exe" exit /b 1',
        "",
        "rem Skip if already running",
        'tasklist /FI "IMAGENAME eq syncthing.exe" 2>nul | find /I "syncthing.exe" >nul',
        "if not errorlevel 1 exit /b 0",
        "",
        'start "" /min "%SYNC_ROOT%\\syncthing.exe" serve --home="%SYNC_ROOT%\\data" '
        "--no-browser --no-restart",
        "exit /b 0",
        "",
    ])


# ------------------------------------------------------------------ 注册表
def reg_get(name):
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_RUN_KEY)
        try:
            return winreg.QueryValueEx(k, name)[0]
        except FileNotFoundError:
            return None
    except Exception:
        return None


def reg_set(name, value):
    import winreg
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_RUN_KEY, 0,
                           winreg.KEY_SET_VALUE)
    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    winreg.CloseKey(k)


def _reg_points_to_existing(cur):
    """注册表值形如 "pythonw.exe" "脚本.py" —— 只要第 2 段指向的脚本真存在就算有效"""
    if not cur:
        return None
    parts = re.findall(r'"([^"]+)"', cur)
    if len(parts) >= 2 and os.path.isfile(parts[1]):
        return parts[1]
    return None


# ------------------------------------------------------------------ 主入口
def audit(dry=True, here=None):
    """检查（并可选修复）三条登录自启。返回 dict。"""
    here = here or os.path.dirname(os.path.abspath(__file__))
    wb = os.path.join(os.path.expanduser("~"), ".workbuddy")
    sh = os.path.join(wb, "sync-heal")

    ok, fixed, issues, notes = [], [], [], []
    pw = pythonw()

    # ---------- A 启动器 ----------
    # 判据只看「能不能真的拉起同步」：启动器在 + 它写的 SYNC_ROOT 下确有
    # syncthing.exe。不比对字节 —— 注释文字差几个字不该触发重写。
    root = sync_root()
    sdir = startup_dir()
    bat = os.path.join(sdir, LAUNCHER_NAME)
    lroot = launcher_root(bat) if os.path.isfile(bat) else None
    if lroot and os.path.isfile(os.path.join(lroot, "syncthing.exe")):
        ok.append("同步引擎启动器已登记（%s → %s）" % (LAUNCHER_NAME, lroot))
    elif not root:
        notes.append("找不到 syncthing.exe 所在目录，登录启动器没法核（不影响当前正在跑的同步）")
    elif not root.isascii():
        notes.append("Syncthing 目录含非 ASCII 字符（%s），启动器有字节偏移风险，不自动改写" % root)
    elif dry:
        issues.append("同步引擎启动器缺失或指向无效：%s" % bat)
    else:
        try:
            os.makedirs(sdir, exist_ok=True)
            with open(bat, "w", encoding="ascii", newline="") as f:
                f.write(launcher_text(root))
            fixed.append("重建登录启动器 %s（指向 %s）" % (LAUNCHER_NAME, root))
        except Exception as e:
            issues.append("写启动器失败：%s" % e)

    # ---------- B / C 注册表两条 ----------
    cands = [
        (ITEM_GUARD, "常驻守护",
         [os.path.join(sh, "sync-watch.py"),
          os.path.join(here, "自动同步守护.py"),
          os.path.join(here, "自愈程序", "sync-watch.py")]),
        (ITEM_EXPERT, "专家登记自愈",
         [os.path.join(here, "专家自愈.py"),
          os.path.join(sh, "heal-expert.py"),
          os.path.join(here, "自愈程序", "heal-expert.py")]),
    ]
    for key, label, paths in cands:
        cur = reg_get(key)
        if _reg_points_to_existing(cur):
            ok.append("%s 已登记登录自启" % label)
            continue
        target = next((p for p in paths if os.path.isfile(p)), None)
        if not target:
            issues.append("%s 登录自启缺失，且找不到可挂的脚本" % label)
            continue
        if not os.path.isfile(pw):
            issues.append("%s 需要补登记，但找不到 pythonw.exe" % label)
            continue
        want = '"%s" "%s"' % (pw, target)
        if dry:
            issues.append("%s 登录自启缺失或已失效，可补为：%s" % (label, want))
        else:
            try:
                reg_set(key, want)
                fixed.append("补登录自启 %s → %s" % (label, os.path.basename(target)))
            except Exception as e:
                issues.append("写注册表 %s 失败：%s" % (key, e))

    return {"ok": ok, "fixed": fixed, "issues": issues, "notes": notes}


def summary_lines(r):
    lines = []
    for x in r.get("ok", []):
        lines.append("  OK   %s" % x)
    for x in r.get("fixed", []):
        lines.append("  已修 %s" % x)
    for x in r.get("issues", []):
        lines.append("  ★    %s" % x)
    for x in r.get("notes", []):
        lines.append("  注   %s" % x)
    return lines


if __name__ == "__main__":
    d = "--check" in sys.argv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("登录自启登记 · %s" % ("只体检" if d else "体检并修复"))
    print("-" * 60)
    res = audit(dry=d)
    for l in summary_lines(res):
        print(l)
    print("-" * 60)
    print("结论：%d 项正常，%d 项已修，%d 项待处理"
          % (len(res["ok"]), len(res["fixed"]), len(res["issues"])))
    sys.exit(10 if res["fixed"] else (2 if res["issues"] else 0))
