# -*- coding: utf-8 -*-
"""隔离自测：自动同步守护 v3 的「自更新」逻辑。
全部在临时目录里跑，不碰任何真实路径。
"""
import json
import os
import shutil
import sys
import tempfile
import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
SRC = os.path.join(_ROOT, "scripts", "sync-watch.py")
if not os.path.isfile(SRC):
    SRC = os.path.join(_ROOT, "sync-watch.py")
GOOD_GUARD = "# -*- coding: utf-8 -*-\nprint('guard v2')\n"
GOOD_HEAL = "# -*- coding: utf-8 -*-\nprint('heal v2')\n"
GOOD_WATCH = "# -*- coding: utf-8 -*-\nprint('watch v2')\n"
BAD_GUARD = "# -*- coding: utf-8 -*-\ndef broken(:\n    pass\n"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("   %s %s%s" % ("[OK]" if cond else "[XX]", name,
                          ("  <- " + str(extra)) if extra and not cond else ""))


def build(root, dist=None, local_guard=GOOD_GUARD):
    """root/A = 常驻目录（HERE），root/W = 假工作区

    分发源目录必须与守护自己算出来的路径一致。守护走配置层
    （config -> 环境变量 -> 缺省），所以这里用环境变量显式钉住，
    避免"测试按自己的猜法建目录、守护按缺省算路径"两边错位。
    """
    A = os.path.join(root, "A")
    W = os.path.join(root, "W")
    os.makedirs(A, exist_ok=True)
    _coop = os.environ.setdefault("SYNCHEAL_COOP_DIR", "coop")
    _dist = os.environ.setdefault("SYNCHEAL_DIST_SUBDIR", "dist")
    D = os.path.join(W, _coop, _dist)
    os.makedirs(D, exist_ok=True)
    shutil.copy2(SRC, os.path.join(A, "sync-watch.py"))
    # sync-watch 现在 import 配置层，常驻目录里必须同时有这两个模块，
    # 否则 import 阶段就死 —— 这里如实照搬真实部署的清单。
    _sd = os.path.dirname(os.path.abspath(SRC))
    for _mod in ("shconfig.py", "shdetect.py"):
        _src = os.path.join(_sd, _mod)
        if os.path.isfile(_src):
            shutil.copy2(_src, os.path.join(A, _mod))
    with open(os.path.join(A, "guard.py"), "wb") as f:
        f.write(local_guard.encode("utf-8"))
    with open(os.path.join(A, "heal-expert.py"), "wb") as f:
        f.write(GOOD_HEAL.encode("utf-8"))
    with open(os.path.join(A, "guard-config.json"), "w", encoding="utf-8") as f:
        json.dump({"workspace": W, "peer_id": "X", "dead_ids": []}, f,
                  ensure_ascii=True)
    if dist:
        for name, text in dist.items():
            # 二进制写入：避免 Windows 文本模式把 \n 变 \r\n 造成假哈希差
            with open(os.path.join(D, name), "wb") as f:
                f.write(text.encode("utf-8"))
    return A, W, D


def load(A):
    spec = importlib.util.spec_from_file_location(
        "watchmod_%d" % id(A), os.path.join(A, "sync-watch.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def rd(p):
    return open(p, encoding="utf-8").read()


print("=" * 66)
print("[1] 分发源有新版 -> 应把 guard.py / heal-expert.py 取过来")
print("=" * 66)
t1 = tempfile.mkdtemp(prefix="syncupd-")
A, W, D = build(t1, dist={"guard.py": GOOD_GUARD + "# new\n",
                          "heal-expert.py": GOOD_HEAL + "# new\n",
                          "sync-watch.py": GOOD_WATCH})
m = load(A)
hits = m.maybe_selfupdate()
names = sorted(n for n, _ in hits)
check("取到 3 个文件", len(hits) == 3, names)
check("guard.py 已更新", rd(os.path.join(A, "guard.py")) == GOOD_GUARD + "# new\n")
check("heal-expert.py 已更新",
      rd(os.path.join(A, "heal-expert.py")) == GOOD_HEAL + "# new\n")
check("守护自身被替换（sync-watch.py 内容变了）",
      rd(os.path.join(A, "sync-watch.py")) == GOOD_WATCH)
baks = [n for n in os.listdir(A) if ".bak-" in n]
check("旧版留了 .bak（3 个）", len(baks) == 3, baks)

print()
print("=" * 66)
print("[2] 再跑一次 -> 内容一致，应一个字都不动（幂等）")
print("=" * 66)
t2 = tempfile.mkdtemp(prefix="syncupd-")
A2, W2, D2 = build(t2, dist={"guard.py": GOOD_GUARD,
                             "heal-expert.py": GOOD_HEAL,
                             "sync-watch.py": rd(SRC)})
m2 = load(A2)
before = {n: os.path.getmtime(os.path.join(A2, n))
          for n in ("guard.py", "heal-expert.py", "sync-watch.py")}
hits2 = m2.maybe_selfupdate()
after = {n: os.path.getmtime(os.path.join(A2, n))
         for n in ("guard.py", "heal-expert.py", "sync-watch.py")}
check("无改动（hits 为空）", hits2 == [], hits2)
check("三个文件 mtime 未变", before == after)
check("没有产生 .bak", not [n for n in os.listdir(A2) if ".bak-" in n])

print()
print("=" * 66)
print("[3] 分发源里是坏文件（编译不过）-> 必须拒绝，保留本地版")
print("=" * 66)
t3 = tempfile.mkdtemp(prefix="syncupd-")
A3, W3, D3 = build(t3, dist={"guard.py": BAD_GUARD})
m3 = load(A3)
hits3 = m3.maybe_selfupdate()
check("坏文件未被采纳（hits 为空）", hits3 == [], hits3)
check("本地 guard.py 原样未动", rd(os.path.join(A3, "guard.py")) == GOOD_GUARD)
lg = rd(os.path.join(A3, "watcher-log-%s.log" % (os.environ.get("COMPUTERNAME") or "unknown")))
check("日志记下了拒绝原因", "编译不通过" in lg, lg[-200:])

print()
print("=" * 66)
print("[4] 没有分发源目录 -> 安静返回，不报错")
print("=" * 66)
t4 = tempfile.mkdtemp(prefix="syncupd-")
A4, W4, D4 = build(t4, dist=None)
shutil.rmtree(D4)
m4 = load(A4)
check("返回空列表", m4.maybe_selfupdate() == [])
check("本地文件未动", rd(os.path.join(A4, "guard.py")) == GOOD_GUARD)

print()
print("=" * 66)
print("[5] 源码完整性：语法 + 关键接线")
print("=" * 66)
src = rd(SRC)
ok = True
try:
    compile(src, SRC, "exec")
except Exception as e:
    ok = False
    print("   语法错误：%s" % e)
check("语法可编译", ok)
check("main 里调了 maybe_selfupdate（启动时）", "maybe_selfupdate()\n    run_guard()" in src)
check("主循环里也调了", "maybe_selfupdate()\n            run_guard()" in src)
check("强制 UTF-8 解码子进程输出", src.count('encoding="utf-8",') >= 2)

print()
print("=" * 66)
print("结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("   失败：" + f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
