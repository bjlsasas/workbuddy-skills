# -*- coding: utf-8 -*-
"""受控实验：证明「自启自愈」真的能补回来。

做法：备份 → 故意打坏两条（删一条注册表项、清掉启动器内容）
     → 跑 自启登记.py（修复模式）→ 校验恢复 → 无条件兜底还原。
"""
import os
import subprocess
import sys
import winreg

sys.stdout.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
HERE = os.path.join(_ROOT, "scripts")
if not os.path.isdir(HERE):
    HERE = _ROOT
MOD = os.path.join(HERE, "自启登记.py")
PY = sys.executable

ITEM = "ZGH-ExpertSelfHeal"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SF = os.path.join(os.environ["APPDATA"],
                  r"Microsoft\Windows\Start Menu\Programs\Startup")
BAT = os.path.join(SF, "启动同步-Syncthing.bat")

fail = []


def get(name):
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY)
        try:
            return winreg.QueryValueEx(k, name)[0]
        except FileNotFoundError:
            return None
    except Exception:
        return None


def put(name, value):
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    winreg.CloseKey(k)


print("=" * 66)
print("  受控实验：自启自愈能力验证")
print("=" * 66)

# ---- 0 备份 ----
orig_reg = get(ITEM)
orig_bat = open(BAT, "rb").read() if os.path.isfile(BAT) else None
print("\n[0] 备份原始状态")
print("    注册表 %s = %s" % (ITEM, (orig_reg or "（无）")[:80]))
print("    启动器 %d 字节" % (len(orig_bat) if orig_bat else 0))
if not orig_reg:
    print("    ！原本就没有这条，实验无意义，退出")
    sys.exit(1)

# ---- 1 打坏 ----
print("\n[1] 故意打坏两条")
k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE)
winreg.DeleteValue(k, ITEM)
winreg.CloseKey(k)
print("    已删除注册表项 %s   现在=%s" % (ITEM, get(ITEM)))
BAD = "@echo off\r\nrem deliberately broken by test\r\nset SYNC_ROOT=\r\n"
with open(BAT, "w", encoding="ascii", newline="") as f:
    f.write(BAD)
print("    已把启动器改成无效内容（SYNC_ROOT 空）")

# ---- 2 跑修复 ----
print("\n[2] 跑 自启登记.py（修复模式）")
r = subprocess.run([PY, MOD], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", cwd=HERE)
print(r.stdout.strip())
print("    退出码 =", r.returncode)

# ---- 3 校验 ----
print("\n[3] 校验是否恢复")
now_reg = get(ITEM)
now_bat = open(BAT, encoding="ascii", errors="ignore").read() if os.path.isfile(BAT) else ""
reg_ok = bool(now_reg and "pythonw" in now_reg.lower())
bat_ok = "SYNC_ROOT=E:" in now_bat and "syncthing.exe" in now_bat
print("    注册表 %s 恢复：%s  -> %s" % (ITEM, "✓" if reg_ok else "★", (now_reg or "（仍空）")[:80]))
print("    启动器内容有效：%s" % ("✓" if bat_ok else "★"))
if not reg_ok:
    fail.append("注册表项没补回来")
if not bat_ok:
    fail.append("启动器没重建")

# ---- 4 兜底还原 ----
print("\n[4] 兜底：确保回到原始可用状态")
if not reg_ok and orig_reg:
    put(ITEM, orig_reg)
    print("    已手工写回原注册表值")
if orig_bat is not None and not bat_ok:
    with open(BAT, "wb") as f:
        f.write(orig_bat)
    print("    已手工写回原启动器")

print("\n[5] 终态核对")
print("    注册表 =", (get(ITEM) or "（无）")[:80])
print("    启动器 =", os.path.isfile(BAT) and open(BAT, encoding="ascii", errors="ignore").read().count("syncthing.exe"), "处提到 syncthing.exe")
print("\n" + "=" * 66)
print("结论：%s" % ("★ 有失败：" + "；".join(fail) if fail else "两条都自动补回，自启自愈有效 ✓"))
print("=" * 66)
sys.exit(1 if fail else 0)
