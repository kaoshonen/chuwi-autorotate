#!/usr/bin/env python3
"""Restore files and service state saved by install.sh; preserve logs and backups."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def restore(backup, home=None, systemctl=True):
    home = home or Path.home()
    manifest = json.loads((backup / "manifest.json").read_text())
    allowed = {".local/bin/chuwi-autorotate", ".local/bin/chuwi-autorotate-panel",
               ".local/bin/chuwi-control-panel", ".local/bin/chuwi-trackpad", ".config/chuwi-autorotate/config.conf", ".local/bin/chuwi",
               ".config/systemd/user/chuwi-autorotate.service",
               ".config/autostart/chuwi-autorotate.desktop",
               ".local/share/applications/chuwi-autorotate-panel.desktop",
               ".local/share/applications/chuwi-control-panel.desktop",
               ".local/bin/chuwi-tablet-guard", ".local/bin/chuwi_tablet.py", ".local/bin/chuwi_tablet_client.py",
               ".config/systemd/user/chuwi-tablet-guard.service",
               ".local/share/icons/hicolor/scalable/apps/chuwi-autorotate-panel.svg"}
    for entry in manifest["files"]:
        if entry["path"] not in allowed or not entry["backup"].isdigit():
            raise ValueError("Unexpected path in backup manifest")
        if entry["existed"] and not (backup / entry["backup"]).exists():
            raise ValueError("Incomplete backup")
    if systemctl:
        present = subprocess.run(["systemctl", "--user", "cat", "chuwi-tablet-guard.service"], capture_output=True).returncode == 0
        if present:
            subprocess.run(["systemctl", "--user", "stop", "chuwi-tablet-guard.service"], check=True)
        guard = home / ".local/bin/chuwi-tablet-guard"
        if guard.exists():
            subprocess.run([str(guard), "--restore"], check=True)
        subprocess.run(["systemctl", "--user", "disable", "--now", "chuwi-autorotate.service"], check=False)
    for entry in manifest["files"]:
        target = home / entry["path"]
        if entry["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            source = backup / entry["backup"]
            if source.is_dir():
                if target.exists(): shutil.rmtree(target)
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        else:
            if target.is_dir(): shutil.rmtree(target)
            else: target.unlink(missing_ok=True)
    if systemctl:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        if manifest["service"]["is-enabled"] == "enabled":
            subprocess.run(["systemctl", "--user", "enable", "chuwi-autorotate.service"], check=True)
        if manifest["service"]["is-active"] == "active":
            subprocess.run(["systemctl", "--user", "start", "chuwi-autorotate.service"], check=True)
    print("Previous installation restored. Configuration, logs and backups otherwise preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    restore(parser.parse_args().backup)
