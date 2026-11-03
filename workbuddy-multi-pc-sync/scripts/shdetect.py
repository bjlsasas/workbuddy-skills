# -*- coding: utf-8 -*-
"""
Runtime auto-detection helpers.

The toolkit must work on a machine it has never seen: no config file, no
known paths, no known device IDs.  Everything it needs is discoverable:

  * where Syncthing lives  -> enumerate running processes, then parse
                              logon launchers, then a shallow disk scan
  * who am I / who is the peer -> the REST API tells us
  * which folders matter   -> any folder in the Syncthing config

Nothing here contains a machine-specific constant.
"""

import ctypes
import ctypes.wintypes as wt
import glob
import os
import re
import string
import sys

API = "http://127.0.0.1:8384"

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# ------------------------------------------------------------ process scan
class _PE32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def running_processes(name_filter=None):
    """[(pid, ppid, exe_name, full_path)] for live processes.

    Uses the Win32 snapshot API rather than shelling out to tasklist,
    because tasklist's CSV output is locale-dependent and mangles commas.
    """
    out = []
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return out
        pe = _PE32()
        pe.dwSize = ctypes.sizeof(_PE32)
        ok = k32.Process32First(snap, ctypes.byref(pe))
        while ok:
            nm = pe.szExeFile.decode("mbcs", "replace")
            if not name_filter or name_filter.lower() in nm.lower():
                path = ""
                h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                    False, pe.th32ProcessID)
                if h:
                    buf = ctypes.create_unicode_buffer(2048)
                    sz = wt.DWORD(2048)
                    if k32.QueryFullProcessImageNameW(h, 0, buf,
                                                      ctypes.byref(sz)):
                        path = buf.value
                    k32.CloseHandle(h)
                out.append((pe.th32ProcessID, pe.th32ParentProcessID, nm, path))
            ok = k32.Process32Next(snap, ctypes.byref(pe))
        k32.CloseHandle(snap)
    except Exception:
        pass
    return out


def is_running(exe_name):
    return bool(running_processes(exe_name))


def port_open(host="127.0.0.1", port=8384, timeout=1.5):
    """True if something accepts a TCP connection there.

    Note: on some sandboxes bind() succeeds spuriously, so we connect
    rather than bind.
    """
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


# ------------------------------------------------------- syncthing location
def _drive_roots():
    return [c + ":\\" for c in string.ascii_uppercase if os.path.isdir(c + ":\\")]


def sync_home_from_process():
    """Derive --home from a live syncthing.exe command line.

    The portable layout is  <dir>\\syncthing.exe  +  <dir>\\data\\config.xml
    A running process may carry an explicit --home=<path>; if it does, that
    wins, because it is what the process is actually using.
    """
    procs = running_processes("syncthing")
    if not procs:
        return ""
    # prefer the process whose path ends in syncthing.exe
    cands = [p for _, _, n, p in procs if n.lower() == "syncthing.exe" and p]
    for p in cands:
        d = os.path.dirname(p)
        if os.path.isfile(os.path.join(d, "data", "config.xml")):
            return d
    return ""


def sync_home_from_launcher():
    """Parse logon launchers (*.bat) for a syncthing.exe path."""
    homes = []
    dirs = []
    up = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    dirs.append(os.path.join(up, "Desktop"))
    # honour a relocated desktop
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer"
            r"\User Shell Folders")
        d = winreg.QueryValueEx(k, "Desktop")[0]
        dirs.append(os.path.expandvars(d))
    except Exception:
        pass
    dirs.append(os.path.join(os.environ.get("APPDATA", ""),
                             r"Microsoft\Windows\Start Menu\Programs\Startup"))
    dirs.append(os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"),
                             "Desktop"))
    seen = set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for pat in ("*.bat", "*.cmd"):
            for bat in glob.glob(os.path.join(d, pat)) + \
                    glob.glob(os.path.join(d, "*", pat)):
                rp = os.path.realpath(bat)
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    t = open(bat, "rb").read().decode("utf-8", "replace")
                except Exception:
                    continue
                for m in re.finditer(r'"?([A-Za-z]:[^"\r\n]*?syncthing\.exe)',
                                     t, re.I):
                    cand = m.group(1).strip().strip('"')
                    if os.path.isfile(cand):
                        homes.append(os.path.dirname(cand))
    return homes[0] if homes else ""


def sync_home_shallow_scan(budget=40):
    """Last resort: shallow scan for <anything>\\data\\config.xml.

    Bounded by a wall-clock budget so an unattended logon task can never
    hang on a slow disk.
    """
    import time
    t0 = time.time()
    for drv in _drive_roots():
        for sub in _walk_limited(drv, 3):
            if time.time() - t0 > budget:
                return ""
            if os.path.isfile(os.path.join(sub, "data", "config.xml")):
                return sub
    return ""


def _walk_limited(root, max_depth):
    root = os.path.abspath(root)
    base = root.rstrip("\\").count("\\")
    for dp, dn, fn in os.walk(root, onerror=lambda e: None):
        depth = os.path.abspath(dp).count("\\") - base
        if depth >= max_depth:
            dn[:] = []
        dn[:] = [d for d in dn if d not in (
            "Windows", "$Recycle.Bin", "System Volume Information",
            "node_modules", "AppData")]
        yield dp


def find_sync_home(extra=None):
    """Best-effort Syncthing home. '' if not found."""
    for c in (extra or []):
        if c and os.path.isfile(os.path.join(c, "data", "config.xml")):
            return c
        if c and os.path.isfile(os.path.join(c, "config.xml")):
            return os.path.dirname(c)
    for fnc in (sync_home_from_process, sync_home_from_launcher):
        try:
            p = fnc()
            if p and os.path.isfile(os.path.join(p, "data", "config.xml")):
                return p
        except Exception:
            pass
    for base in (os.path.join(os.path.expanduser("~"), "AppData", "Local",
                              "Syncthing"),
                 os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                              "Syncthing")):
        if os.path.isfile(os.path.join(base, "config.xml")):
            return base
    return sync_home_shallow_scan()


def find_config_xml(extra=None):
    """Absolute path of config.xml, '' if not found."""
    h = find_sync_home(extra)
    if not h:
        return ""
    for cand in (os.path.join(h, "data", "config.xml"),
                 os.path.join(h, "config.xml")):
        if os.path.isfile(cand):
            return cand
    return ""


# ---------------------------------------------------------------- REST I/O
def opener():
    """An opener that bypasses any system HTTP proxy.

    A local proxy (http_proxy=127.0.0.1:xxxx) turns 127.0.0.1:8384 into a
    502 Bad Gateway and looks exactly like a crashed daemon.
    """
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_api_key(config_xml):
    """Pull <apikey> out of config.xml. '' if absent."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(config_xml).getroot()
        el = root.find("gui").find("apikey")
        return (el.text or "").strip() if el is not None else ""
    except Exception:
        return ""


def api(method, path, key, body=None, timeout=20, base=API):
    """(status, raw_text). status 0 means a transport-level failure."""
    import json as _json
    import urllib.error
    import urllib.request
    data = None
    hdr = {"X-API-Key": key} if key else {}
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = _json.dumps(body).encode("utf-8")
            hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=hdr,
                                 method=method)
    try:
        r = opener().open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def apij(method, path, key, body=None, timeout=20, base=API):
    import json as _json
    code, raw = api(method, path, key, body, timeout, base)
    try:
        return code, _json.loads(raw)
    except Exception:
        return code, None


def my_id(key):
    _, st = apij("GET", "/rest/system/status", key)
    return (st or {}).get("myID") or ""


def peer_ids(key, exclude=None):
    """Every device in the global table except ourselves."""
    me = exclude or my_id(key)
    _, devs = apij("GET", "/rest/config/devices", key)
    out = []
    for d in (devs or []):
        did = (d or {}).get("deviceID") or ""
        if did and did != me:
            out.append(did)
    return out


def peer_id(key):
    p = peer_ids(key)
    return p[0] if p else ""


# ------------------------------------------------------------------ desktop
def real_desktop():
    """The actual desktop path (it is often relocated off C:)."""
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer"
            r"\User Shell Folders")
        v = winreg.QueryValueEx(k, "Desktop")[0]
        return os.path.expandvars(v)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


# --------------------------------------------------------------- console
def force_utf8():
    """Child-process stdout degrades to the ANSI codepage when piped.

    Without this, Chinese output captured by a parent process arrives
    garbled, while the same text printed directly looks fine.
    """
    for s in ("stdout", "stderr"):
        try:
            f = getattr(sys, s, None)
            if f is not None and hasattr(f, "reconfigure"):
                f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


if __name__ == "__main__":
    force_utf8()
    print("running syncthing : %s" % is_running("syncthing"))
    print("port 8384 open    : %s" % port_open())
    print("sync home         : %r" % find_sync_home())
    print("config xml        : %r" % find_config_xml())
    print("real desktop      : %r" % real_desktop())
    cx = find_config_xml()
    if cx:
        k = read_api_key(cx)
        print("api key           : %s" % ("(found, %d chars)" % len(k) if k else "(none)"))
        if k:
            print("my id             : %s" % my_id(k))
            print("peers             : %s" % [p[:7] for p in peer_ids(k)])
