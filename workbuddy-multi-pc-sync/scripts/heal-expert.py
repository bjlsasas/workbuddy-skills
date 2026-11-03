# -*- coding: utf-8 -*-
"""
自建专家 · 登记自愈脚本
================================================================
解决一个具体的坑：
    Syncthing 只负责把「文件」搬过去。但专家要在 WorkBuddy 里真正
    "活起来"，还要依赖三处**本机登记**，而这三处不进同步夹、也
    不能跨机复制（里面的路径带用户名）：

        ① 源包        plugins/marketplaces/my-experts/plugins/<专家>/
        ② 运行副本    plugins/cache/my-experts/<专家>/<版本>/      ← 真正被加载的
        ③ 安装清单    plugins/installed_plugins.json               ← 含绝对路径
        ④ 市场注册    plugins/known_marketplaces.json
        ⑤ 启用开关    settings.json 的 enabledPlugins

    所以：**哪台机器收到了变动，就要在哪台机器补登记。**
    本脚本就是那个补登记的动作，可反复跑、幂等、改动前自动备份。

顺手还核 ⑥ 登录自启（引擎 / 常驻守护 / 本脚本自己）
    这三条被安全软件或「开机加速」清掉时不会有任何提示，表现就是
    「某天开始两边不同步了」。既然本脚本本来就靠登录自启被拉起来，
    就让它在开机自检时把这三条一并核了、缺了补上。

双向可用
    主力机 <-> 笔记本 都能跑。在笔记本上调优了专家，文件会同步回
    主力机，但主力机的 ③④⑤ 不会自动变 —— 登录时跑一次本脚本即可。

用法
    python 专家自愈.py            体检并自动补齐（WorkBuddy 在跑时弹窗提醒重启）
    python 专家自愈.py --check    只看不动
    python 专家自愈.py --silent   补齐但不弹窗（适合无人值守）
    python 专家自愈.py --force    连运行副本也强制从源包重建（修复用）
    python 专家自愈.py --no-log   不写共享日志（调试用）

退出码
    0 = 无需改动（或检查模式无待办）
    10 = 已改动，需要重启 WorkBuddy 生效
    1 = 出错
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

# pythonw.exe 下没有 stdout，先兜住，否则 print 直接抛异常
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


# 市场名由配置层给出（缺省 my-experts）。
# 注意：本脚本从「源包目录」里**逐个发现**专家，不写死任何专家名，
# 所以这里不需要 EXPERT_NAME —— 原来那个常量定义了却从未被读取，
# 反而携带了专属标识，已移除。
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shconfig          # noqa: E402  统一配置层

MARKET_NAME = shconfig.market_name()
APP_EXE = "WorkBuddy.exe"

# 比对完整性时忽略的东西（同步工具自己的元数据、缓存、冲突副本）
SKIP_DIRS = {".stfolder", ".stversions", ".stignore", ".git", "__pycache__",
             ".in_use", "node_modules"}
SKIP_MARK = (".sync-conflict-",)


TS = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
HOST = os.environ.get("COMPUTERNAME") or "unknown-host"

changes = []      # 本次实际做的改动
notices = []      # 只报告、不动手的事
problems = []     # 处理不了的事


def log(msg=""):
    try:
        print(msg)
        sys.stdout.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 路径

def wb_root():
    """WorkBuddy 配置目录。测试时可用环境变量指到假树。"""
    ov = os.environ.get("ZGH_WB_HOME")
    if ov:
        return ov
    return os.path.join(os.path.expanduser("~"), ".workbuddy")


class Ctx(object):
    def __init__(self):
        self.WB = wb_root()
        self.PLUGINS = os.path.join(self.WB, "plugins")
        self.MARKET = os.path.join(self.PLUGINS, "marketplaces", MARKET_NAME)
        self.SRC = os.path.join(self.MARKET, "plugins")
        self.CACHE = os.path.join(self.PLUGINS, "cache", MARKET_NAME)
        self.INSTALLED = os.path.join(self.PLUGINS, "installed_plugins.json")
        self.KNOWN = os.path.join(self.PLUGINS, "known_marketplaces.json")
        self.SETTINGS = os.path.join(self.WB, "settings.json")
        self.SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 小工具

def rd(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def wr(path, obj, dry):
    if dry:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak-" + TS)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def semkey(v):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", str(v))[:4]]


def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def norm(p):
    return os.path.normcase(os.path.normpath(p or ""))


def file_set(root):
    """目录内真实文件集合（相对 posix 路径），排除同步元数据与冲突副本"""
    out = set()
    if not os.path.isdir(root):
        return out
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if any(m in f for m in SKIP_MARK):
                continue
            rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
            out.add(rel)
    return out


def find_version(pkg_dir):
    for rel in (".codebuddy-plugin/plugin.json", "plugin.json",
                ".codebuddy-plugin/marketplace.json"):
        p = os.path.join(pkg_dir, rel)
        d = rd(p)
        if isinstance(d, dict):
            for k in ("version", "pluginVersion"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            for it in (d.get("plugins") or []):
                if isinstance(it, dict) and it.get("version"):
                    return str(it["version"]).strip()
    return None


def app_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + APP_EXE],
                             capture_output=True, text=True, errors="ignore",
                             timeout=20).stdout or ""
        return APP_EXE.lower() in out.lower()
    except Exception:
        return False


def notify(title, text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x00000040 | 0x00001000)
    except Exception:
        pass


# ---------------------------------------------------------------- 登录自启

def load_autostart():
    """按路径加载 自启登记.py（中文模块名走路径加载，避开编码坑）"""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    sh = os.path.join(os.path.expanduser("~"), ".workbuddy", "sync-heal")
    for p in (os.path.join(here, "自启登记.py"),
              os.path.join(sh, "自启登记.py"),
              os.path.join(here, "自愈程序", "自启登记.py")):
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
    """按路径加载 同步忽略登记.py（补 Syncthing 忽略规则，防跨机冲突）

    ★ 为什么要多加两条「工作区」候选（2026-09-21 踩过的鸡生蛋）：
      本脚本在 sync-heal\\ 里跑，而新模块第一次是靠 sync-watch 的复制列表铺过来的；
      若对端跑的还是**旧守护**（列表里没有新模块），这里就会找不到、静默跳过。
      而模块在工作区里一定有（跟着工作区同步夹一起同步），所以拿工作区兜底。
    """
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    sh = os.path.join(os.path.expanduser("~"), ".workbuddy", "sync-heal")
    cands = [os.path.join(here, "同步忽略登记.py"),
             os.path.join(sh, "同步忽略登记.py"),
             os.path.join(here, "自愈程序", "同步忽略登记.py")]
    # 工作区兜底：日志目录环境变量（守护注入）及其上一级，
    # 外加配置层解析出来的协同目录 —— 不再写死任何个人路径。
    for base in (os.environ.get("ZGH_SELFHEAL_LOGDIR"),
                 os.environ.get("ZGH_WORKSPACE"),
                 shconfig.coop_dir()):
        if not base:
            continue
        cands.append(os.path.join(base, "同步忽略登记.py"))
        cands.append(os.path.join(base, os.pardir, "同步忽略登记.py"))
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


# ---------------------------------------------------------------- 共享日志

def write_shared_log(lines):
    """写在本脚本旁边（工作区里），随同步回到另一台机器，便于两侧互查"""
    d = os.environ.get("ZGH_SELFHEAL_LOGDIR") or Ctx().SCRIPT_DIR
    if not os.path.isdir(d):
        return
    p = os.path.join(d, "自愈日志-%s.md" % HOST)
    try:
        new = os.path.exists(p)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            if not new:
                f.write("# 专家自愈日志 · %s\n\n"
                        "> 本文件由 `专家自愈.py` 自动追加，随工作区同步回另一台机器。\n"
                        "> 有新增段落 = 该机器曾自动补过专家登记。\n\n" % HOST)
            f.write("## %s\n\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            for l in lines:
                f.write("- %s\n" % l)
            f.write("\n")
    except Exception as e:
        log("  (共享日志写入失败：%s)" % e)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--check", action="store_true", help="只看不动")
    ap.add_argument("--silent", action="store_true", help="改动后不弹窗")
    ap.add_argument("--force", action="store_true", help="运行副本强制从源包重建")
    ap.add_argument("--no-log", action="store_true", help="不写共享日志")
    args = ap.parse_args()
    dry = args.check

    cx = Ctx()

    log("=" * 66)
    log("  专家登记自愈 · %s · %s" % (HOST, "只检查" if dry else "自动补齐"))
    log("  配置目录：%s" % cx.WB)
    log("=" * 66)
    log("")

    if not os.path.isdir(cx.WB):
        log("！没找到 WorkBuddy 配置目录：%s" % cx.WB)
        return 1

    # ---------- ① 源包 ----------
    log("【① 源包】")
    if not os.path.isdir(cx.SRC):
        log("  ！源包目录不存在：%s" % cx.SRC)
        log("    等同步把 wb-experts-market 传过来。")
        return 1
    names = sorted(d for d in os.listdir(cx.SRC)
                   if os.path.isdir(os.path.join(cx.SRC, d)))
    if not names:
        log("  ！源包里没有专家")
        return 1
    for n in names:
        log("  ✓ %s" % n)
    log("")

    # ---------- ② 运行副本 ----------
    log("【② 运行副本（真正被加载的）】")
    if not dry:
        os.makedirs(cx.CACHE, exist_ok=True)
    plan = []
    for n in names:
        src = os.path.join(cx.SRC, n)
        ver = find_version(src)
        if not ver:
            msg = "%s 读不到版本号，跳过" % n
            problems.append(msg)
            log("  ！%s" % msg)
            continue
        dst = os.path.join(cx.CACHE, n, ver)
        src_files = file_set(src)

        # 冲突副本体检（只报告，不删）
        conf = 0
        for root in (src, dst):
            if os.path.isdir(root):
                for dp, dn, fn in os.walk(root):
                    conf += sum(1 for f in fn if ".sync-conflict-" in f)
        if conf:
            notices.append("%s 下有 %d 个同步冲突副本（未删，需人工判断）" % (n, conf))
            log("  ! %s 下有 %d 个 .sync-conflict 副本" % (n, conf))

        missing = src_files - file_set(dst)
        need_copy = args.force or (not os.path.isdir(dst)) or bool(missing)
        why = []
        if args.force:
            why.append("强制重建")
        if not os.path.isdir(dst):
            why.append("副本不存在")
        elif missing:
            why.append("副本缺 %d 个文件（同步未完成）" % len(missing))
        log("  %s %-28s 版本 %-9s %s" % ("需重建" if need_copy else "已完整", n, ver,
                                         ("；".join(why) or "-")))
        if need_copy and not dry:
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*SKIP_DIRS,
                                                              "*.sync-conflict-*"))
            except Exception as e:
                msg = "复制 %s -> %s 失败：%s" % (src, dst, e)
                problems.append(msg)
                log("      ！%s" % msg)
                continue
            log("      ✓ 已从源包重建（%d 个文件）" % len(src_files))
        if need_copy:
            changes.append("重建运行副本 %s@%s（%s）" % (n, ver, "；".join(why) or "-"))
        plan.append((n, ver, dst))
    log("")

    # ---------- ④ 市场注册 ----------
    log("【④ 市场注册 known_marketplaces.json】")
    known = rd(cx.KNOWN, {}) or {}
    if MARKET_NAME in known:
        loc = (known[MARKET_NAME] or {}).get("installLocation", "")
        if norm(loc) == norm(cx.MARKET):
            log("  ✓ 已登记，路径正确")
        else:
            log("  · 路径需修正")
            log("      原：%s" % (loc or "（空）"))
            log("      新：%s" % cx.MARKET)
            known[MARKET_NAME]["installLocation"] = cx.MARKET
            known[MARKET_NAME]["source"] = {"source": "directory", "path": cx.MARKET}
            changes.append("修正市场登记路径")
            wr(cx.KNOWN, known, dry)
    else:
        log("  · 未登记，正在补")
        known[MARKET_NAME] = {
            "manifestName": MARKET_NAME,
            "type": "directory",
            "source": {"source": "directory", "path": cx.MARKET},
            "installLocation": cx.MARKET,
            "description": "%s marketplace (auto-generated)" % MARKET_NAME,
            "lastUpdated": utcnow_iso(),
            "autoUpdate": True,
        }
        changes.append("补登记市场 %s" % MARKET_NAME)
        wr(cx.KNOWN, known, dry)
    log("")

    # ---------- ③ 安装清单 ----------
    log("【③ 安装清单 installed_plugins.json】")
    inst = rd(cx.INSTALLED, {}) or {}
    inst.setdefault("version", 2)
    inst.setdefault("plugins", {})
    dirty = False
    for n, ver, dst in plan:
        key = "%s@%s" % (n, MARKET_NAME)
        # 这条在清单里有两种存法：list（带历史）或 dict（单条）。
        # 实测两种都会出现 —— WorkBuddy 应用自己重写过的键是 dict，
        # 其余键是 list。读的时候两种都要认，写回时保持原形态，
        # 不去改应用自己的约定（否则双方来回改写）。
        raw = inst["plugins"].get(key)
        if isinstance(raw, list):
            e = dict(raw[0]) if raw else {}
            as_list = True
        elif isinstance(raw, dict):
            e = dict(raw)
            as_list = False
        else:
            e = {}
            as_list = True
        if norm(e.get("installPath")) == norm(dst) and e.get("version") == ver:
            log("  ✓ %-34s 已正确（%s）" % (key, ver))
            continue
        log("  · %-34s 需更新" % key)
        log("      原：%s  @%s" % (e.get("installPath") or "（空）", e.get("version") or "-"))
        log("      新：%s  @%s" % (dst, ver))
        e.update({
            "scope": "user",
            "installPath": dst,
            "version": ver,
            "installedAt": e.get("installedAt") or
            utcnow_iso(),
            "lastUpdated": utcnow_iso(),
        })
        inst["plugins"][key] = [e] if as_list else e
        changes.append("更新安装清单 %s → %s" % (key, ver))
        dirty = True
    if dirty:
        wr(cx.INSTALLED, inst, dry)
    # 只报告：清单里有、源包已无的专家（不自动摘除，避免误删）
    for key in list(inst["plugins"].keys()):
        if key.endswith("@" + MARKET_NAME):
            n = key.split("@")[0]
            if n not in names:
                notices.append("清单里还有 %s，但源包已无此专家（未自动摘除）" % key)
    log("")

    # ---------- ⑤ 启用开关 ----------
    log("【⑤ 启用开关 settings.json】")
    st = rd(cx.SETTINGS, {}) or {}
    ep = st.setdefault("enabledPlugins", {})
    touched = False
    for n, ver, dst in plan:
        key = "%s@%s" % (n, MARKET_NAME)
        if ep.get(key) is True:
            log("  ✓ %-34s 已启用" % key)
        else:
            log("  · %-34s 需启用" % key)
            ep[key] = True
            changes.append("启用 %s" % key)
            touched = True
    if touched:
        wr(cx.SETTINGS, st, dry)
    log("")

    # ---------- ⑥ 登录自启登记 ----------
    # 专家登记修好了，但如果「开机谁来跑这个脚本」这条自启被清了，
    # 下次开机就没人修了 —— 一并核、一并补。
    log("【⑥ 登录自启（引擎 / 常驻守护 / 本脚本）】")
    am, apath = load_autostart()
    if not am:
        log("  ！找不到 自启登记.py，这条跳过")
        notices.append("登录自启没能核（找不到 自启登记.py）")
    else:
        ar = am.audit(dry=dry, here=cx.SCRIPT_DIR)
        for x in ar["ok"]:
            log("  ✓ %s" % x)
        for x in ar["fixed"]:
            log("  · %s" % x)
            changes.append(x)
        for x in ar["issues"]:
            log("  ！%s" % x)
            notices.append(x)
        for x in ar["notes"]:
            log("  · %s" % x)
    log("")

    # ---------- ⑦ 同步忽略规则（本机专属标记，防跨机冲突）----------
    # 运行副本夹里有 .in_use / .orphaned_at / .created-by-session 这类本机专属标记，
    # 跨机同步毫无意义，还会因进程号撞名产生冲突副本；
    # 严重点会让整个文件夹同步失败（对端删、本机那份正被占用）。
    # .stignore 不跨机传输，所以每台机器都得各自有一份 —— 这里负责补。
    log("【⑦ 同步忽略规则（本机专属标记）】")
    sim, sipath = load_sync_ignore()
    if not sim:
        log("  · 找不到 同步忽略登记.py，这条跳过")
    else:
        try:
            sr = sim.audit(dry=dry)
            for x in sr["ok"]:
                log("  ✓ %s" % x)
            for x in sr["fixed"]:
                log("  · %s" % x)
                changes.append(x)
            for x in sr["issues"]:
                log("  ！%s" % x)
                notices.append(x)
            for x in sr["notes"]:
                log("  · %s" % x)
        except Exception as e:
            log("  ！同步忽略规则检查出错：%r" % (e,))
            notices.append("同步忽略规则没核成：%r" % (e,))
    log("")

    # ---------- 汇总 ----------
    log("=" * 66)
    if dry:
        log("  体检完成，%d 处待处理" % len(changes))
    else:
        log("  自愈完成，%d 处已修改" % len(changes))
    for c in changes:
        log("    · %s" % c)
    if notices:
        log("")
        log("  仅报告（未动手）：")
        for x in notices:
            log("    · %s" % x)
    if problems:
        log("")
        log("  处理不了：")
        for x in problems:
            log("    ！%s" % x)
    log("=" * 66)

    if changes and not dry:
        # 共享日志：随工作区同步回另一台，便于两侧互查
        if not args.no_log:
            write_shared_log(["主机：`%s`" % HOST, "配置目录：`%s`" % cx.WB, ""] +
                             ["- " + c for c in changes])
        running = app_running()
        if not args.silent:
            body = "专家登记已自动补齐（%d 处）。\n\n" % len(changes)
            for c in changes[:6]:
                body += "  · %s\n" % c
            if len(changes) > 6:
                body += "  · 其余 %d 处见日志\n" % (len(changes) - 6)
            body += "\n"
            if running:
                body += "WorkBuddy 正在运行 —— 请完全退出后重新打开，新版才会生效。"
            else:
                body += "WorkBuddy 尚未启动，直接打开即可生效。"
            notify("专家登记自愈", body)
        log("")
        log("≥ 需要重启 WorkBuddy 才会生效。")
        return 10

    if dry and changes:
        return 10
    log("")
    log("一切正常，无需改动。")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
