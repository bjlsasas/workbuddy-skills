# -*- coding: utf-8 -*-
"""
Shared configuration layer for the multi-PC sync toolkit.

Design goal
-----------
Every script in this toolkit used to carry private constants inline:
a hard-coded Syncthing install directory, a specific workspace folder
name, a specific expert name, and (worst) two real device IDs used as
fallbacks.  A published copy must contain none of those.

This module replaces them with one JSON file plus runtime auto-detection:

    <script_dir>/sync-heal-config.json

    {
      "workspace": "D:\\\\MyWorkspace",          # optional; auto-detected
      "coop_dir": "21-SyncAndMigration",         # relative dir under workspace
      "folder_ids": ["wb-skills", "wb-experts-market",
                     "wb-experts-cache", "my-workspace"],
      "relay_subdir": "回传日志",
      "dist_subdir": "自愈程序",
      "market_name": "my-experts",
      "expert_name": "",                         # "" = auto-detect all
      "peer_id": "",                             # "" = detect at runtime
      "dead_ids": [],
      "sync_root_candidates": [],
      "relay_stale_hours": 72
    }

Anything omitted falls back to a sane default or is detected at runtime,
so the toolkit works out of the box on a fresh machine and only needs
editing when the user's layout differs from the defaults.

Every public helper is defensive: it never raises on a missing or broken
config file, because these scripts run unattended at logon.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_NAME = "sync-heal-config.json"
CONFIG_PATH = os.path.join(HERE, CONFIG_NAME)

# ---- defaults: deliberately generic, contain nothing machine-specific ----
DEFAULTS = {
    "workspace": "",
    "coop_dir": "21-SyncAndMigration",
    "folder_ids": [
        "wb-skills",
        "wb-experts-market",
        "wb-experts-cache",
        "my-workspace",
    ],
    "relay_subdir": "\u56de\u4f20\u65e5\u5fd7",          # 回传日志
    "dist_subdir": "\u81ea\u6108\u7a0b\u5e8f",            # 自愈程序
    "market_name": "my-experts",
    "expert_name": "",
    "peer_id": "",
    "dead_ids": [],
    "sync_root_candidates": [],
    "relay_stale_hours": 72,
}

_cache = None


def load(force=False):
    """Read the config file, merged over DEFAULTS. Never raises."""
    global _cache
    if _cache is not None and not force:
        return _cache
    data = {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            data = got
    except Exception:
        data = {}
    merged = dict(DEFAULTS)
    for k, v in data.items():
        if v is None:
            continue
        merged[k] = v
    _cache = merged
    return merged


def get(key, default=None):
    v = load().get(key, default)
    return DEFAULTS.get(key) if v in (None, "") and key in DEFAULTS else v


def workspace():
    """The synced workspace root.

    Order: config file -> ZGH_WORKSPACE env var -> '' (caller decides).
    """
    ws = load().get("workspace") or ""
    if ws and os.path.isdir(ws):
        return ws
    env = os.environ.get("ZGH_WORKSPACE") or ""
    if env and os.path.isdir(env):
        return env
    return ""


def _env(key):
    """Read a SYNCHEAL_* environment override, if any.

    Precedence is config file -> SYNCHEAL_* env var -> built-in default.
    The env layer exists so tests (and one-off runs) can redirect the
    workspace layout without writing a config file.
    """
    v = os.environ.get(key)
    return v.strip() if v and v.strip() else ""


def coop_dir_rel():
    """The co-operation folder as a *relative* path.

    Callers that already hold a workspace (or want to build a path they
    will later join) need the relative form, because joining an absolute
    path onto a workspace silently discards the workspace.
    """
    return (_env("SYNCHEAL_COOP_DIR")
            or load().get("coop_dir")
            or DEFAULTS["coop_dir"])


def _sub_dir(env_key, cfg_key):
    return _env(env_key) or load().get(cfg_key) or DEFAULTS[cfg_key]


def relay_subdir():
    return _sub_dir("SYNCHEAL_RELAY_SUBDIR", "relay_subdir")


def dist_subdir():
    return _sub_dir("SYNCHEAL_DIST_SUBDIR", "dist_subdir")


def coop_dir():
    """Absolute path of the co-operation folder inside the workspace."""
    ws = workspace()
    if not ws:
        return ""
    return os.path.join(ws, coop_dir_rel())


def relay_dir():
    """Where the two machines exchange log files."""
    c = coop_dir()
    if not c:
        return ""
    return os.path.join(c, relay_subdir())


def dist_dir():
    """The self-update distribution source."""
    c = coop_dir()
    if not c:
        return ""
    return os.path.join(c, dist_subdir())


def folder_ids():
    v = load().get("folder_ids")
    return list(v) if isinstance(v, list) and v else list(DEFAULTS["folder_ids"])


def market_name():
    return load().get("market_name") or DEFAULTS["market_name"]


def expert_name():
    """'' means: handle every expert found in the source package."""
    return load().get("expert_name") or ""


def peer_id():
    return load().get("peer_id") or ""


def dead_ids():
    v = load().get("dead_ids")
    return set(v) if isinstance(v, list) else set()


def sync_root_candidates():
    v = load().get("sync_root_candidates")
    return list(v) if isinstance(v, list) else []


def write_template(path=None, overwrite=False):
    """Emit a commented starter config. Used by the installer."""
    path = path or CONFIG_PATH
    if os.path.exists(path) and not overwrite:
        return path, False
    doc = {
        "_comment": "Multi-PC sync toolkit config. Delete any key to use its default.",
        "workspace": "D:\\\\MyWorkspace",
        "coop_dir": DEFAULTS["coop_dir"],
        "folder_ids": DEFAULTS["folder_ids"],
        "relay_subdir": DEFAULTS["relay_subdir"],
        "dist_subdir": DEFAULTS["dist_subdir"],
        "market_name": DEFAULTS["market_name"],
        "expert_name": "",
        "peer_id": "",
        "dead_ids": [],
        "sync_root_candidates": ["D:\\\\Tools\\\\syncthing"],
        "relay_stale_hours": 72,
    }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path, True


if __name__ == "__main__":
    import sys
    c = load()
    print("config file : %s" % CONFIG_PATH)
    print("exists      : %s" % os.path.isfile(CONFIG_PATH))
    print("workspace   : %r" % workspace())
    print("coop_dir    : %r" % coop_dir())
    print("relay_dir   : %r" % relay_dir())
    print("dist_dir    : %r" % dist_dir())
    print("folder_ids  : %s" % folder_ids())
    print("market_name : %s" % market_name())
    print("expert_name : %r" % expert_name())
    print("peer_id     : %r" % peer_id())
    print("dead_ids    : %s" % sorted(dead_ids()))
    print("sync roots  : %s" % sync_root_candidates())
    sys.exit(0)
