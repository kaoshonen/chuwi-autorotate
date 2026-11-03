"""Private session state shared with the recovery UI; no persisted input lock."""
import json
import os
from pathlib import Path
import stat
import time
from .config import atomic


def directory(create=False):
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    info = base.stat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("No owned session runtime directory")
    path = base / "chuwi-companion"
    if path.is_symlink():
        raise RuntimeError("Unsafe session directory")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if path.exists() and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise RuntimeError("Session state must be private")
    return path


def write(name, value):
    if name not in ("controller", "tablet", "inhibit"):
        raise ValueError("Unknown session record")
    atomic(directory(True) / (name + ".json"), json.dumps({**value, "at": time.monotonic()}).encode())


def read(name, max_age=3):
    try:
        path = directory() / (name + ".json")
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            return {}
        data = json.loads(path.read_text())
        age = time.monotonic()-data["at"]
        return data if 0 <= age <= max_age else {}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {}


def inhibit():
    write("inhibit", {"inhibited": True})


def rearm():
    write("inhibit", {"inhibited": False})


def inhibited():
    return read("inhibit", float("inf")).get("inhibited", False)
