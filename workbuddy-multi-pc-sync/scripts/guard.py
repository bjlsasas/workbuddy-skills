# -*- coding: utf-8 -*-
"""
同步守卫 · 文件夹登记体检（含加密口令）
================================================================
为什么要有这个脚本
----------------------------------------------------------------
Syncthing 生成「集群配置」发给对端时，是逐文件夹、逐设备条目来构建的。
只要某条设备登记带着**非空 encryptionPassword**，而这条又不是「专门发给
目标设备的」，Syncthing 就会**把这条整条跳过**，对端收到的设备列表里
就少了设备，于是立刻报：

    error="handling cluster-config: remote device missing in cluster config"
    Device sent cluster-config without the device info for the remote (folder.id=...)

然后**秒断连接**，20 秒后再来一次 —— 无限循环、0 字节。

本机自己那条登记**永远不是「专门发给本机的」**，所以一旦它自带口令，
就必然被跳过。2026-09-19 那场「连上 2 秒就断」，真凶就是它：
四个文件夹里，本机自己那条的 encryptionPassword 长度 = 9。

顺带体检项
----------------------------------------------------------------
· 每个文件夹的设备登记里，必须含「本机自己」和「对端」各一条
· 不能残留已废弃的旧身份设备条目

用法
----------------------------------------------------------------
    同步守卫.py              体检，发现问题就修（改前自动备份 config.xml）
    同步守卫.py --check      只体检，一个字都不写
    同步守卫.py --quiet      静默；退出码 0=无需处理 / 10=已修 / 2=出错
    同步守卫.py --report     无论有没有问题，都往同步工作区写一份回传日志

配置文件（由安装器写入同目录的 guard-config.json）
----------------------------------------------------------------
    {"config_xml": "...", "workspace": "...", "peer_id": "...", "dead_ids": [...]}
"""

import argparse
import datetime
import glob
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import shconfig          # noqa: E402  统一配置层
import shdetect          # noqa: E402  运行期自动探测

HOST = os.environ.get("COMPUTERNAME") or "unknown"
HOME = os.path.expanduser("~")
API = shdetect.API
CFG_JSON = os.path.join(HERE, "guard-config.json")
LINE = "=" * 68

# 一切私有取值都改由配置层提供（缺省值刻意保持通用）：
#   文件夹 id  ->  shconfig.folder_ids()
#   对端设备   ->  guard-config.json 或运行期自动探测
#   废弃设备   ->  shconfig.dead_ids()
def FOLDER_IDS():
    return shconfig.folder_ids()


# 本机实际存在、本次要看护的夹（main() 里由 resolve_watched 填）。
# ★ 各机的同步夹清单**本来就可能不同**（后加的夹不会自己跑到另一台），
#   所以清单里本机没有的夹由 WATCHED 过滤掉，只提示、不当问题，
#   避免每轮一条假告警。
WATCHED = None


def watched():
    """本次要看的夹：优先用 resolve_watched 过滤后的结果，否则退回全清单。"""
    return WATCHED or FOLDER_IDS()


def resolve_watched(key):
    """返回 (本次要看的夹, 清单里有但本机没有的夹)。

    verify() 会对清单里每条做存在性复核；清单里塞了本机没有的夹 ＝ 每轮一条假告警。
    但反过来又不能因此取消存在性复核（夹真的消失时必须报）。折中：
    先按本机现有夹过滤出本次要看的集合，被过滤掉的单独提示一句。
    """
    code, folds = apij("GET", "/rest/config/folders", key)
    if code != 200 or not isinstance(folds, list):
        return list(FOLDER_IDS()), []
    have = set()
    for f in folds:
        fid = (f or {}).get("id")
        if fid:
            have.add(fid)
    keep = [f for f in FOLDER_IDS() if f in have]
    absent = [f for f in FOLDER_IDS() if f not in have]
    return keep, absent


def RELAY_REL():
    return os.path.join(shconfig.coop_dir_rel(), shconfig.relay_subdir())

# Windows 下 stdout 被管道/重定向捕获时会退化成 GBK，中文输出会变成乱码
# （2026-09-19 实证：同一个父进程抓两个子脚本的输出，一个乱码一个正常，
#   差别就在本文件缺这一段、专家自愈脚本有）。统一强制 UTF-8。
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = []
VERBOSE = True


def out(s=""):
    OUT.append(s)
    if VERBOSE:
        try:
            print(s)
        except Exception:
            pass


def warn(msg):
    out("  ! " + msg)


# ------------------------------------------------------------------ 定位
def find_config_xml():
    """返回 [(config_xml, 来源说明)]，按可信度排序。

    实探测逻辑收敛到 shdetect，这里只把它包装成 (路径, 来源) 列表，
    保持与既有调用方（choose_config / resolve_config）的签名一致。
    """
    hits = []

    def add(p, why):
        if p and os.path.isfile(p):
            rp = os.path.realpath(p)
            if all(rp != os.path.realpath(h[0]) for h in hits):
                hits.append((rp, why))

    # 1) 配置文件里显式给的候选
    for c in shconfig.sync_root_candidates():
        add(os.path.join(c, "data", "config.xml"), "配置候选 " + c)

    # 2) 运行期自动探测（进程 -> 启动器 -> 已知目录 -> 浅扫）
    p = shdetect.find_config_xml(shconfig.sync_root_candidates())
    if p:
        add(p, "自动探测")

    # 3) 用户目录下的默认位置
    for base in (os.path.join(HOME, "AppData", "Local", "Syncthing"),
                 os.path.join(HOME, "AppData", "Roaming", "Syncthing"),
                 os.path.join(HOME, "Syncthing")):
        add(os.path.join(base, "config.xml"), "用户目录")

    return hits


def choose_config(hits):
    """优先选含我们四个文件夹 id 的那个"""
    scored = []
    for p, why in hits:
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        n = sum(1 for f in FOLDER_IDS() if f in t)
        scored.append((n, os.path.getmtime(p), p, why))
    if not scored:
        return None, None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2], scored[0][3]


def load_cfg():
    if os.path.isfile(CFG_JSON):
        try:
            return json.load(open(CFG_JSON, encoding="utf-8"))
        except Exception:
            pass
    return {}


def resolve_config():
    c = load_cfg()
    p = c.get("config_xml")
    if p and os.path.isfile(p):
        return p, "guard-config.json"
    hits = find_config_xml()
    p, why = choose_config(hits)
    return p, why


# ------------------------------------------------------------------ REST
def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(method, path, key, body=None, timeout=20):
    data = None
    hdr = {"X-API-Key": key}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=hdr, method=method)
    try:
        r = opener().open(req, timeout=timeout)
        raw = r.read().decode("utf-8", "replace")
        return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def apij(method, path, key, body=None, timeout=20):
    code, raw = api(method, path, key, body, timeout)
    try:
        return code, json.loads(raw)
    except Exception:
        return code, None


def read_key(cfg):
    import xml.etree.ElementTree as ET
    root = ET.parse(cfg).getroot()
    el = root.find("gui").find("apikey")
    return (el.text or "").strip() if el is not None else ""


# ------------------------------------------------------------------ 体检
def pw_len(dev):
    v = dev.get("encryptionPassword")
    if v is None:
        return ("none", None)
    if isinstance(v, str):
        return (len(v), v)
    return ("bad", str(v))


def inspect(key):
    code, folds = apij("GET", "/rest/config/folders", key)
    if code != 200 or not isinstance(folds, list):
        return None, "读文件夹配置失败 HTTP %s %s" % (code, str(folds)[:140])
    code, st = apij("GET", "/rest/system/status", key)
    my_id = (st or {}).get("myID") or ""
    c = load_cfg()
    peer = c.get("peer_id") or shconfig.peer_id() or ""
    dead = set(c.get("dead_ids") or []) | shconfig.dead_ids()
    # 对端一律不写死：优先用配置，其次从全局设备表里挑「不是自己的那台」。
    # 这样同一个脚本在两台机器上跑都对，且发布版里不含任何真实设备 ID。
    if not peer or peer == my_id:
        code2, devs = apij("GET", "/rest/config/devices", key)
        for d in (devs or []):
            did = (d or {}).get("deviceID") or ""
            if did and did != my_id:
                peer = did
                break
    return {"folders": folds, "my_id": my_id, "peer": peer, "dead": dead}, None


def analyse(snap):
    """返回 (问题清单, 需要改写的文件夹 dict 列表)"""
    probs = []
    todo = []
    for f in snap["folders"]:
        fid = f.get("id") or "?"
        if fid not in watched():
            continue
        ftype = (f.get("type") or "sendreceive").lower()
        devs = list(f.get("devices") or [])
        ids = [(d.get("deviceID") or "") for d in devs]
        need_fix = False

        # ① 加密口令：非 receiveencrypted 的文件夹里出现非空口令 = 配置被污染
        if ftype != "receiveencrypted":
            for d in devs:
                n, val = pw_len(d)
                if n == "none":
                    continue
                if n == "bad":
                    probs.append("%s：某条设备的 encryptionPassword 类型异常（%s），已清空"
                                 % (fid, val[:20]))
                    need_fix = True
                elif n > 0:
                    who = "本机自己" if (d.get("deviceID") or "") == snap["my_id"] else \
                          ("对端" if (d.get("deviceID") or "") == snap["peer"] else
                           (d.get("deviceID") or "")[:7])
                    probs.append("%s：%s 那条带非空 encryptionPassword（长度 %d），已清空"
                                 % (fid, who, n))
                    need_fix = True

        # ② 本机自己那条必须在
        if snap["my_id"] and snap["my_id"] not in ids:
            probs.append("%s：缺「本机自己」的设备登记，已补" % fid)
            need_fix = True

        # ③ 对端那条必须在
        if snap["peer"] and snap["peer"] not in ids:
            probs.append("%s：缺「对端」的设备登记，已补" % fid)
            need_fix = True

        # ④ 废弃设备
        for d in devs:
            if (d.get("deviceID") or "") in snap["dead"]:
                probs.append("%s：残留废弃设备条目 %s，已删"
                             % (fid, (d.get("deviceID") or "")[:7]))
                need_fix = True

        if need_fix:
            nd = []
            seen = set()
            for d in devs:
                did = d.get("deviceID") or ""
                if did in snap["dead"]:
                    continue
                if not did:
                    continue
                if did in seen:
                    continue
                seen.add(did)
                d = dict(d)
                if ftype != "receiveencrypted":
                    if pw_len(d)[0] != "none":
                        d["encryptionPassword"] = ""
                nd.append(d)
            for want in (snap["my_id"], snap["peer"]):
                if want and want not in seen:
                    nd.append({"deviceID": want, "encryptionPassword": ""})
            nf = dict(f)
            nf["devices"] = nd
            todo.append(nf)
    return probs, todo


def verify(key, snap):
    code, folds = apij("GET", "/rest/config/folders", key)
    if code != 200 or not isinstance(folds, list):
        return False, ["复核时读配置失败 HTTP %s" % code]
    bad = []
    got = {}
    for f in folds:
        got[f.get("id")] = f
    for fid in watched():
        f = got.get(fid)
        if not f:
            bad.append("%s：复核时不在配置里" % fid)
            continue
        devs = f.get("devices") or []
        ids = [(d.get("deviceID") or "") for d in devs]
        pws = [pw_len(d)[0] for d in devs]
        ok = True
        if any(isinstance(x, int) and x > 0 for x in pws):
            bad.append("%s：仍有非空口令 %s" % (fid, pws))
            ok = False
        if snap["my_id"] and snap["my_id"] not in ids:
            bad.append("%s：复核时仍缺本机登记" % fid)
            ok = False
        if snap["peer"] and snap["peer"] not in ids:
            bad.append("%s：复核时仍缺对端登记" % fid)
            ok = False
        out("   %-18s devices=%d 本机=%s 对端=%s 口令长度=%s  %s"
            % (fid, len(devs),
               "有" if snap["my_id"] in ids else "无",
               "有" if snap["peer"] in ids else "无",
               pws, "OK" if ok else "★仍异常"))
    return (not bad), bad


# ------------------------------------------------------------------ 回传日志
def write_relay(lines, workspace=None):
    ws = workspace or load_cfg().get("workspace") or ""
    if not ws or not os.path.isdir(ws):
        return None
    d = os.path.join(ws, RELAY_REL())
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    p = os.path.join(d, "同步守卫日志-%s.md" % HOST)
    try:
        new = not os.path.isfile(p)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write("# 同步守卫回传日志 · %s\n\n"
                        "> 每次跑守卫（含每 45 秒一次的常驻巡查）若发现问题，"
                        "或显式带 `--report`，就在这里追加一段。\n"
                        "> 随工作区同步回另一台，便于两侧互查。\n\n" % HOST)
            f.write("## %s\n\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            for l in lines:
                f.write("- %s\n" % l)
            f.write("\n")
        return p
    except Exception as e:
        warn("回传日志写入失败：%s" % e)
        return None


# ------------------------------------------------------------------ 主流程
def main():
    global VERBOSE, WATCHED
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只看不动")
    ap.add_argument("--quiet", action="store_true", help="静默")
    ap.add_argument("--silent", action="store_true", help="静默（同 --quiet）")
    ap.add_argument("--report", action="store_true", help="无论有无问题都写回传日志")
    ap.add_argument("--config", default=None, help="指定 config.xml")
    a = ap.parse_args()
    dry = a.check
    VERBOSE = not (a.quiet or a.silent)

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out(LINE)
    out("  同步守卫 · 文件夹登记体检   %s   %s" % (HOST, ts))
    out(LINE)

    cfg = a.config or None
    why = "命令行指定"
    if not cfg:
        cfg, why = resolve_config()
    if not cfg or not os.path.isfile(cfg):
        out("  X 找不到 config.xml，无法体检。")
        out("    可以把 -config <路径> 传进来。")
        return 2
    out("  配置文件：%s   （来自 %s）" % (cfg, why))

    try:
        key = read_key(cfg)
    except Exception as e:
        out("  X 读 apikey 失败：%s" % e)
        return 2
    if not key:
        out("  X config.xml 里没有 apikey。")
        return 2

    code, _ = api("GET", "/rest/noauth/health", key, timeout=5)
    if code != 200:
        out("  X 管理接口 %s 没响应（HTTP %s）。同步进程可能没在跑。" % (API, code))
        return 2

    snap, err = inspect(key)
    if err:
        out("  X " + err)
        return 2
    out("  本机身份：%s" % snap["my_id"])
    out("  对端身份：%s" % snap["peer"])

    WATCHED, _absent = resolve_watched(key)
    if _absent:
        out("  本机没有这几个同步夹，本次不查（各机清单本就可能不同）：%s"
            % "、".join(_absent))
    out("")

    probs, todo = analyse(snap)
    if not probs:
        out("  体检结论：%d 个同步文件夹的设备登记都正常，无需处理。" % len(watched()))
        if a.report:
            write_relay(["主机：`%s`" % HOST, "结果：**正常**，无需处理",
                         "本机身份：`%s`" % snap["my_id"]])
        out(LINE)
        return 0

    out("  发现 %d 处问题：" % len(probs))
    for p in probs:
        out("    · " + p)
    out("")
    if dry:
        out("  只体检模式：没有改动任何东西。")
        if a.report:
            write_relay(["主机：`%s`" % HOST, "结果：**只体检**，发现 %d 处问题（未改）" % len(probs)]
                        + ["· " + p for p in probs])
        out(LINE)
        return 0

    bak = None
    try:
        bak = cfg + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(cfg, bak)
        out("  已备份：%s（%d 字节）" % (os.path.basename(bak), os.path.getsize(bak)))
    except Exception as e:
        out("  ! 备份失败：%s" % e)
        bak = None

    out("")
    out("  改写登记：")
    failed = []
    for nf in todo:
        code, raw = api("PUT", "/rest/config/folders/" + nf.get("id"), key, nf)
        ok = (code == 200)
        out("    %-18s HTTP %s %s" % (nf.get("id"), code, "" if ok else raw[:120]))
        if not ok:
            failed.append(nf.get("id"))

    out("")
    out("  复核：")
    good, bad = verify(key, snap)
    for b in bad:
        out("    ! " + b)

    lines = ["主机：`%s`" % HOST, "配置：`%s`" % cfg,
             "结果：%s" % ("**已修好**" if good else "**仍有异常**")] + \
            ["· " + p for p in probs]
    if bad:
        lines += ["· " + b for b in bad]
    write_relay(lines)

    out(LINE)
    if good:
        return 10
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        try:
            print("同步守卫异常：%s" % e)
        except Exception:
            pass
        sys.exit(2)
