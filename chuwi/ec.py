"""Narrow sysfs EC adapter and leased transactions for the optional root helper."""
import json
import os
from pathlib import Path
import stat
import time
from .config import atomic
from .profiles import identify, read

BUS = "io.github.kaoshonen.ChuwiHardware1"
PATH = "/io/github/kaoshonen/ChuwiHardware1"


class EC:
    def __init__(self, root=Path("/")):
        self.root = Path(root)
        self.base = self.root / "sys/devices/platform/minibook_ec"
        self.led = self.root / "sys/class/leds/minibook::kbd_backlight"

    def compatible(self):
        manifest = self.root / "etc/chuwi-autorotate/hardware-approved.json"
        try:
            info = manifest.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                return False
            approved = json.loads(manifest.read_text())
            machine = identify(self.root)
            return (approved.get("enabled") is True and machine["profile"] == "minibook-x-n150"
                    and all(approved.get(k) == machine[k] for k in ("vendor", "model", "bios"))
                    and approved.get("ec_version") == read(self.base / "ec_version")
                    and bool(approved.get("ec_version")))
        except (OSError, ValueError):
            return False

    def states(self):
        values = [read(self.base / (role+"_enabled")) for role in ("keyboard", "touchpad")]
        if any(v not in ("0", "1") for v in values):
            raise RuntimeError("EC input state unavailable")
        return tuple(v == "1" for v in values)

    def set_inputs(self, keyboard, touchpad):
        # LTSM may change both devices. Restore the touchpad after the keyboard.
        for role, enabled in (("keyboard", keyboard), ("touchpad", touchpad)):
            path = self.base / (role+"_enabled")
            path.write_text("1" if enabled else "0")
        if self.states() != (keyboard, touchpad):
            raise RuntimeError("EC input readback does not match the request")

    def backlight(self, brightness=None, timer=None):
        if not self.compatible():
            raise RuntimeError("EC controls require a matching approved hardware manifest")
        if brightness is not None:
            maximum = int((self.led / "max_brightness").read_text())
            if type(brightness) is not int or not 0 <= brightness <= maximum:
                raise ValueError("Backlight level is outside the hardware range")
            path, text = self.led / "brightness", str(brightness)
        else:
            if type(timer) is not bool:
                raise ValueError("Backlight timeout is a boolean setting")
            path, text = self.led / "auto_off_timer", str(int(timer))
        path.write_text(text)
        if path.read_text().strip() != text:
            raise RuntimeError("EC backlight readback failed")


class Lease:
    def __init__(self, ec, journal, clock=lambda: time.clock_gettime(time.CLOCK_BOOTTIME)):
        self.ec, self.journal, self.clock = ec, Path(journal), clock
        self.owner = None
        self.uid = None
        self.deadline = 0

    def acquire(self, owner, uid):
        if self.owner or self.journal.exists():
            raise RuntimeError("Previous EC input transaction must be restored first")
        if not self.ec.compatible():
            raise RuntimeError("EC hardware is not approved for writable controls")
        keyboard, touchpad = self.ec.states()
        atomic(self.journal, json.dumps({"version": 1, "uid": uid,
                                       "keyboard": keyboard, "touchpad": touchpad}).encode())
        self.owner, self.uid = owner, uid
        self.deadline = self.clock()+5
        return keyboard, touchpad

    def check(self, owner):
        if owner != self.owner or not self.owner:
            raise RuntimeError("EC input lease belongs to another caller")
        if self.clock() >= self.deadline:
            self.restore()
            raise RuntimeError("EC input lease expired")

    def set(self, owner, keyboard, touchpad):
        self.check(owner)
        try:
            self.ec.set_inputs(keyboard, touchpad)
        except Exception:
            self.restore()
            raise

    def renew(self, owner):
        self.check(owner)
        self.deadline = self.clock()+5

    def restore(self):
        if self.journal.exists():
            info = self.journal.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeError("Unsafe EC recovery journal")
            saved = json.loads(self.journal.read_text())
            if saved.get("version") != 1 or any(type(saved.get(k)) is not bool for k in ("keyboard", "touchpad")):
                raise RuntimeError("Invalid EC recovery journal")
            self.ec.set_inputs(saved["keyboard"], saved["touchpad"])
            self.journal.unlink()
        self.owner, self.uid, self.deadline = None, None, 0

    def tick(self):
        if self.owner and self.clock() >= self.deadline:
            self.restore()


class Client:
    def __init__(self):
        from gi.repository import Gio
        self.connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def call(self, method, signature=None, values=(), timeout=3000):
        from gi.repository import Gio, GLib
        args = GLib.Variant(signature, values) if signature else None
        result = self.connection.call_sync(BUS, PATH, BUS, method, args, None,
                                           Gio.DBusCallFlags.NONE, timeout, None).unpack()
        return json.loads(result[0]) if result else None


class InputBackend:
    kind = "ec"

    def __init__(self, client=None):
        self.client = client or Client()
        self.original = None
        self.current = None

    def preflight(self):
        from chuwi_tablet import InputDevice, XInputBackend
        XInputBackend().preflight()
        caps = self.client.call("Acquire", timeout=2000)
        self.original = self.current = (caps["keyboard"], caps["touchpad"])
        return [InputDevice(role, role, role, "ec", "ec:"+role, "ec:"+role, enabled)
                for role, enabled in zip(("keyboard", "touchpad"), self.original)]

    def change(self, device, enabled):
        states = list(self.current)
        states[0 if device.role == "keyboard" else 1] = enabled
        self.client.call("SetInputs", "(bb)", tuple(states))
        self.current = tuple(states)

    def verify_touch(self):
        from chuwi_tablet import XInputBackend
        XInputBackend().verify_touch()

    def renew(self):
        self.client.call("Renew")

    def restore(self):
        self.client.call("Restore")


def select_backend(config):
    from chuwi_tablet import XInputBackend, TabletError
    selection = config.get("input_backend", "auto")
    if selection != "xinput":
        try:
            candidate = InputBackend()
            if candidate.client.call("Capabilities").get("writable"):
                return candidate
        except Exception:
            if selection == "ec":
                raise TabletError("EC helper unavailable; no input was changed")
    if selection == "ec":
        raise TabletError("EC controls are not approved for this hardware")
    return XInputBackend()
