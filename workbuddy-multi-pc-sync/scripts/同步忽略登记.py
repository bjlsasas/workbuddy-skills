# -*- coding: utf-8 -*-
"""
同步忽略登记 —— 给 Syncthing 同步夹补「本机专属标记」忽略规则。

为什么需要它：
  WorkBuddy 的运行副本夹（wb-experts-cache）里有一批**本机专属**的小标记文件：
    .in_use              进程锁，文件名 = 本机进程号
    .orphaned_at         孤儿标记，毫秒时间戳
    .created-by-session  会话标识，本机会话 UUID
  它们跨机同步毫无意义，而且两台机器的进程号一撞名就产生冲突副本；
  更严重的是对端删这类文件时，本机那份正被进程占用删不掉，
  Syncthing 会报 "Failed to sync … file modified but not rescanned"，
  导致**整个文件夹同步失败并进入重试循环**。

  Syncthing 的 .stignore **永不跨机传输**（官方文档明示），
  所以每台机器都得各自配一份 —— 本模块就是干这个的，可被自愈/巡检直接调用。

用法：
  python 同步忽略登记.py --check    只检查不动手
  python 同步忽略登记.py            缺什么补什么

判据（幂等）：只按「必需规则行是否存在」判断，不比对注释文字，不重写已有内容。
"""

import os
import re
import io
import sys
import json
import urllib.request
import urllib.error

# ---------------------------------------------------------------- 规则表
# 只登记「必须存在」的 pattern；注释行只用于新建文件时的可读性，不参与判定。
#
# ★★ 每条 pattern 都必须带 (?d) 前缀 —— 这是 Syncthing 的官方修饰符，
#    含义是「父目录被删时，允许把这个被忽略项一并删掉」。
#    缺了它，对端卸载/删除某个目录、而该目录里只剩这些被忽略的本机标记时，
#    Syncthing 会**拒绝删除"内含忽略文件"的目录** → 该夹进入 Folder failed to sync
#    → 指数退避重试 → **永久卡在「未同步」，界面还不报错**。
#    详见 SKILL.md 第十九章。
#    注意：同一条 pattern 的裸形式与 (?d) 形式**不能并存** ——
#    忽略匹配逐条按顺序判定、任一条命中即生效，裸行排前面时 (?d) 永远轮不到，
#    看着配了、实际照旧卡死。所以 audit() 用 _normalize_d() 把裸行**就地升级**
#    （替换），而不是像普通缺项那样往文件末尾**追加**。
RULES = {
    "wb-experts-cache": {
        "label": "专家运行副本",
        "required": ["(?d).in_use", "(?d).orphaned_at", "(?d).created-by-session"],
        "header": [
            "// WorkBuddy 运行副本夹 —— 本机专属标记，跨机同步无意义且会撞名产生冲突副本",
            "// .in_use             进程锁（文件名 = 本机进程号）",
            "// .orphaned_at        孤儿标记（毫秒时间戳）",
            "// .created-by-session 会话标识（本机会话 UUID）",
            "// (?d) 前缀 = 父目录被删时允许一并删掉本项，否则整个夹会卡在「未同步」。",
            "// 只拦这三类；.codebuddy-plugin / .gitkeep / .stfolder 一律照常同步，不得拦。",
            "// 本文件不参与同步（Syncthing 特性），需每台机器各自配置。",
        ],
    },
    # ★ 2026-10-08 补：工作区夹此前是**手工配**的（没进规则表），
    #   意味着换机 / 新机时没人帮它补规则，而它同样会撞上
    #   「删目录被忽略文件挡住 → 整个夹同步失败」这个坑。就地固化成规则，
    #   由开机自愈与周检在每台机器上幂等补齐。
    "my-workspace": {
        "label": "工作区",
        "required": [
            "(?d)*.tmp",
            "(?d)~$*",
            "(?d)Thumbs.db",
            "(?d)desktop.ini",
            "(?d)__pycache__",
            "(?d)*.pyc",
        ],
        "header": [
            "// 工作区夹 —— 临时件与 Python 字节码缓存，跨机同步只会制造抖动与冲突",
            "// __pycache__ / *.pyc  Python 字节码缓存（每次运行都重新生成）",
            "// (?d) 前缀 = 父目录被删时允许一并删掉本项，否则整个夹会卡在「未同步」。",
            "// 正式产出物、脚本、报告一律照常同步，不得拦。",
            "// 本文件不参与同步（Syncthing 特性），需每台机器各自配置。",
        ],
    },
}

# 安装目录候选：由配置层给出；为空时交给运行期自动探测。
# 发布版里不含任何个人的实际安装路径。
try:
    if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import shconfig as _shc
    SYNC_ROOT_CANDS = list(_shc.sync_root_candidates())
except Exception:
    SYNC_ROOT_CANDS = []

# ---------------------------------------------------------------- 定位
def sync_root():
    """定位 Syncthing 安装目录：环境变量 → 启动器 → 已知候选 → 运行中进程"""
    v = (os.environ.get("WBSYNC") or "").strip().strip('"')
    if v and os.path.isfile(os.path.join(v, "syncthing.exe")):
        return v

    # 启动文件夹里的登录启动器（正文纯 ASCII，SYNC_ROOT=…）
    appdata = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    sdir = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    if os.path.isdir(sdir):
        for n in os.listdir(sdir):
            if not n.lower().endswith(".bat"):
                continue
            try:
                with io.open(os.path.join(sdir, n), "r", encoding="ascii", errors="ignore") as f:
                    for line in f:
                        s = line.strip()
                        if s.upper().startswith("SET SYNC_ROOT="):
                            p = s.split("=", 1)[1].strip().strip('"')
                            if p and os.path.isfile(os.path.join(p, "syncthing.exe")):
                                return p
            except Exception:
                pass

    for c in SYNC_ROOT_CANDS:
        if os.path.isfile(os.path.join(c, "syncthing.exe")):
            return c

    try:
        import subprocess
        r = subprocess.run(["wmic", "process", "where", "name='syncthing.exe'",
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


def config_path():
    r = sync_root()
    if not r:
        return None
    p = os.path.join(r, "data", "config.xml")
    return p if os.path.isfile(p) else None


def folder_paths():
    """config.xml → {folder_id: (path, label)}"""
    cp = config_path()
    if not cp:
        return {}
    try:
        t = io.open(cp, encoding="utf-8", errors="replace").read()
    except Exception:
        return {}
    out = {}
    for m in re.finditer(r"<folder\b([^>]*)>", t):
        a = m.group(1)

        def g(k):
            mm = re.search(k + r'="([^"]*)"', a)
            return mm.group(1) if mm else ""

        fid = g("id")
        if fid:
            out[fid] = (g("path"), g("label"))
    return out


def api_key():
    cp = config_path()
    if not cp:
        return None
    try:
        t = io.open(cp, encoding="utf-8", errors="replace").read()
        m = re.search(r"<apikey>(.*?)</apikey>", t)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def _api(path, method="GET", body=None, timeout=60):
    key = api_key()
    if not key:
        raise RuntimeError("没有 API Key")
    url = "http://127.0.0.1:8384" + path
    # ★ body 可能是 bytes（如 scan 传空 body）——不能拿去 json.dumps，
    #   否则 TypeError 被上层 try 吞掉，重扫会静默失败。
    if body is None:
        data, ctype = None, False
    elif isinstance(body, (bytes, bytearray)):
        data, ctype = bytes(body), False
    else:
        data, ctype = json.dumps(body).encode("utf-8"), True
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("X-API-Key", key)
    if ctype:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def rescan(folder):
    """触发重扫（POST）。这是同步阻塞调用，大夹会慢；失败不影响主流程
    —— fsWatcher 通常会自己发现 .stignore 变化。"""
    try:
        _api("/rest/db/scan?folder=" + folder, "POST", body=b"", timeout=120)
        return True
    except Exception:
        return False


def api_read_ignores(folder):
    """经 Syncthing API 读忽略行（这是权威视图）"""
    d = _api("/rest/db/ignores?folder=" + folder)
    return d.get("ignore") or []


def api_write_ignores(folder, lines):
    """经 Syncthing API 写忽略行。

    ★ 为什么不直接写 .stignore 文件：Syncthing 自己也会读写这个文件，
      外部直接 os.replace 存在抢占窗口（实测会遇到 PermissionError），
      而且 API 写入能让 Syncthing 立刻感知并重扫。所以**一律走 API**。
    """
    _api("/rest/db/ignores?folder=" + folder, "POST", body={"ignore": list(lines)})
    return api_read_ignores(folder)


def merge_lines(existing, spec, missing):
    """合并策略：已有文件 → 保留原内容只追加缺失行；无文件 → 写完整块（含注释头）"""
    lines = [l for l in (existing or [])]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return list(spec["header"]) + list(spec["required"])
    out = list(lines)
    out.append("")
    out.append("// 由同步忽略登记自动补齐 · %s" % _now())
    for p in spec["required"]:
        if p in missing:
            out.append(p)
    return out


# ---------------------------------------------------------------- 读写
def _normalize_d(lines, required):
    """把与 required 同模式、但缺 `(?d)` 前缀的行**就地升级**；顺手去掉同模式的重复行。

    为什么必须替换、不能追加：
      Syncthing 的忽略匹配是逐条按顺序判定的，任一条命中即生效。`.in_use` 与
      `(?d).in_use` 并存时，裸行若排在前面，`(?d)` 的「允许随父目录删除」
      就永远不会生效 —— 看着配了、实际照旧卡死。

    返回 (新行列表, 升级/去重的条数)。纯函数，不碰磁盘、不碰网络，可直接单测。
    """
    core2full = {}
    for p in (required or []):
        if p.startswith("(?d)"):
            core2full[p[4:]] = p
    out, n, seen = [], 0, set()
    for l in (lines or []):
        s = (l or "").strip()
        if not s or s.startswith("//"):
            out.append(l)
            continue
        core = s[4:] if s.startswith("(?d)") else s
        full = core2full.get(core)
        if not full:
            out.append(l)
            continue
        if full in seen:            # 同一模式重复出现（手工加过 + 自愈又加过）→ 只留一条
            n += 1
            continue
        seen.add(full)
        if s != full:               # 裸形式 → 升级为带前缀
            out.append(full)
            n += 1
        else:
            out.append(l)
    return out, n


def patterns_of(lines):
    """从行列表里取出有效 pattern（去掉空行与 // 注释）"""
    pats = set()
    for l in lines or []:
        s = (l or "").strip()
        if not s or s.startswith("//"):
            continue
        pats.add(s)
    return pats


def read_patterns(ig_path):
    """返回 (全部行, 有效 pattern 行集合) —— 直接读磁盘文件"""
    if not os.path.isfile(ig_path):
        return [], set()
    try:
        raw = io.open(ig_path, encoding="utf-8", errors="replace").read()
    except Exception:
        return [], set()
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return lines, patterns_of(lines)


def write_atomic(ig_path, text):
    """UTF-8 无 BOM + LF，临时文件 + 原子替换"""
    tmp = ig_path + ".tmp-zgh"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, ig_path)


# ---------------------------------------------------------------- 主检查
def audit(dry=True):
    """返回 {ok, fixed, issues, notes} —— 与 自启登记.py 同款四类汇总"""
    res = {"ok": [], "fixed": [], "issues": [], "notes": []}

    if not sync_root():
        res["issues"].append("找不到 Syncthing 安装目录，同步忽略规则没核成")
        return res

    fps = folder_paths()
    if not fps:
        res["issues"].append("读不出 Syncthing 的 config.xml（拿不到同步夹路径）")
        return res

    for fid, spec in RULES.items():
        label = spec.get("label") or fid
        if fid not in fps:
            res["notes"].append("%s（%s）不在本机同步夹配置里，跳过" % (fid, label))
            continue
        path = fps[fid][0]
        if not path or not os.path.isdir(path):
            res["notes"].append("%s 的目录还不在本机（等同步过来）" % fid)
            continue

        ig_path = os.path.join(path, ".stignore")
        had_file = os.path.isfile(ig_path)

        # 判定基准：能用 API 就用 API（权威视图，且与 Syncthing 所见一致）；
        # API 不可用（Syncthing 没跑）才退回读磁盘文件。
        lines = None
        try:
            lines = api_read_ignores(fid)
            src = "API"
        except Exception:
            lines, _ = read_patterns(ig_path)
            src = "文件"
        # ★ 先就地升级裸行（并去重），再判缺项 —— 顺序不能反。
        lines, upgraded = _normalize_d(lines, spec["required"])
        pats = patterns_of(lines)
        missing = [p for p in spec["required"] if p not in pats]

        if not missing and not upgraded:
            res["ok"].append("%s：忽略规则齐全（%s）" % (fid, "、".join(spec["required"])))
        elif dry:
            why = []
            if missing:
                why.append("缺 %d 条（%s）" % (len(missing), "、".join(missing)))
            if upgraded:
                why.append("%d 条未带 (?d) 前缀或重复" % upgraded)
            res["issues"].append(
                "%s %s（判定基准=%s）—— 不修会反复产生冲突副本，"
                "并可能让整个文件夹同步失败"
                % (fid, "，".join(why), src))
        else:
            new_lines = merge_lines(lines, spec, missing) if missing else list(lines)
            back = None
            try:
                back = api_write_ignores(fid, new_lines)   # 首选：Syncthing 自己的通道
                via = "API"
            except Exception as e_api:
                try:                                        # 兜底：直接写文件
                    write_atomic(ig_path, "\n".join(new_lines) + "\n")
                    _, pats_file = read_patterns(ig_path)
                    back = sorted(pats_file)
                    via = "文件(API 不可用: %r)" % (e_api,)
                except Exception as e_file:
                    res["issues"].append(
                        "%s 写忽略规则失败 —— API: %r / 文件: %r" % (fid, e_api, e_file))
                    continue
            pats2 = patterns_of(back if isinstance(back, list) else [])
            if not pats2 and back:
                pats2 = set(back)
            still = [p for p in spec["required"] if p not in pats2]
            if still:
                res["issues"].append("%s 写了但复读仍缺 %s" % (fid, "、".join(still)))
                continue
            back_lines = back if isinstance(back, list) else []
            _, left = _normalize_d(back_lines, spec["required"])
            if left:
                res["notes"].append("%s 写回后仍有 %d 条裸/重复规则（下一轮会再修）"
                                    % (fid, left))
            rescan(fid)
            parts = []
            if missing:
                parts.append("补 %d 条（%s）" % (len(missing), "、".join(missing)))
            if upgraded:
                parts.append("升级/去重 %d 条为 (?d) 形式" % upgraded)
            res["fixed"].append("%s %s%s 经%s写入"
                                % (fid, "，".join(parts),
                                   "" if had_file else "，并新建规则文件", via))

    # 顺带点一下冲突副本（只报告，不擅自处理）
    total_conf = 0
    for fid in RULES:
        if fid not in fps:
            continue
        path = fps[fid][0]
        if not path or not os.path.isdir(path):
            continue
        n = 0
        for dp, dn, fn in os.walk(path):
            if ".stversions" in dp:
                continue
            for f in fn:
                if "sync-conflict" in f:
                    n += 1
        total_conf += n
    if total_conf:
        res["notes"].append("该夹仍有 %d 个历史冲突副本（不影响运行，可择日清理）" % total_conf)

    return res


def _now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- CLI
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    dry = "--check" in sys.argv
    print("=" * 62)
    print("  同步忽略登记 · %s" % ("只检查" if dry else "自动补齐"))
    print("=" * 62)
    r = audit(dry=dry)
    for x in r["ok"]:
        print("  [OK]   %s" % x)
    for x in r["fixed"]:
        print("  [修好] %s" % x)
    for x in r["issues"]:
        print("  [要处理] %s" % x)
    for x in r["notes"]:
        print("  [说明] %s" % x)
    if dry:
        print("\n（--check 模式，未做任何改动）")
    print()
    return 1 if r["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
