# -*- coding: utf-8 -*-
"""
多机同步 · 周检（巡检 + 修复 + 出报告）
================================================================
一次跑完，把「两台电脑之间同步还活着吗、有没有病灶」摊开讲清楚。
设计成「无人值守也能跑」：由自动化每周触发一次，直接把报告写进同步工作区，
两台机器都能看到。

检查什么（八项）
  1 同步进程与配置定位   —— syncthing.exe 是否在跑、配置文件找对了没
  2 链路                 —— 对端连接状态、地址、累计字节
  3 同步文件夹           —— state / need / errors
  4 对端完成度           —— 我这边还有多少没送过去
  5 设备登记与加密口令   —— 那条「连上就断」的老病灶（口令非空即清零）
  6 常驻守护与登录自启   —— 守护进程存活；三条自启（引擎 / 守护 / 专家自愈）能查能补
  7 专家五层登记         —— 专家在对端能不能真正被调出来
  8 近期报错             —— 最近 2 小时的断连类日志

修复策略
  · 口令 / 设备登记异常 → 交给 同步守卫.py 修（它写自己的回传日志）
  · 索引落后（need>0）  → 触发重扫
  · 登录自启丢失 / 失效 → 交给 自启登记.py 直接补回（启动器 + 两条注册表）
  · 守护没在跑          → 只报告（拉起要用户双击，沙箱内起的会被回收）
  · 专家登记缺项        → 只报告，并把修复命令打印出来

用法
  python 同步巡检.py            # 体检 + 能修的顺手修
  python 同步巡检.py --check    # 只看不动
  python 同步巡检.py --quiet    # 只打印结论段

作者：见技能 SKILL.md    首次：见技能 SKILL.md
"""

import argparse
import ctypes
import datetime
import glob
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("COMPUTERNAME") or "unknown"
HOME = os.path.expanduser("~")
SYNC_HEAL = os.path.join(HOME, ".workbuddy", "sync-heal")
# 协同目录 / 文件夹 id 一律来自配置层，发布版不含个人目录名。
try:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import shconfig as _shc
except Exception:
    _shc = None


def RELAY_REL():
    if _shc is None:
        return os.path.join("", "")
    return os.path.join(_shc.coop_dir_rel(), _shc.relay_subdir())


def FOLDER_IDS():
    if _shc is None:
        return []
    return _shc.folder_ids()


# 本机实际存在、本次要看护的夹（main() 里由守卫模块的 resolve_watched 填）。
# 各机的同步夹清单本来就可能不同，清单里本机没有的夹过滤掉，只提示、不当问题。
WATCHED = None


def watched():
    """本次要看的夹：优先用过滤后的结果，否则退回全清单。"""
    return WATCHED or FOLDER_IDS()

STALE_RELAY_HOURS = 72      # 回传日志多久没更新算「对端失联」
LOG_TAIL_HOURS = 2          # 近期报错的观察窗
ACTIVE_MINUTES = 30         # 报错最后一条若在此分钟数内，判定为「仍在刷」

LINE = "=" * 70
LINES = []
QUIET = False


def out(s=""):
    LINES.append(s)
    if not QUIET:
        print(s)


def head(n, title):
    out("")
    out("[%d] %s" % (n, title))
    out("-" * 70)


# ---------------------------------------------------------------- 模块复用
def load_guard():
    """按路径加载 同步守卫.py，复用它的定位与 REST 封装"""
    cands = [os.path.join(HERE, "同步守卫.py"),
             os.path.join(SYNC_HEAL, "guard.py"),
             os.path.join(HERE, "自愈程序", "sguard.py")]
    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("sguard_run", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m, p
        except Exception:
            continue
    return None, None


def load_config_json():
    for p in (os.path.join(SYNC_HEAL, "guard-config.json"),
              os.path.join(HERE, "guard-config.json")):
        if os.path.isfile(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                pass
    return {}


def load_autostart():
    """按路径加载 自启登记.py（中文模块名走路径加载，避开编码坑）"""
    cands = [os.path.join(HERE, "自启登记.py"),
             os.path.join(SYNC_HEAL, "自启登记.py"),
             os.path.join(HERE, "自愈程序", "自启登记.py")]
    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("zgh_autostart", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m, p
        except Exception:
            continue
    return None, None


def load_sync_ignore():
    """按路径加载 同步忽略登记.py（补 Syncthing 忽略规则，防跨机冲突）"""
    cands = [os.path.join(HERE, "同步忽略登记.py"),
             os.path.join(SYNC_HEAL, "同步忽略登记.py"),
             os.path.join(HERE, "自愈程序", "同步忽略登记.py")]
    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("zgh_stignore", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m, p
        except Exception:
            continue
    return None, None


# ---------------------------------------------------------------- 单项检查
def is_alive(pid):
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def count_syncthing():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq syncthing.exe",
                            "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, errors="replace",
                           creationflags=0x08000000, timeout=30)
        rows = [l for l in (r.stdout or "").splitlines() if "syncthing" in l.lower()]
        return len(rows), rows
    except Exception as e:
        return -1, [str(e)]


def recent_errors(logfile):
    if not logfile or not os.path.isfile(logfile):
        return None, "找不到 syncthing.log"
    now = datetime.datetime.now()
    cut = (now - datetime.timedelta(hours=LOG_TAIL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    n_cc, last = 0, None
    try:
        lines = open(logfile, encoding="utf-8", errors="replace").read().splitlines()
    except Exception as e:
        return None, "读日志失败：%s" % e
    for l in lines:
        if l[:19] < cut:
            continue
        if ("without the device info for the remote" in l
                or "missing in cluster config" in l):
            n_cc += 1
            last = l[:19]
    quiet_min = None
    if last:
        try:
            quiet_min = (now - datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                         ).total_seconds() / 60.0
        except Exception:
            quiet_min = None
    return {"cc": n_cc, "last": last, "quiet_min": quiet_min,
            "window": LOG_TAIL_HOURS}, None


def relay_freshness(ws):
    """回传日志目录里，**对端写的**最新文件有多旧（小时）。本机自己写的不算。"""
    d = os.path.join(ws or "", RELAY_REL())
    if not os.path.isdir(d):
        return None, "目录不存在：%s" % d
    newest, name = None, None
    mine = 0
    for n in os.listdir(d):
        fp = os.path.join(d, n)
        if not os.path.isfile(fp):
            continue
        if HOST in n:          # 本机自己写的，不算对端回传
            mine += 1
            continue
        mt = os.path.getmtime(fp)
        if newest is None or mt > newest:
            newest, name = mt, n
    if newest is None:
        return {"none": True, "mine": mine}, None
    age = (time_now() - newest) / 3600.0
    return {"age_hours": age, "name": name, "mine": mine}, None


def time_now():
    return datetime.datetime.now().timestamp()


# 登录自启的检查与自愈已收进 自启登记.py（见 load_autostart）——
# 旧版这里只读不写，发现没登记也只能提示人去点，无法自愈，已废弃。


def expert_status():
    """调 专家自愈.py --check，取退出码与关键行"""
    cands = [os.path.join(HERE, "专家自愈.py"),
             os.path.join(SYNC_HEAL, "heal-expert.py")]
    p = next((x for x in cands if os.path.isfile(x)), None)
    if not p:
        return None, "找不到 专家自愈.py"
    try:
        r = subprocess.run([sys.executable, p, "--check", "--no-log"],
                           capture_output=True, text=True, errors="replace",
                           encoding="utf-8", timeout=300,
                           creationflags=0x08000000)
        tail = [l.strip() for l in (r.stdout or "").splitlines()
                if l.strip() and ("✓" in l or "★" in l or "处待处理" in l
                                  or "已完整" in l or "运行副本" in l or "源包" in l)]
        return {"code": r.returncode, "tail": tail[-8:]}, None
    except Exception as e:
        return None, "调专家自愈失败：%s" % e


# ---------------------------------------------------------------- 主流程
def main():
    global QUIET
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只体检，不做任何修改")
    ap.add_argument("--quiet", action="store_true", help="只打印结论段")
    args = ap.parse_args()
    dry = args.check

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    issues = []          # 需要人处理的
    fixed = []           # 本轮已修的
    notable = []         # 提示但不算问题

    out(LINE)
    out("  多机同步 · 周检        %s" % ("（只体检）" if dry else "（体检并修复）"))
    out("  主机：%s      时间：%s" % (HOST, ts))
    out("  Python：%s" % sys.executable)
    out("  本脚本：%s" % os.path.abspath(__file__))
    out(LINE)

    g, gpath = load_guard()
    gcfg = load_config_json()
    ws = gcfg.get("workspace") or ""

    # ---- 1 进程与配置 -------------------------------------------------
    head(1, "同步进程与配置定位")
    n, rows = count_syncthing()
    if n <= 0:
        out("   ★ 没有发现 syncthing.exe —— 同步没有在跑")
        issues.append("Syncthing 进程不在运行（双击同步启动器，或重启电脑等自启）")
    else:
        out("   syncthing.exe 进程数：%d %s" % (n, "（正常父子进程对）" if n == 2 else ""))
        for l in rows:
            # CSV 里内存形如 "18,380 K"，数字自带逗号，不能按逗号切
            parts = [x.strip('"') for x in l.strip().split('","')]
            if len(parts) >= 5:
                out("      PID %-8s 内存 %s" % (parts[1], parts[4].strip('"')))
    if not g:
        out("   ★ 加载不到 同步守卫.py，后续体检无法进行")
        issues.append("找不到 同步守卫.py（工作区是否完整同步过来了？）")
        report(ws, dry, issues, fixed, notable, gcfg)
        return 2
    out("   守卫模块：%s" % gpath)

    cfg, why = g.resolve_config()
    if not cfg or not os.path.isfile(cfg):
        out("   ★ 定位不到 config.xml")
        issues.append("定位不到 Syncthing 的 config.xml")
        report(ws, dry, issues, fixed, notable, gcfg)
        return 2
    out("   配置文件：%s" % cfg)
    out("   来源　　：%s" % why)

    # ---- 2 链路 -------------------------------------------------------
    head(2, "链路")
    key = ""
    try:
        key = g.read_key(cfg)
    except Exception as e:
        out("   ★ 读 API Key 失败：%s" % e)
        issues.append("读不到 Syncthing API Key")
    snap, err = g.inspect(key) if key else (None, "无 API Key")
    if not snap:
        out("   ★ 连不上本机管理接口（端口 8384）：%s" % err)
        issues.append("本机 Syncthing 管理接口 8384 不可达：%s" % err)
        report(ws, dry, issues, fixed, notable, gcfg)
        return 2
    my_id = snap["my_id"]
    peer = snap["peer"]
    out("   本机身份：%s" % my_id)
    out("   对端身份：%s" % peer)

    global WATCHED
    try:
        WATCHED, _absent = g.resolve_watched(key)
    except Exception:
        WATCHED, _absent = None, []
    out("   同步文件夹：%d 个" % len([f for f in snap["folders"]
                                       if f.get("id") in watched()]))
    if _absent:
        out("   本机没有这几个夹，本次不查（各机清单本就可能不同）：%s"
            % "、".join(_absent))

    code, conns = g.apij("GET", "/rest/system/connections", key)
    peer_ok, peer_addr, peer_in = False, "-", 0
    for d, v in ((conns or {}).get("connections") or {}).items():
        if d == peer:
            peer_ok = bool(v.get("connected"))
            peer_addr = v.get("address") or "-"
            peer_in = v.get("inBytesTotal") or 0
    if peer_ok:
        out("   对端连接：connected=True   %s" % peer_addr)
    else:
        out("   ★ 对端连接：connected=False —— 两台没有握上手")
        issues.append("对端未连接：确认对端开机、装了 Syncthing 且在跑、两个 ID 都还在设备表里")
    pend = (conns or {}).get("pending") or []
    if pend:
        out("   待配对设备：%d 个（多余条目，建议在 GUI 里丢弃）" % len(pend))
        notable.append("有 %d 个待配对设备" % len(pend))

    # ---- 3 四夹 -------------------------------------------------------
    head(3, "同步文件夹")
    out("   %-18s %-8s %-7s %-7s %-6s %s" % ("文件夹", "状态", "local", "global", "need", "错误"))
    for fid in watched():
        code, s = g.apij("GET", "/rest/db/status?folder=" + fid, key)
        if code != 200 or not s:
            out("   %-18s ★ 读状态失败 HTTP %s" % (fid, code))
            issues.append("%s 读状态失败" % fid)
            continue
        need = s.get("needFiles") or 0
        er = s.get("errors") or 0
        out("   %-18s %-8s %-7s %-7s %-6s %s" % (
            fid, s.get("state"), s.get("localFiles"), s.get("globalFiles"), need, er))
        if er:
            issues.append("%s 有 %s 个错误（看 GUI 的「失败的项目」）" % (fid, er))
        if need and not dry:
            c2, _ = g.apij("POST", "/rest/db/scan?folder=" + fid, key)
            if c2 == 200:
                fixed.append("%s 触发重扫（need=%s）" % (fid, need))
                out("      -> 已触发重扫")
        elif need:
            notable.append("%s 待同步 %s 项（只体检未处理）" % (fid, need))

    # ---- 4 完成度 -----------------------------------------------------
    head(4, "对端完成度")
    _ws_folder = (watched() or ["my-workspace"])[-1]
    code, comp = g.apij("GET", "/rest/db/completion?folder=%s&device=%s"
                        % (_ws_folder, peer), key)
    if code == 200 and comp:
        nb = comp.get("needBytes") or 0
        out("   工作区：%.2f%%   待传 %s 字节 / 共 %s 字节"
            % (comp.get("completion", 0), nb, comp.get("globalBytes")))
        if peer_ok and (comp.get("completion") or 0) < 99.5:
            notable.append("工作区对端尚未传完（当前 %.2f%%），数据正在传输"
                           % comp.get("completion", 0))
        elif peer_ok and nb:
            out("      （待传 %s 字节是正在途中的小文件，属正常）" % nb)
    else:
        out("   取不到完成度数据（HTTP %s）" % code)

    # ---- 5 设备登记与口令 ---------------------------------------------
    head(5, "设备登记与加密口令")
    probs, todo = g.analyse(snap)
    if not probs:
        out("   四个文件夹的设备登记都正常（口令长度 0、无废弃设备）")
    else:
        for p in probs:
            out("   ★ %s" % p)
            issues.append(str(p))
    if gcfg.get("check_report_shown") is not None:
        pass
    if todo and dry:
        out("   -> 有 %d 处可自动修（本次只体检，未动手）" % len(todo))
    elif todo:
        r = subprocess.run([sys.executable, gpath, "--report", "--quiet"],
                           capture_output=True, text=True, errors="replace",
                           encoding="utf-8", timeout=300,
                           creationflags=0x08000000)
        out("   -> 已调同步守卫处理：退出码 %s（0=无需处理 / 10=已修 / 2=出错）"
            % r.returncode)
        if r.returncode == 10:
            fixed.append("设备登记异常已由同步守卫修好")
        elif r.returncode == 2:
            issues.append("同步守卫执行出错，请看 回传日志\\同步守卫日志-%s.md" % HOST)
        snap2, _ = g.inspect(key)
        if snap2:
            p2, t2 = g.analyse(snap2)
            out("   复验：%s" % ("已恢复正常" if not t2 and not p2
                                else "仍有 %d 处待处理" % max(len(t2), len(p2))))

    # ---- 6 常驻守护与登录自启 -----------------------------------------
    head(6, "常驻守护与登录自启（三条）")
    pid, alive = 0, False
    lock = os.path.join(SYNC_HEAL, ".watcher.lock")
    if os.path.isfile(lock):
        try:
            pid = int(open(lock, encoding="utf-8").read().strip() or 0)
            alive = is_alive(pid)
        except Exception:
            pass
    out("   守护进程：%s" % ("存活（PID %s）" % pid if alive else "★ 未在运行"))
    if not alive:
        notable.append("守护进程当前没跑（下次登录会自动起来；想立刻起就双击 自愈程序\\更新守护.bat）")

    am, apath = load_autostart()
    if not am:
        out("   ★ 找不到 自启登记.py，登录自启这条既没法核也没法自愈")
        issues.append("找不到 自启登记.py（登录自启无法自动补登记）")
    else:
        ar = am.audit(dry=dry, here=HERE)
        for x in ar["ok"]:
            out("   · %s" % x)
        for x in ar["fixed"]:
            out("   ✓ 已修：%s" % x)
            fixed.append(x)
        for x in ar["issues"]:
            out("   ★ %s" % x)
            issues.append(x)
        for x in ar["notes"]:
            out("   · %s" % x)

    # ---- 7 专家五层登记 -----------------------------------------------
    head(7, "专家五层登记")
    ex, eerr = expert_status()
    if not ex:
        out("   ★ %s" % eerr)
        notable.append("专家登记没查成：%s" % eerr)
    else:
        for l in ex["tail"]:
            out("   %s" % l)
        if ex["code"] == 0:
            out("   结论：五层登记齐全，专家可用")
        elif ex["code"] == 10:
            out("   结论：★ 有缺项（退出码 10）")
            issues.append("专家五层登记有缺项 —— 跑 python 专家自愈.py 补齐，然后重启 WorkBuddy")
        else:
            out("   结论：★ 自愈脚本报错（退出码 %s）" % ex["code"])
            issues.append("专家自愈脚本报错，退出码 %s" % ex["code"])

    # ---- 8 近期报错 ---------------------------------------------------
    head(8, "近期报错（最近 %d 小时）" % LOG_TAIL_HOURS)
    logf = os.path.join(os.path.dirname(cfg), "syncthing.log")
    re_, rerr = recent_errors(logf)
    if not re_:
        out("   %s" % rerr)
    elif re_["cc"] == 0:
        out("   最近 %d 小时内无断连类报错" % LOG_TAIL_HOURS)
    else:
        q = re_["quiet_min"]
        if q is not None and q <= ACTIVE_MINUTES:
            out("   ★ 断连类报错 %d 条，最后一条 %s（%.0f 分钟前，仍在刷）"
                % (re_["cc"], re_["last"], q))
            issues.append("断连类报错仍在新增（共 %d 条，最后一条 %.0f 分钟前）"
                          " —— 典型病灶是设备登记，见 [5]" % (re_["cc"], q))
        else:
            out("   断连类报错 %d 条，最后一条 %s%s —— 已经停了"
                % (re_["cc"], re_["last"],
                   "（%.0f 分钟前）" % q if q is not None else ""))
            out("      历史遗留，已被后续修复消解，无需处理。")
            notable.append("日志里留有 %d 条历史断连报错（已停止）" % re_["cc"])

    # ---- 回传日志新鲜度 -----------------------------------------------
    head(9, "对端回传日志")
    rf, rferr = relay_freshness(ws)
    if not rf:
        out("   %s" % rferr)
    elif rf.get("none"):
        out("   ★ 目录里没有对端写的日志（本机自己写了 %d 份）" % rf.get("mine", 0))
        notable.append("还没有看到对端回传的日志 —— 对端的守护可能还没跑起来"
                       "（在那台机器上双击 自愈程序\\更新守护.bat 即可）")
    else:
        age = rf["age_hours"]
        out("   对端最新：%s   （%.1f 小时前）" % (rf["name"], age))
        out("   本机自己写的：%d 份" % rf.get("mine", 0))
        if age > STALE_RELAY_HOURS:
            notable.append("对端回传日志已 %.0f 小时没更新（对端可能长期关机，或守护没跑）" % age)

    # ---- 同步忽略规则（本机专属标记）---------------------------------
    head(10, "同步忽略规则（本机专属标记）")
    sim, simpath = load_sync_ignore()
    if not sim:
        out("   找不到 同步忽略登记.py（工作区是否完整同步过来了？）")
        notable.append("同步忽略规则没核成：找不到 同步忽略登记.py")
    else:
        try:
            sr = sim.audit(dry=dry)
            for x in sr["ok"]:
                out("   ✓ %s" % x)
            for x in sr["fixed"]:
                out("   · %s" % x)
                fixed.append(x)
            for x in sr["issues"]:
                out("   ！%s" % x)
                issues.append(x)
            for x in sr["notes"]:
                out("   · %s" % x)
        except Exception as e:
            out("   ！检查出错：%r" % (e,))
            notable.append("同步忽略规则没核成：%r" % (e,))

    report(ws, dry, issues, fixed, notable, gcfg)
    return 10 if issues else 0


def report(ws, dry, issues, fixed, notable, gcfg):
    out("")
    out(LINE)
    out("  结论")
    out(LINE)
    if not issues and not fixed and not notable:
        out("   ✅ 全部正常：两机链路通、四个文件夹干净、设备登记无口令、专家登记齐全。")
    else:
        if fixed:
            out("   本轮已自动修复 %d 项：" % len(fixed))
            for x in fixed:
                out("      · %s" % x)
        if notable:
            out("   提示（不影响同步）：")
            for x in notable:
                out("      · %s" % x)
        if issues:
            out("   ★ 需要处理 %d 项：" % len(issues))
            for x in issues:
                out("      · %s" % x)
        else:
            out("   无需人工处理。")
    out(LINE)

    # 落盘
    d = os.path.join(ws or "", RELAY_REL())
    if not ws or not os.path.isdir(ws):
        sys.stdout.write("  （工作区不在本机，报告未落盘）\n")
        return
    os.makedirs(d, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    fp = os.path.join(d, "同步巡检-%s-%s.md" % (HOST, ts))
    body = ["# 多机同步 · 周检报告",
            "",
            "- 主机：`%s`" % HOST,
            "- 时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "- 模式：%s" % ("只体检" if dry else "体检并修复"),
            "- 结论：%s" % ("全部正常" if not issues else "需处理 %d 项" % len(issues)),
            "",
            "```text"] + LINES + ["```", ""]
    try:
        with open(fp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(body))
        sys.stdout.write("  报告已写：%s\n" % fp)
    except Exception as e:
        sys.stdout.write("  ★ 报告写入失败：%s\n" % e)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
