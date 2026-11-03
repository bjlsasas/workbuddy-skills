# -*- coding: utf-8 -*-
"""
装 Syncthing —— 为「多电脑同步」准备这台机器上的同步客户端。

为什么要单独有这个脚本
--------------------
Syncthing 是**第三方开源软件**（官方 Windows 便携版 zip 约 20 MB+），
它装在**操作系统**里、跑在 WorkBuddy 之外，**不可能打进技能包**。
所以**每接入一台新电脑，都要在那台机器上装一次**（装过的机器不用重复装）。

本脚本把那一步压成一条命令：

    python 装Syncthing.py --check                只探测，一个字都不写
    python 装Syncthing.py                        没有就装到默认目录
    python 装Syncthing.py --dir <安装目录>         指定安装目录（syncthing.exe 直接落在这一层）
    python 装Syncthing.py --force                已装也重下一份（用于升级）
    python 装Syncthing.py --zip <已下好的zip>      跳过下载，只做校验 + 解压 + 落位
                                                 （浏览器下载比命令行稳时用这条）

设计约束（与包内其他脚本一致）
----------------------------
  * **零机器常量**：不含任何私人路径 / 设备 ID / 主机名 / 盘符写死。
    默认安装位置按「第一个非系统盘 → 用户目录」的通用规则推导。
  * **幂等**：已装且 exe 在，直接报路径返回，不重复下载。
  * **不抛栈**：无网络、被代理挡、无写权限，都给人话 + 下一步。
  * **永不覆盖**：目标已有 exe 时，先改名 `.bak-<时间戳>` 再落新文件。

退出码：0 = 已就绪（无论是新装还是本来就有）；1 = 没装成。
"""

import io
import os
import re
import shutil
import string
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

RELEASES_API = "https://api.github.com/repos/syncthing/syncthing/releases/latest"
RELEASES_PAGE = "https://github.com/syncthing/syncthing/releases/latest"
DL_PAGE = "https://syncthing.net/downloads/"

# 只认官方 Windows 64 位便携版：syncthing-windows-amd64-v1.2.3.zip
ASSET_RE = re.compile(r"^syncthing-windows-amd64-v(\d+(?:\.\d+)+)\.zip$", re.I)
ASSET_URL_RE = re.compile(
    r"/releases/download/[^/\"']+/(syncthing-windows-amd64-v\d+(?:\.\d+)+\.zip)", re.I)

EXE = "syncthing.exe"
CHECK_ONLY = "--check" in sys.argv
FORCE = "--force" in sys.argv


# ------------------------------------------------------------------ 小工具
def force_utf8():
    """子进程被管道捕获时 stdout 会退化成 ANSI 码页，中文会花。"""
    for s in ("stdout", "stderr"):
        f = getattr(sys, s, None)
        if f is not None and hasattr(f, "reconfigure"):
            try:
                f.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def say(msg=""):
    print(msg)


def drive_roots():
    """所有存在的盘根，例如 C 盘根、D 盘根。"""
    out = []
    for c in string.ascii_uppercase:
        p = c + ":\\"
        try:
            if os.path.isdir(p):
                out.append(p)
        except Exception:
            pass
    return out


def default_root():
    """默认安装根目录：第一个非系统盘下的 WorkBuddySync；没有就退到用户目录。

    刻意不写死盘符 —— 别人机器上 C/D/E 的分布与本机不同。
    """
    sysdrv = (os.environ.get("SystemDrive") or "C:").upper()
    for drv in drive_roots():
        if drv.upper().startswith(sysdrv):
            continue
        return os.path.join(drv, "WorkBuddySync")
    return os.path.join(os.path.expanduser("~"), "WorkBuddySync")


def opt_value(flag):
    """取 --flag <值> 或 --flag=<值>；没给返回 ''。"""
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return ""


def print_proxy_hint():
    say("")
    say("    这多半是网络 / 代理问题，不是软件问题。三条出路：")
    say("      1) 用浏览器打开下面任一地址，下 Windows 64 位便携版 zip，然后跑：")
    say("           python 装Syncthing.py --zip <你下好的zip路径>")
    say("         %s" % DL_PAGE)
    say("         %s" % RELEASES_PAGE)
    say("      2) 关掉代理再跑一次本脚本")
    say("      3) 有代理工具的话，把 github.com 与 objects.githubusercontent.com 设为直连")


def target_dir():
    return opt_value("--dir") or os.path.join(default_root(), "syncthing")


# ------------------------------------------------------------------ 探测
def detect():
    """返回 (exe_path, how) —— 本机已装的 Syncthing；没有则 ('', '')。

    顺序：① Syncthing 的 home 目录（进程 / 启动器 / 常见位置 / 浅扫，交给 shdetect）
          ② 运行中进程的 exe 路径
          ③ 通用候选目录
    """
    try:
        import shdetect
    except Exception:
        shdetect = None

    if shdetect is not None:
        try:
            home = shdetect.find_sync_home()
        except Exception:
            home = ""
        if home:
            cand = os.path.join(home, EXE)
            if os.path.isfile(cand):
                return cand, "Syncthing home 目录"
            # home 可能是 data 的子级，往上退一级再找
            up = os.path.dirname(home)
            cand = os.path.join(up, EXE)
            if os.path.isfile(cand):
                return cand, "Syncthing home 上级目录"
        try:
            # running_processes() 返回 (pid, ppid, exe_name, full_path)
            for proc in shdetect.running_processes(EXE):
                if len(proc) >= 4 and proc[3] and os.path.isfile(proc[3]):
                    return proc[3], "运行中进程"
        except Exception:
            pass

    cands = []
    for drv in drive_roots():
        cands.append(os.path.join(drv, "WorkBuddySync", "syncthing"))
        cands.append(os.path.join(drv, "syncthing"))
    cands.append(os.path.join(os.path.expanduser("~"), "AppData", "Local", "Syncthing"))
    for c in cands:
        p = os.path.join(c, EXE)
        if os.path.isfile(p):
            return p, "常见目录扫描"
    return "", ""


# ------------------------------------------------------------------ 联网
def _get(url, timeout=25):
    """先走系统默认（含系统代理），失败再绕代理重试一次。"""
    req = urllib.request.Request(url, headers={"User-Agent": "syncthing-installer"})
    try:
        return urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return op.open(req, timeout=timeout).read()


def latest_asset():
    """返回 (文件名, 下载直链, 版本号, sha256 清单文本或 '')。"""
    # A) GitHub API（最稳，能一次拿到版本 + 直链 + 校验清单）
    try:
        import json
        data = json.loads(_get(RELEASES_API, timeout=20).decode("utf-8", "replace"))
        ver = (data.get("tag_name") or "").lstrip("v")
        url, name = "", ""
        for a in data.get("assets") or []:
            n = a.get("name") or ""
            if ASSET_RE.match(n):
                name, url = n, a.get("browser_download_url") or ""
        if url:
            sums = ""
            for a in data.get("assets") or []:
                if "sha256" in (a.get("name") or "").lower():
                    try:
                        sums = _get(a["browser_download_url"], timeout=20).decode(
                            "utf-8", "replace")
                    except Exception:
                        sums = ""
                    break
            return name, url, ver, sums
    except Exception:
        pass

    # B) 抓 releases 页面（官网 / API 被挡时的兜底）
    try:
        html = _get(RELEASES_PAGE, timeout=25).decode("utf-8", "replace")
        m = ASSET_URL_RE.search(html)
        if m:
            name = m.group(1)
            return name, "https://github.com" + m.group(0), "", ""
    except Exception:
        pass
    return "", "", "", ""


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "syncthing-installer"})
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except Exception:
        resp = urllib.request.build_opener(
            urllib.request.ProxyHandler({})).open(req, timeout=120)
    got = 0
    with io.open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
    return got


def expected_sha(sums_text, fname):
    if not sums_text:
        return ""
    for line in sums_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and os.path.basename(parts[-1]) == fname:
            return parts[0].lower()
        if len(parts) >= 2 and parts[0].lower().endswith(fname.lower()):
            return parts[-1].lower()
    return ""


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ------------------------------------------------------------------ 主流程
def main():
    force_utf8()
    say("=" * 60)
    say("  装 Syncthing · 多电脑同步的第一步")
    say("=" * 60)

    exe, how = detect()
    if exe and not FORCE:
        say("")
        say("[OK] 本机已经有 Syncthing，不需要再装。")
        say("     位置来源 : %s" % how)
        say("     exe 路径 : %s" % exe)
        say("     版本目录 : %s" % os.path.dirname(exe))
        say("")
        say("下一步：直接进「建四夹 → 双向配对」两步（见 SKILL.md §二）。")
        return 0

    if CHECK_ONLY:
        say("")
        say("[--] 本机未探测到 Syncthing。")
        say("     去掉 --check 再跑一次，本脚本会下载官方便携版并解压到位。")
        say("     默认安装位置：%s" % target_dir())
        return 1

    tdir = target_dir()
    say("")
    say("[..] 准备安装到：%s" % tdir)

    # 临时目录放**系统 temp**：一是不污染安装目录，二是万一清理不动也无残留。
    # （不建在 tdir 下的原因：有些沙箱环境会拦截删除调用，留在安装目录会碍事。）
    try:
        tmp = tempfile.mkdtemp(prefix="syncthing-install-")
    except Exception:
        tmp = os.path.join(tdir, ".install-tmp")
        try:
            os.makedirs(tmp)
        except Exception:
            pass

    local_zip = opt_value("--zip")
    name, url, ver, sums = "", "", "", ""

    if local_zip:
        if not os.path.isfile(local_zip):
            say("[X] --zip 给的文件不存在：%s" % local_zip)
            return 1
        name = os.path.basename(local_zip)
        zpath = local_zip
        m = ASSET_RE.match(name)
        ver = m.group(1) if m else ""
        say("[--] 用你已下好的 zip：%s" % name)
        if not m:
            say("[!!] 文件名不像官方 64 位便携版（应形如 syncthing-windows-amd64-v<版本>.zip）。")
            say("     仍会尝试安装，但请确认下的是 Windows 64 位便携版。")
    else:
        say("[..] 正在获取最新版信息 …")
        name, url, ver, sums = latest_asset()
        if not url:
            say("")
            say("[X] 取不到下载信息 —— 本机到 GitHub 的连接被挡住了。")
            print_proxy_hint()
            return 1
        say("     版本 %s  文件 %s" % (ver or "(未标注)", name))
        zpath = os.path.join(tmp, name)
        try:
            say("[..] 正在下载 …（约 20 MB，视网速）")
            size = download(url, zpath)
            say("     已下载 %.1f MB" % (size / 1048576.0))
        except Exception as e:
            say("[X] 下载中断：%s" % e)
            print_proxy_hint()
            return 1

    want = expected_sha(sums, name)
    if want:
        got = sha256_of(zpath)
        if got != want:
            say("[X] SHA256 校验失败：文件可能没下全，也可能被人替换过。已中止。")
            say("     期望 %s" % want)
            say("     实际 %s" % got)
            say("     文件保留在：%s" % zpath)
            return 1
        say("[OK] SHA256 校验通过")
    elif not local_zip:
        say("[--] 本次没取到官方 sha256 清单，跳过校验（只影响完整性确认，不影响可用性）")

    say("[..] 正在解压 …")
    stage = os.path.join(tmp, "stage")
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(stage)
    except Exception as e:
        say("[X] 解压失败：%s" % e)
        return 1

    found = ""
    for dp, _dn, fn in os.walk(stage):
        if EXE in fn:
            found = dp
            break
    if not found:
        say("[X] 解压结果里没有 %s，包可能不完整，已中止。" % EXE)
        return 1

    say("[..] 正在落位 …")
    try:
        os.makedirs(tdir)
    except Exception:
        pass
    ts = time.strftime("%Y%m%d-%H%M%S")
    for f in sorted(os.listdir(found)):
        src = os.path.join(found, f)
        dst = os.path.join(tdir, f)
        if os.path.isfile(src):
            if os.path.exists(dst):
                shutil.move(dst, dst + ".bak-" + ts)   # 永不覆盖
            shutil.move(src, dst)
        elif os.path.isdir(src):
            if not os.path.isdir(dst):
                shutil.move(src, dst)

    final = os.path.join(tdir, EXE)
    if not os.path.isfile(final):
        say("[X] 落位后没找到 %s，请检查目录权限。" % final)
        return 1

    # 临时目录在系统 temp 下，交给操作系统回收，这里不做删除式调用。

    say("")
    say("[OK] 装好了。")
    say("     exe 路径 : %s" % final)
    say("     下一步   : 建四夹 → 双向配对（见 SKILL.md §二 第 2、3 步）")
    say("     小提醒   : 这个目录要放在同步工作区**之外**，否则会自我同步。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
