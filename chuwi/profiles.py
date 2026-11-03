"""Capabilities and desktop identity, never machine-specific calibration."""
import os
from pathlib import Path


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return default


def identify(root=Path("/"), environment=None):
    env = os.environ if environment is None else environment
    dmi = root / "sys/class/dmi/id"
    vendor = read(dmi / "sys_vendor")
    model = read(dmi / "product_name")
    cpu = next((line.split(":", 1)[1].strip() for line in
                read(root / "proc/cpuinfo").splitlines() if line.startswith("model name")), "")
    desktop = (env.get("XDG_CURRENT_DESKTOP", "") + ":" + env.get("XDG_SESSION_DESKTOP", "")).lower()
    desktop = "cinnamon" if "cinnamon" in desktop else "xfce" if "xfce" in desktop else "unknown"
    profile = "generic"
    if "chuwi" in vendor.lower():
        if model.strip().lower() == "minibook x" and "N150" in cpu.upper():
            profile = "minibook-x-n150"
        elif model.strip().lower() == "minibook":
            profile = "minibook-legacy"
    return {"vendor": vendor, "model": model, "cpu": cpu, "bios": read(dmi / "bios_version"),
            "profile": profile, "desktop": desktop, "session": env.get("XDG_SESSION_TYPE", "unknown").lower()}


LOCKERS = {
    "cinnamon": [("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver")],
    "xfce": [("org.xfce.ScreenSaver", "/org/xfce/ScreenSaver"),
             ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver")],
}


def settings_command(desktop, section):
    if desktop == "cinnamon":
        return ["cinnamon-settings", section]
    if desktop == "xfce":
        return ["xfce4-display-settings"] if section == "display" else ["xfce4-power-manager", "--customize"]
    raise ValueError("Desktop settings are unavailable for this session")
