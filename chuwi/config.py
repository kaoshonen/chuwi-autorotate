"""Reversible, atomic migration. Reading status never migrates configuration."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

DEFAULTS = {"config_version": "2", "tablet_automatic": "false", "input_backend": "auto",
            "sensor_frame": "", "calibration_fingerprint": "", "tablet_osk": "manual"}


def atomic(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def migrate(path):
    path = Path(path)
    if not path.exists():
        return None
    original = path.read_bytes()
    values = dict(line.split("=", 1) for line in original.decode().splitlines()
                  if "=" in line and not line.lstrip().startswith("#"))
    version = values.get("config_version", "1").strip()
    if version == "2":
        return None
    if version != "1":
        raise ValueError("Configuration is from a newer version; migration refused")
    digest = hashlib.sha256(original).hexdigest()
    backup = path.with_name(path.name + ".v1-" + digest[:12])
    if not backup.exists():
        atomic(backup, original, 0o400)
    additions = "".join(f"{key}={value}\n" for key, value in DEFAULTS.items() if key not in values)
    if "config_version" in values:
        lines = original.decode().splitlines(keepends=True)
        original = "".join("config_version=2\n" if line.startswith("config_version=") else line for line in lines).encode()
    atomic(path, original.rstrip(b"\n") + b"\n" + additions.encode())
    return str(backup)


def fingerprint(display, provider, frame):
    keys = ("name", "identity", "panel_orientation", "transform", "reflection")
    data = {key: display.get(key) for key in keys}
    data.update(provider=provider, frame=frame)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
