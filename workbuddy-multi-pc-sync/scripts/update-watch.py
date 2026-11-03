# -*- coding: utf-8 -*-
"""更新守护 —— 把本目录里的三个程序刷进常驻目录，并重启守护
================================================================
为什么需要它
  常驻目录（~/.workbuddy/sync-heal/）不在任何同步夹里，跨机搬不到。
  所以「程序本身的新版」要靠这条通道送过去：

      分发源 = <工作区>\\<协同目录>\\<自愈程序目录>\\   （随工作区自动同步）
      常驻   = %USERPROFILE%\\.workbuddy\\sync-heal\\

  而守护 v3 起会自己每 45 秒比对一次 md5，**发现分发源更新就自动取过来**。
  也就是说：跑过一次本脚本之后，以后再改这些程序都不必再手点。

用法
      双击同目录的 更新守护.bat        ← 装/刷新 + 重启守护
      更新守护.py --check              ← 只看不装
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import shconfig          # noqa: E402  统一配置层
import shdetect          # noqa: E402  运行期自动探测

HEAL_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "sync-heal")
FILES = ["shconfig.py", "shdetect.py", "guard.py", "heal-expert.py",
         "sync-watch.py", "自启登记.py", "同步忽略登记.py"]
LOCK = os.path.join(HEAL_DIR, ".watcher.lock")
CFG = os.path.join(HEAL_DIR, "guard-config.json")
LINE = "=" * 64


def _workspace():
    """本工具包所在工作区。

    优先取配置层；配置为空时退回「本文件往上两级」这一历史约定，
    这样两种部署方式（配置化 / 传统两层目录）都能用。
    """
    ws = shconfig.workspace()
    if ws:
        return ws
    return os.path.dirname(os.path.dirname(HERE))


def _detect_peer_and_dead():
    """(peer_id, dead_ids)，全部运行期求得，发布版里不含任何真实设备 ID。

    取不到就返回空值 —— 守卫自己还会再探测一次，不会因为这里为空而失效。
    """
    peer = shconfig.peer_id()
    dead = sorted(shconfig.dead_ids())
    if peer:
        return peer, dead
    cx = shdetect.find_config_xml(shconfig.sync_root_candidates())
    if not cx:
        return "", dead
    key = shdetect.read_api_key(cx)
    if not key:
        return "", dead
    try:
        return shdetect.peer_id(key) or "", dead
    except Exception:
        return "", dead


def out(s=""):
    try:
        print(s)
    except Exception:
        pass


def md5(p):
    try:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(65536), b""):
                h.update(c)
        return h.hexdigest()
    except Exception:
        return None


def alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def pythonw():
    exe = sys.executable or ""
    d, n = os.path.split(exe)
    w = os.path.join(d, "pythonw.exe")
    return w if os.path.isfile(w) else exe


def stop_old():
    if not os.path.isfile(LOCK):
        return None
    try:
        pid = int(open(LOCK, encoding="utf-8", errors="replace").read().strip() or 0)
    except Exception:
        return None
    if not pid or not alive(pid):
        return None
    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, creationflags=0x08000000)
    return pid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只看不装")
    a = ap.parse_args()

    out(LINE)
    out("  更新守护 · 把分发源刷进常驻目录   %s"
        % ("（只体检）" if a.check else ""))
    out("  时间    ：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    out("  主机    ：%s" % (os.environ.get("COMPUTERNAME") or "?"))
    out("  分发源  ：%s" % HERE)
    out("  常驻目录：%s" % HEAL_DIR)
    out(LINE)

    # [1] 分发源清点
    out("[1] 分发源清点")
    missing = [n for n in FILES if not os.path.isfile(os.path.join(HERE, n))]
    for n in FILES:
        p = os.path.join(HERE, n)
        out("   %-18s %s" % (n, ("%d 字节" % os.path.getsize(p))
                             if os.path.isfile(p) else "★缺"))
    if missing:
        out("   X 分发源不完整（缺 %s），请把整个「自愈程序」目录重新同步过来。"
            % ", ".join(missing))
        out("   退出码 2")
        return 2

    # [2] 常驻目录
    out("")
    out("[2] 常驻目录")
    if not os.path.isdir(HEAL_DIR):
        if a.check:
            out("   不存在（正式跑会创建）：%s" % HEAL_DIR)
        else:
            os.makedirs(HEAL_DIR, exist_ok=True)
            out("   已创建：%s" % HEAL_DIR)
    else:
        out("   已存在：%s" % HEAL_DIR)

    # [3] 逐文件比对刷入
    out("")
    out("[3] 逐文件比对（md5）")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    changed, same = [], []
    for n in FILES:
        s = os.path.join(HERE, n)
        d = os.path.join(HEAL_DIR, n)
        if os.path.isfile(d) and md5(s) == md5(d):
            same.append(n)
            out("   %-18s 一致，不动" % n)
            continue
        if a.check:
            out("   %-18s 需要更新（只体检，不动手）" % n)
            changed.append(n)
            continue
        if os.path.isfile(d):
            shutil.copy2(d, d + ".bak-" + stamp)
            out("   %-18s 旧版备份 -> %s" % (n, os.path.basename(d + ".bak-" + stamp)))
        shutil.copy2(s, d)
        out("   %-18s 已更新（%d 字节）" % (n, os.path.getsize(d)))
        changed.append(n)

    # [4] guard-config.json
    out("")
    out("[4] 守卫配置")
    if os.path.isfile(CFG):
        out("   已有：%s" % CFG)
    elif a.check:
        out("   缺（正式跑会写一份）")
    else:
        ws = _workspace()
        peer, dead = _detect_peer_and_dead()
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump({"config_xml": "", "workspace": ws, "peer_id": peer,
                       "dead_ids": dead,
                       "installed_at": datetime.datetime.now().strftime(
                           "%Y-%m-%d %H:%M:%S"),
                       "installer": "更新守护.py"}, f, ensure_ascii=False, indent=2)
        out("   已写：%s（工作区=%s%s）"
            % (CFG, ws, "，对端已探测" if peer else "，对端待守卫自行探测"))

    if a.check:
        out("")
        out(LINE)
        out("  只体检：%d 个需要更新，%d 个一致" % (len(changed), len(same)))
        out(LINE)
        return 0

    # [5] 重启守护
    out("")
    out("[5] 重启守护")
    old = stop_old()
    out("   旧守护：%s" % ("已停 PID %s" % old if old else "没在跑（或锁已失效）"))
    if not os.path.isfile(os.path.join(HEAL_DIR, "sync-watch.py")):
        out("   X 常驻目录里没有 sync-watch.py，无法启动")
        out("   退出码 2")
        return 2
    try:
        subprocess.Popen([pythonw(), os.path.join(HEAL_DIR, "sync-watch.py")],
                         cwd=HEAL_DIR, close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED|NEW_GROUP
        out("   已启动：%s" % pythonw())
    except Exception as e:
        out("   X 启动失败：%s" % e)

    # [6] 复验
    out("")
    out("[6] 复验（等 8 秒）")
    import time
    time.sleep(8)
    pid = 0
    try:
        pid = int(open(LOCK, encoding="utf-8", errors="replace").read().strip() or 0)
    except Exception:
        pass
    out("   锁文件 PID=%s  存活=%s" % (pid, alive(pid) if pid else False))
    lg = os.path.join(HEAL_DIR, "watcher-log-%s.log"
                      % (os.environ.get("COMPUTERNAME") or "unknown"))
    if os.path.isfile(lg):
        lines = open(lg, encoding="utf-8", errors="replace").read().splitlines()
        out("   守护日志尾部：")
        for l in lines[-6:]:
            out("     | " + l)
    else:
        out("   还没有守护日志（%s）" % lg)

    out("")
    out(LINE)
    out("  结论：更新 %d 个 / 一致 %d 个；守护已重启" % (len(changed), len(same)))
    out("  以后分发源一更新，守护会自己在 45 秒内取走，不必再点。")
    out(LINE)
    out("")
    out("退出码 0")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("出错：%s" % e)
        sys.exit(2)
