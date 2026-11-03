# -*- coding: utf-8 -*-
"""
专家自愈.py 的隔离自测。
在临时目录里造一棵「假的 WorkBuddy 配置树」，把各种坏状态摆进去，
验证自愈能真的修好、幂等、只检查模式不动手、冲突副本不误删。

跑法：python 制作脚本/test-专家自愈.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HEAL = os.path.join(ROOT, "scripts", "heal-expert.py")
if not os.path.isfile(HEAL):
    HEAL = os.path.join(ROOT, "heal-expert.py")
PY = sys.executable

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  | " + str(extra)) if extra else ""))


def build_tree(root, *, src_ver, cache_vers, stale_path, known_path,
               enabled=False, partial_cache=False, conflict=False):
    P = os.path.join(root, "plugins")
    market = os.path.join(P, "marketplaces", "my-experts")
    src = os.path.join(market, "plugins", "demo-expert")
    os.makedirs(os.path.join(src, ".codebuddy-plugin"), exist_ok=True)
    os.makedirs(os.path.join(src, "agents"), exist_ok=True)
    os.makedirs(os.path.join(src, "references"), exist_ok=True)
    with open(os.path.join(src, ".codebuddy-plugin", "plugin.json"), "w",
              encoding="utf-8") as f:
        json.dump({"name": "demo-expert", "version": src_ver}, f)
    open(os.path.join(src, "agents", "demo-expert.md"), "w",
         encoding="utf-8").write("# agent")
    for i in range(5):
        open(os.path.join(src, "references", "r%d.md" % i), "w",
             encoding="utf-8").write("ref %d" % i)
    if conflict:
        open(os.path.join(src, "references",
                          "r0.sync-conflict-20260101-000000.md"), "w",
             encoding="utf-8").write("dup")
    os.makedirs(os.path.join(market, ".codebuddy-plugin"), exist_ok=True)
    with open(os.path.join(market, ".codebuddy-plugin", "marketplace.json"), "w",
              encoding="utf-8") as f:
        json.dump({"name": "my-experts"}, f)

    for v in cache_vers:
        d = os.path.join(P, "cache", "my-experts", "demo-expert", v)
        # 优先照抄源包全部文件（真实 cache 就是这样），partial 时再抽掉几个
        if os.path.isdir(src):
            shutil.copytree(src, d, dirs_exist_ok=True)
        else:
            os.makedirs(d, exist_ok=True)
        os.makedirs(os.path.join(d, "agents"), exist_ok=True)
        os.makedirs(os.path.join(d, "references"), exist_ok=True)
        if not os.path.exists(os.path.join(d, "agents", "demo-expert.md")):
            open(os.path.join(d, "agents", "demo-expert.md"), "w",
                 encoding="utf-8").write("# agent")
        if partial_cache:
            for i in range(2, 5):
                fp = os.path.join(d, "references", "r%d.md" % i)
                if os.path.exists(fp):
                    os.remove(fp)

    with open(os.path.join(P, "installed_plugins.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 2, "plugins": {
            "demo-expert@my-experts": [{
                "scope": "user",
                "installPath": stale_path,
                "version": os.path.basename(stale_path.rstrip("\\/")),
            }]}}, f, ensure_ascii=False, indent=2)

    with open(os.path.join(P, "known_marketplaces.json"), "w", encoding="utf-8") as f:
        json.dump({"my-experts": {"manifestName": "my-experts",
                                  "installLocation": known_path}}, f,
                  ensure_ascii=False, indent=2)

    with open(os.path.join(root, "settings.json"), "w", encoding="utf-8") as f:
        ep = {}
        if enabled:
            ep["demo-expert@my-experts"] = True
        json.dump({"enabledPlugins": ep}, f, ensure_ascii=False, indent=2)
    return P, src


def run_heal(wb, logdir, *args):
    env = dict(os.environ)
    env["ZGH_WB_HOME"] = wb
    env["ZGH_SELFHEAL_LOGDIR"] = logdir
    r = subprocess.run([PY, HEAL, "--silent"] + list(args), env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def snapshot(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            fp = os.path.join(dp, f)
            try:
                out[os.path.relpath(fp, root)] = open(fp, "rb").read()
            except Exception:
                pass
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    tmp = tempfile.mkdtemp(prefix="heal_test_")
    logdir = os.path.join(tmp, "logs")
    os.makedirs(logdir)
    try:
        print("=" * 70)
        print("  场景 A：版本已升到 1.40.0，但登记还是 1.39.0；副本目录缺失")
        print("=" * 70)
        wbA = os.path.join(tmp, "A", ".workbuddy")
        P, src = build_tree(
            wbA, src_ver="1.40.0", cache_vers=["1.39.0"],
            stale_path=r"C:\Users\SomeoneElse\.workbuddy\plugins\cache"
                       r"\my-experts\demo-expert\1.39.0",
            known_path=r"C:\Users\SomeoneElse\.workbuddy\plugins\marketplaces\my-experts",
            enabled=False, conflict=True)

        rc, out = run_heal(wbA, logdir, "--check")
        before = snapshot(wbA)
        print(out)
        ck("只检查模式：退出码=10", rc == 10, rc)
        ck("只检查模式：一个字节都没写", snapshot(wbA) == before)

        rc, out = run_heal(wbA, logdir)
        print(out)
        ck("修复模式：退出码=10", rc == 10, rc)

        newcache = os.path.join(P, "cache", "my-experts",
                                "demo-expert", "1.40.0")
        ck("运行副本 1.40.0 已建出", os.path.isdir(newcache))
        if os.path.isdir(newcache):
            def fs(r):
                s = set()
                for dp, dn, fn in os.walk(r):
                    for f in fn:
                        if ".sync-conflict-" in f:
                            continue
                        s.add(os.path.relpath(os.path.join(dp, f), r))
                return s
            ck("副本文件集 == 源包文件集", fs(newcache) == fs(src),
               "差=%s" % (fs(src) ^ fs(newcache)))
        ck("旧副本 1.39.0 原样保留",
           os.path.isdir(os.path.join(P, "cache", "my-experts",
                                      "demo-expert", "1.39.0")))

        inst = json.load(open(os.path.join(P, "installed_plugins.json"), encoding="utf-8"))
        e = inst["plugins"]["demo-expert@my-experts"][0]
        import os.path as _p
        ck("清单 installPath 指向本机新副本",
           _p.normcase(_p.normpath(e["installPath"])) == _p.normcase(_p.normpath(newcache)),
           e["installPath"])
        ck("清单 version = 1.40.0", e["version"] == "1.40.0", e["version"])
        ck("清单已留 .bak 备份",
           any("installed_plugins.json.bak-" in f for f in os.listdir(P)))

        kn = json.load(open(os.path.join(P, "known_marketplaces.json"), encoding="utf-8"))
        ck("市场登记路径已修正为本机路径",
           _p.normcase(_p.normpath(kn["my-experts"]["installLocation"]))
           == _p.normcase(_p.normpath(os.path.join(P, "marketplaces", "my-experts"))),
           kn["my-experts"]["installLocation"])

        st = json.load(open(os.path.join(wbA, "settings.json"), encoding="utf-8"))
        ck("settings.json 已启用该专家",
           st["enabledPlugins"].get("demo-expert@my-experts") is True)

        ck("冲突副本被报告而非删除",
           os.path.exists(os.path.join(src, "references",
                                       "r0.sync-conflict-20260101-000000.md")))
        logs = os.listdir(logdir)
        ck("共享日志已写出", len(logs) == 1 and logs[0].startswith("自愈日志-"), logs)

        rc2, out2 = run_heal(wbA, logdir)
        ck("幂等：再跑一次退出码=0", rc2 == 0, rc2)
        ck("幂等：日志没有重复追加", len(os.listdir(logdir)) == 1)

        print()
        print("=" * 70)
        print("  场景 B：副本存在但缺文件（模拟同步没传完）")
        print("=" * 70)
        wbB = os.path.join(tmp, "B", ".workbuddy")
        P2, src2 = build_tree(
            wbB, src_ver="2.0.0", cache_vers=["2.0.0"],
            stale_path="X:/nope/2.0.0", known_path="X:/nope",
            enabled=True, partial_cache=True)
        rc, out = run_heal(wbB, logdir)
        print(out)
        ck("识别出副本缺文件并补齐", rc == 10, rc)
        nc = os.path.join(P2, "cache", "my-experts", "demo-expert", "2.0.0")
        ck("补齐后文件数 = 5",
           len([f for f in os.listdir(os.path.join(nc, "references"))]) == 5,
           os.listdir(os.path.join(nc, "references")))
        st2 = json.load(open(os.path.join(wbB, "settings.json"), encoding="utf-8"))
        ck("已启用的不重复改", st2["enabledPlugins"]["demo-expert@my-experts"] is True)

        print()
        print("=" * 70)
        print("  场景 C：源包不存在（同步还没来）")
        print("=" * 70)
        wbC = os.path.join(tmp, "C", ".workbuddy")
        os.makedirs(wbC, exist_ok=True)
        rc, out = run_heal(wbC, logdir)
        ck("源包缺失时退出码=1 且不报错崩溃", rc == 1, rc)

        print()
        print("=" * 70)
        print("  场景 D：已经全对（模拟本机正常状态）")
        print("=" * 70)
        wbD = os.path.join(tmp, "D", ".workbuddy")
        good = os.path.join(wbD, "plugins", "cache", "my-experts",
                            "demo-expert", "3.0.0")
        build_tree(wbD, src_ver="3.0.0", cache_vers=["3.0.0"],
                   stale_path=good, known_path="REPLACED", enabled=True)
        knD = os.path.join(wbD, "plugins", "known_marketplaces.json")
        d = json.load(open(knD, encoding="utf-8"))
        d["my-experts"]["installLocation"] = os.path.join(
            wbD, "plugins", "marketplaces", "my-experts")
        json.dump(d, open(knD, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        beforeD = snapshot(wbD)
        rc, out = run_heal(wbD, logdir)
        print(out)
        ck("全对时退出码=0", rc == 0, rc)
        ck("全对时不写任何文件", snapshot(wbD) == beforeD)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 70)
    print("  合计：PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("    [FAIL] %s" % f)
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
