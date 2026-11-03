# -*- coding: utf-8 -*-
"""
自动同步守护 v3
================================================================
跨机同步只能搬「文件」。还有两件本机私事，必须由「收到变动的那台」自己做：

  ① 专家登记 —— 专家要在 WorkBuddy 里真正活起来，还差本机三处登记
     （安装清单 / 市场注册 / 启用开关）。这几处不进同步夹。
  ② 文件夹登记 —— 同步夹里某条设备登记一旦带上 encryptionPassword，
     Syncthing 就会在发集群配置时**把那条整条跳过**，对端秒断连接。
     2026-09-19 那场「连上 2 秒就断、0 字节」的真凶。

v2 与 v1 的差别
  · 每 45 秒先跑**同步守卫**：体检四个文件夹的设备登记与加密口令，发现异常即修
  · 守卫与自愈的日志，都**回传到同步工作区的回传目录**，
    这样两台机器互相都能看到对方做了什么，不必再靠人报
  · 自愈调用时注入 ZGH_SELFHEAL_LOGDIR，让它把日志也写到回传目录
  · 其余不变：无窗口、单实例、指纹不动就不干活

v3 与 v2 的差别
  · 新增**自更新**：常驻目录不在任何同步夹里，跨机搬不到，所以守护每轮
    去同步工作区里的**分发源目录**（见配置 `coop_dir` / `dist_subdir`）取新版，
    比 md5、变了才写、编译不过不采纳。以后再改这些程序，不必再让人手点。
  · 守卫的中文输出经管道捕获后不再乱码（强制 UTF-8）

设计要点
  · 无窗口：由 pythonw.exe 启动，不弹黑框
  · 单实例：锁文件 + 进程存活校验，重复启动会自己退出
  · 只在「真的变了」时才干活：先算目录指纹，指纹不动就不跑专家自愈
  · 全程写日志：~/.workbuddy/sync-heal/watcher-log-<主机名>.log

装法（由同目录的 自启登记.py 完成）：
    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 下
    <自启项名> = "<pythonw.exe>" "<本文件>"
"""

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import shconfig          # noqa: E402  统一配置层

WB = os.path.join(os.path.expanduser("~"), ".workbuddy")
HOST = os.environ.get("COMPUTERNAME") or "unknown"
LOG = os.path.join(HERE, "watcher-log-%s.log" % HOST)
LOCK = os.path.join(HERE, ".watcher.lock")
CFG_JSON = os.path.join(HERE, "guard-config.json")


def _find(ascii_name, cn_name):
    for n in (ascii_name, cn_name):
        p = os.path.join(HERE, n)
        if os.path.isfile(p):
            return p
    return os.path.join(HERE, ascii_name)


HEAL = _find("heal-expert.py", "专家自愈.py")
GUARD = _find("guard.py", "同步守卫.py")

# 自更新分发源（相对工作区）：常驻目录本身不在任何同步夹里，跨机搬不到。
# 目录名由配置层给出，发布版里不含任何个人目录名。
def dist_dir_in(ws):
    if not ws:
        return ""
    return os.path.join(ws, shconfig.coop_dir_rel(), shconfig.dist_subdir())

WATCH = [
    os.path.join(WB, "plugins", "marketplaces", "my-experts"),
    os.path.join(WB, "plugins", "cache", "my-experts"),
    os.path.join(WB, "skills"),
]

SKIP = {".git", "__pycache__", "node_modules", ".stfolder", ".stversions", ".in_use"}
INTERVAL = 45
PID = os.getpid()

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


def cfg():
    try:
        return json.load(open(CFG_JSON, encoding="utf-8"))
    except Exception:
        return {}


def relay_dir():
    """回传日志目录：同步工作区里，两机都能看到"""
    ws = cfg().get("workspace") or shconfig.workspace() or ""
    if not ws or not os.path.isdir(ws):
        return None
    return os.path.join(ws, shconfig.coop_dir_rel(), shconfig.relay_subdir())


def log(msg):
    line = "%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def relay(kind, lines):
    """往同步工作区追加一条回传记录（kind=守卫 / 自愈 / 守护）"""
    d = relay_dir()
    if not d:
        return
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "%s-%s.md" % (kind, HOST))
        new = not os.path.isfile(p)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write("# %s回传日志 · %s\n\n"
                        "> 由本机常驻守护自动追加，随工作区同步回另一台。\n\n" % (kind, HOST))
            f.write("## %s\n\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            for l in lines:
                f.write("- %s\n" % l)
            f.write("\n")
    except Exception as e:
        log("回传日志写入失败：%s" % e)


def _md5(p):
    try:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _compiles(p):
    """只接受能编译通过的版本，绝不把坏文件推成常驻程序。"""
    try:
        with open(p, "r", encoding="utf-8") as f:
            compile(f.read(), p, "exec")
        return True
    except Exception as e:
        log("自更新跳过（编译不通过）：%s -> %s" % (os.path.basename(p), e))
        return False


def maybe_selfupdate():
    """
    自更新。常驻目录（本文件所在处）不在任何同步夹里，跨机搬不到，
    所以由守护自己从**同步工作区的分发源**取新版：

        分发源 = <工作区>\\<协同目录>\\<自愈程序目录>\\

      · guard.py / heal-expert.py —— 每轮都被新进程拉起，复制过去下一轮即生效
      · sync-watch.py（守护自身） —— 复制过去，**下次登录/启动**才生效

    只比 md5、只在真变了才写；改前自动 .bak；编译不过的一律不采纳。
    """
    ws = cfg().get("workspace") or ""
    if not ws:
        return []
    d = dist_dir_in(ws)
    if not os.path.isdir(d):
        return []
    me = os.path.basename(os.path.abspath(__file__))
    hits = []
    # shconfig / shdetect 必须在列 —— sync-watch 自己 import 它们，
    # 少一个守护就会在 import 阶段直接死掉。
    for src_name, dst_name in (("shconfig.py", "shconfig.py"),
                               ("shdetect.py", "shdetect.py"),
                               ("guard.py", os.path.basename(GUARD)),
                               ("heal-expert.py", os.path.basename(HEAL)),
                               ("sync-watch.py", me),
                               ("自启登记.py", "自启登记.py"),
                               ("同步忽略登记.py", "同步忽略登记.py")):
        src = os.path.join(d, src_name)
        dst = os.path.join(HERE, dst_name)
        if not os.path.isfile(src):
            continue
        a = _md5(src)
        if a is None or a == _md5(dst):
            continue
        if not _compiles(src):
            continue
        try:
            if os.path.isfile(dst):
                shutil.copy2(dst, dst + ".bak-" +
                             datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(src, dst)
            hits.append((src_name, "下一轮生效" if dst_name != me else "下次启动生效"))
        except Exception as e:
            log("自更新失败（%s）：%s" % (src_name, e))
    if hits:
        log("程序自更新：" + "；".join("%s（%s）" % h for h in hits))
        relay("程序自更新",
              ["来源：`%s`" % d] + ["· `%s` —— %s" % (n, w) for n, w in hits])
    return hits


def pid_alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def single_instance():
    try:
        if os.path.isfile(LOCK):
            old = int(open(LOCK).read().strip() or 0)
            if old and old != PID and pid_alive(old):
                log("已有守护实例在跑（PID %s），本进程退出" % old)
                return False
    except Exception:
        pass
    try:
        with open(LOCK, "w") as f:
            f.write(str(PID))
    except Exception:
        pass
    return True


def fingerprint():
    """目录指纹：文件数 + 最大修改时间 + 总字节数"""
    n = 0
    mx = 0.0
    total = 0
    for root in WATCH:
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root, onerror=lambda e: None):
            dn[:] = [d for d in dn if d not in SKIP]
            for f in fn:
                if ".sync-conflict-" in f:
                    continue
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                except Exception:
                    continue
                n += 1
                total += st.st_size
                if st.st_mtime > mx:
                    mx = st.st_mtime
    return (n, round(mx, 3), total)


def run_guard():
    """跑同步守卫。退出码 10 = 修好了东西，才写回传与主日志。"""
    if not os.path.isfile(GUARD) or not os.path.isfile(CFG_JSON):
        return
    try:
        p = subprocess.run([sys.executable, GUARD, "--quiet"],
                           capture_output=True, text=True, errors="replace",
                           timeout=180, creationflags=0x08000000)
    except Exception as e:
        log("守卫执行异常：%s" % e)
        return
    if p.returncode == 10:
        hits = [l.strip(" ·-") for l in (p.stdout or "").splitlines()
                if l.strip().startswith("·")][:8]
        log("守卫已修复：%s" % (" | ".join(hits) if hits else "(无明细)"))
        relay("同步守卫", ["结果：**已自动修复同步夹登记**"] +
              (["· " + h for h in hits] if hits else []))
    elif p.returncode == 2:
        log("守卫报错（退出码 2）：%s" % (p.stdout or "")[-200:].replace("\n", " "))


def run_heal(reason):
    if not os.path.isfile(HEAL):
        log("跳过：找不到 %s" % HEAL)
        return
    env = dict(os.environ)
    d = relay_dir()
    if d:
        env["ZGH_SELFHEAL_LOGDIR"] = d
    try:
        p = subprocess.run([sys.executable, HEAL, "--silent"],
                           capture_output=True, text=True, errors="replace",
                           encoding="utf-8",
                           timeout=300, env=env, creationflags=0x08000000)
        tail = [l for l in (p.stdout or "").splitlines()
                if l.strip().startswith(("·", "✓", "！", "≥"))][:8]
        log("自愈（%s）退出码 %s %s" % (reason, p.returncode,
                                       ("| " + " / ".join(tail)) if tail else ""))
        # 自愈自己会把明细写进 ZGH_SELFHEAL_LOGDIR（就是回传目录），这里不重复写。
    except Exception as e:
        log("自愈执行异常：%s" % e)


def main():
    if not single_instance():
        return 0
    log("守护 v3 启动 PID=%s  监视 %d 个目录，间隔 %ss" % (PID, len(WATCH), INTERVAL))
    log("守卫：%s   自愈：%s   回传：%s" % (
        "OK" if os.path.isfile(GUARD) else "缺",
        "OK" if os.path.isfile(HEAL) else "缺",
        relay_dir() or "(无)"))
    maybe_selfupdate()
    run_guard()
    run_heal("开机首次")
    last = fingerprint()
    log("初始指纹 %s" % (last,))
    while True:
        time.sleep(INTERVAL)
        try:
            maybe_selfupdate()
            run_guard()
            cur = fingerprint()
            if cur != last:
                log("检测到变动 %s -> %s" % (last, cur))
                run_heal("目录变动")
                last = fingerprint()
        except Exception as e:
            log("巡检异常：%s" % e)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
