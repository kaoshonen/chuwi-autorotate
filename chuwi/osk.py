"""Tablet-scoped keyboard settings with compare-before-restore ownership."""
import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
from .config import atomic
from .session import directory


class KeyboardPreferences:
    def __init__(self, factory=None, path=None):
        self.sync = lambda: None
        if factory is None:
            from gi.repository import Gio
            self.sync = Gio.Settings.sync
            def factory(schema):
                if not Gio.SettingsSchemaSource.get_default().lookup(schema, True):
                    raise RuntimeError('Screen keyboard settings are unavailable: '+schema)
                return Gio.Settings.new(schema)
        self.factory = factory
        self.path = path or directory(True) / "keyboard.json"

    def enable(self, desktop):
        if self.path.exists():
            self.restore()
        changes = [("org.cinnamon.desktop.a11y.applications", "screen-keyboard-enabled", True)] if desktop == "cinnamon" else [
            ("org.onboard.auto-show", "enabled", True)]
        saved = []
        for schema, key, new in changes:
            settings = self.factory(schema)
            old = settings.get_boolean(key)
            saved.append({"schema": schema, "key": key, "old": old, "set": new})
        atomic(self.path, json.dumps(saved).encode())
        try:
            for item in saved:
                if not self.factory(item["schema"]).set_boolean(item["key"], item["set"]):
                    raise RuntimeError("Screen keyboard preference could not be changed")
            self.sync()
            if desktop == 'xfce':
                self.start_onboard()
        except Exception:
            self.restore()
            raise

    def start_onboard(self):
        from gi.repository import Gio, GLib
        connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        owned=connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','NameHasOwner',
            GLib.Variant('(s)',('org.onboard.Onboard',)),None,Gio.DBusCallFlags.NO_AUTO_START,1000,None).unpack()[0]
        if owned:return
        executable=shutil.which('onboard')
        if not executable:raise RuntimeError('Onboard is not installed')
        process=subprocess.Popen([executable],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            start=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
            atomic(self.path.with_suffix('.process.json'),json.dumps({'pid':process.pid,'start':start,'command':executable}).encode())
        except Exception:
            process.terminate()
            raise

    def stop_onboard(self):
        path=self.path.with_suffix('.process.json')
        if not path.exists():return
        record=json.loads(path.read_text());pid=record['pid']
        try:
            proc=Path(f'/proc/{int(pid)}')
            start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
            command=(proc/'cmdline').read_bytes().split(b'\0')
            if proc.stat().st_uid == os.getuid() and start==record['start'] and record['command'].encode() in command:
                os.kill(pid,signal.SIGTERM)
        except FileNotFoundError:
            pass
        path.unlink()

    def restore(self):
        self.stop_onboard()
        if not self.path.exists():
            return
        saved = json.loads(self.path.read_text())
        allowed = {("org.cinnamon.desktop.a11y.applications", "screen-keyboard-enabled"),
                   ("org.onboard.auto-show", "enabled")}
        for item in saved:
            if (item["schema"], item["key"]) not in allowed:
                raise ValueError("Unexpected keyboard preference in recovery record")
            settings = self.factory(item["schema"])
            if settings.get_boolean(item["key"]) == item["set"]:
                if not settings.set_boolean(item["key"], item["old"]):
                    raise RuntimeError("Screen keyboard restoration failed")
        self.sync()
        self.path.unlink()


def toggle_onboard(connection):
    from gi.repository import Gio, GLib
    present = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
        "NameHasOwner", GLib.Variant("(s)", ("org.onboard.Onboard",)), None,
        Gio.DBusCallFlags.NO_AUTO_START, 1000, None).unpack()[0]
    if present:
        connection.call_sync("org.onboard.Onboard", "/org/onboard/Onboard/Keyboard", "org.onboard.Onboard.Keyboard",
                             "ToggleVisible", None, None, Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
    elif shutil.which("onboard"):
        subprocess.Popen(["onboard"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise RuntimeError("Onboard is not installed")
