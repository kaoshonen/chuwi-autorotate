"""Fail-open tablet input policy and identity-checked X11 backend."""
from dataclasses import asdict, dataclass
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

BUS = "io.github.kaoshonen.ChuwiTablet"
OBJECT = "/io/github/kaoshonen/ChuwiTablet"
SERVICE = "chuwi-tablet-guard.service"
TRIAL_SECONDS = 10
LEASE_SECONDS = 5


class TabletError(RuntimeError):
    pass


def boot_time():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def capture(args, timeout=2):
    try:
        environment = {key:value for key,value in os.environ.items()
                       if key not in ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID")}
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                env={**environment, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise TabletError(f"{args[0]}: {exc}") from exc
    if result.returncode:
        raise TabletError((result.stderr or result.stdout).strip() or f"{args[0]} failed")
    return result.stdout


def runtime_directory():
    root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    info = root.stat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
        raise TabletError("No private user runtime directory is available")
    directory = root / "chuwi-tablet"
    if directory.is_symlink():
        raise TabletError("The tablet runtime directory must not be a symlink")
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid():
        raise TabletError("Unexpected tablet runtime directory owner")
    directory.chmod(0o700)
    return directory


def write_session_environment():
    directory = runtime_directory()
    values = {key:os.environ.get(key, "") for key in
              ("DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP")}
    if values["XDG_SESSION_TYPE"] != "x11" or not values["DISPLAY"]:
        raise TabletError("Tablet mode requires the active X11 desktop session")
    if not values["DBUS_SESSION_BUS_ADDRESS"]:
        values["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{os.getuid()}/bus"
    lines = []
    for key,value in values.items():
        if "\n" in value or "\r" in value:
            raise TabletError("Invalid desktop session environment")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    with tempfile.NamedTemporaryFile(mode="w",dir=directory,delete=False) as handle:
        temporary=Path(handle.name)
        try:
            handle.write("\n".join(lines)+"\n")
            handle.close()
            temporary.replace(directory/"session.env")
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class InputDevice:
    xid: str
    name: str
    role: str
    node: str
    syspath: str
    identity: str
    enabled: bool

    def same_hardware(self, other):
        return (self.role, self.syspath, self.identity) == (other.role, other.syspath, other.identity)


def classify_device(properties, syspath, kind):
    """Names and XInput IDs never establish that a device is built in."""
    path = syspath.lower()
    if any(part in path for part in ("/usb", "/bluetooth", "/virtual/")):
        return None
    i2c = "/i2c-" in path and "/devices/" in path
    if properties.get("ID_INPUT_TOUCHSCREEN") == "1":
        return "touchscreen" if i2c and kind == "pointer" else None
    if (properties.get("ID_INPUT_KEYBOARD") == "1" and properties.get("ID_BUS") == "i8042"
            and "/devices/platform/i8042/serio" in path and kind == "keyboard"):
        return "keyboard"
    if properties.get("ID_INPUT_TOUCHPAD") == "1" and i2c and kind == "pointer":
        return "touchpad"
    return None


class XInputBackend:
    kind = "xinput"
    def __init__(self, runner=capture, sysroot=Path("/sys/class/input")):
        self.run = runner
        self.sysroot = sysroot
        self.properties = {}

    def devices(self):
        if os.environ.get("XDG_SESSION_TYPE") != "x11":
            raise TabletError("Tablet input lock requires a supported X11 session")
        output = self.run(["xinput", "--list", "--short"])
        devices = []
        for line in output.splitlines():
            match = re.search(r"\bid=(\d+)\s+\[(?:slave\s+(pointer|keyboard)\b|floating\s+slave\b)", line)
            if not match:
                continue
            xid, kind = match.groups()
            props = self.run(["xinput", "list-props", xid])
            node = re.search(r'Device Node \(\d+\):\s*"(/dev/input/event\d+)"', props)
            enabled = re.search(r"Device Enabled \(\d+\):\s*([01])\b", props)
            if not node or not enabled:
                continue
            syspath = str((self.sysroot / Path(node[1]).name / "device").resolve(strict=True))
            key = (node[1], syspath)
            if key not in self.properties:
                info = self.run(["udevadm", "info", "--query=property", f"--name={node[1]}"])
                self.properties[key] = dict(line.split("=", 1) for line in info.splitlines() if "=" in line)
            properties = self.properties[key]
            if kind is None:
                kind = ("keyboard" if properties.get("ID_BUS") == "i8042" and properties.get("ID_INPUT_KEYBOARD") == "1"
                        else "pointer" if properties.get("ID_INPUT_TOUCHPAD") == "1" or properties.get("ID_INPUT_TOUCHSCREEN") == "1"
                        else None)
            from chuwi.inputs import enrolled_role
            role = classify_device(properties, syspath, kind) or enrolled_role(properties, syspath, kind)
            if role:
                name = re.sub(r"^[^\w]+", "", line[:match.start()]).strip()
                identity = properties.get("ID_PATH", "")
                if not identity:
                    raise TabletError(f"Cannot establish a stable hardware identity for {name}")
                devices.append(InputDevice(xid, name, role, node[1], syspath, identity, enabled[1] == "1"))
        return devices

    def preflight(self):
        if self.run is capture and self.sysroot == Path('/sys/class/input'):
            from chuwi.provenance import require_application_owner
            try:
                require_application_owner()
                from chuwi.display import parse_verbose
                internal = [d for d in parse_verbose(capture(['xrandr','--verbose'])) if d['internal'] and d['active']]
                if len(internal) != 1:
                    raise RuntimeError('A unique active internal display is required for touchscreen recovery')
            except RuntimeError as exc:
                raise TabletError(str(exc)) from exc
        from chuwi.profiles import read
        if any("MXC6655 Tablet Mode Control" in read(path) for path in
               Path("/sys/class/input").glob("event*/device/name")):
            raise TabletError("Upstream tablet-switch control is active; refusing competing input management")
        devices = self.devices()
        targets = []
        for role in ("keyboard", "touchpad"):
            matches = [device for device in devices if device.role == role]
            if len(matches) != 1:
                raise TabletError(f"Cannot uniquely identify the built-in {role}")
            targets.append(matches[0])
        if not any(d.role == "touchscreen" and d.enabled for d in devices):
            raise TabletError("An enabled internal touchscreen is required before locking input")
        return targets

    def change(self, device, enabled):
        matches = [d for d in self.devices() if d.same_hardware(device)]
        if not matches:
            if enabled and not Path(device.syspath).exists():
                return  # Unplugged/replaced devices must never be addressed using their old IDs.
            raise TabletError(f"Cannot identify present input hardware: {device.name}; recovery record retained")
        if len(matches) != 1:
            raise TabletError("Input identity is ambiguous; refusing to change a device")
        current = matches[0]
        if current.enabled != enabled:
            self.run(["xinput", "enable" if enabled else "disable", current.xid])
        verified = [d for d in self.devices() if d.same_hardware(device)]
        if not verified and (not enabled or Path(device.syspath).exists()):
            raise TabletError(f"Input disappeared from XInput during verification: {device.name}")
        if verified and (len(verified) != 1 or verified[0].enabled != enabled):
            raise TabletError(f"Could not verify input state for {device.name}")

    def verify_touch(self):
        if not any(d.role == "touchscreen" and d.enabled for d in self.devices()):
            raise TabletError("Touchscreen recovery is unavailable")


class RecoveryStore:
    def __init__(self, directory=None, backend_kind="xinput"):
        self.directory = directory if directory is not None else runtime_directory()
        self.path = self.directory / "recovery.json"
        self.lock_depth = 0
        self.backend_kind = backend_kind

    @contextmanager
    def transaction(self):
        if self.lock_depth:
            self.lock_depth += 1
            try:
                yield
            finally:
                self.lock_depth -= 1
            return
        descriptor = os.open(self.directory / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.lock_depth = 1
            try:
                yield
            finally:
                self.lock_depth = 0
        finally:
            os.close(descriptor)

    def save(self, targets):
        if self.path.exists():
            raise TabletError("Previous input recovery must finish before starting another lock")
        record = {"version": 1, "display": os.environ.get("DISPLAY", ""),
                  "xauthority": os.environ.get("XAUTHORITY", ""),
                  "backend": self.backend_kind,
                  "targets": [asdict(d) for d in targets if d.enabled]}
        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(record, handle)
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def load(self):
        if not self.path.exists():
            return None
        if self.path.is_symlink() or self.path.stat().st_uid != os.getuid():
            raise TabletError("Unexpected input recovery file")
        record = json.loads(self.path.read_text())
        if record.get("version") != 1 or len(record.get("targets", [])) > 2:
            raise TabletError("Invalid input recovery record")
        targets = [InputDevice(**entry) for entry in record["targets"]]
        if any(d.role not in ("keyboard", "touchpad") or d.enabled is not True for d in targets):
            raise TabletError("Invalid input recovery targets")
        return record, targets

    def restore(self, backend):
        with self.transaction():
            self._restore(backend)

    def _restore(self, backend):
        if self.path.exists():
            record = json.loads(self.path.read_text())
            if record.get("backend") == "ec":
                if self.path.is_symlink() or self.path.stat().st_uid != os.getuid():
                    raise TabletError("Unexpected EC input recovery file")
                from chuwi.ec import Client
                Client().call("Restore")
                self.path.unlink()
                return
        loaded = self.load()
        if loaded is None:
            return
        record, targets = loaded
        for name, value in (("DISPLAY", record["display"]), ("XAUTHORITY", record["xauthority"])):
            if value:
                os.environ[name] = value
        os.environ["XDG_SESSION_TYPE"] = "x11"
        errors = []
        for target in targets:
            try:
                backend.change(target, True)
            except (TabletError, OSError, ValueError) as exc:
                errors.append(str(exc))
        if errors:
            raise TabletError("; ".join(errors))
        self.path.unlink(missing_ok=True)


class TabletPolicy:
    def __init__(self, backend, store, clock=boot_time):
        self.backend, self.store, self.clock = backend, store, clock
        self.phase = "off"
        self.owner = None
        self.lease_until = 0
        self.trial_until = 0
        self.confirmed_owners = set()
        self.confirmed_hardware = {}
        self.reason = "Keyboard and touchpad are available"
        self.targets = []

    def status(self):
        return {"phase": self.phase, "reason": self.reason,
                "remaining": max(0, math.ceil(self.trial_until-self.clock())) if self.phase == "trial" else 0,
                "targets": [d.name for d in self.targets]}

    def begin(self, owner, armed, screen_locked=False):
        if not armed:
            raise TabletError("The independent recovery watchdog is not armed")
        if screen_locked:
            raise TabletError("Unlock the desktop before starting tablet mode")
        if self.phase != "off":
            raise TabletError("Tablet mode is already active or recovery is pending")
        with self.store.transaction():
            return self._begin(owner)

    def _begin(self, owner):
        targets = self.backend.preflight()
        identity = (getattr(self.backend, "kind", "xinput"), tuple((d.role, d.syspath, d.identity) for d in targets))
        if self.confirmed_hardware.get(owner) != identity:
            self.confirmed_owners.discard(owner)
        self.transaction_identity = identity
        self.store.save(targets)  # Durable recovery instructions precede the first disable.
        self.targets = targets
        try:
            for target in targets:
                if target.enabled:
                    self.backend.change(target, False)
            self.backend.verify_touch()
        except Exception:
            self.unlock("Input lock failed; original input restored")
            raise
        self.owner = owner
        self.lease_until = self.clock()+LEASE_SECONDS
        self.trial_until = self.clock()+TRIAL_SECONDS
        self.phase = "locked" if owner in self.confirmed_owners else "trial"
        self.reason = "Tablet mode on" if self.phase == "locked" else "Tap to keep tablet mode; otherwise it automatically unlocks"
        return self.status()

    def check_owner(self, owner):
        if not self.owner or owner != self.owner:
            raise TabletError("Only the panel that started tablet mode may renew or confirm it")

    def renew(self, owner):
        self.tick()
        self.check_owner(owner)
        if getattr(self.backend, "kind", "xinput") == "ec":
            self.backend.renew()
        self.lease_until = self.clock()+LEASE_SECONDS
        return self.status()

    def confirm(self, owner):
        self.tick()
        self.check_owner(owner)
        if self.phase != "trial":
            raise TabletError("There is no active touchscreen trial to confirm")
        self.confirmed_owners.add(owner)
        self.confirmed_hardware[owner] = self.transaction_identity
        self.phase = "locked"
        self.reason = "Tablet mode on"
        return self.status()

    def unlock(self, reason="Keyboard and touchpad unlocked"):
        self.owner = None
        self.phase = "recovering"
        try:
            self.store.restore(self.backend)
        except Exception as exc:
            self.phase = "error"
            self.reason = f"Input recovery pending: {exc}"
            raise TabletError(self.reason) from exc
        self.phase = "off"
        self.targets = []
        self.reason = reason
        return self.status()

    def tick(self):
        now = self.clock()
        if self.phase in ("trial", "locked"):
            if not self.store.path.exists():
                self.unlock("Input was independently restored")
            elif now >= self.lease_until:
                self.unlock("Panel stopped responding; input automatically restored")
            elif self.phase == "trial" and now >= self.trial_until:
                self.unlock("Trial expired; input automatically restored")
        elif self.phase == "error":
            self.unlock("Input recovery completed")

    def owner_lost(self, owner):
        self.confirmed_owners.discard(owner)
        self.confirmed_hardware.pop(owner, None)
        if self.owner == owner:
            self.unlock("Panel closed; input automatically restored")


def floating_position(workarea, size, margin=12, *, bottom=False):
    x, y, width, height = workarea
    w, h = size
    vertical = y+height-h-margin if bottom else min(y+margin, y+height-h)
    return max(x, x+width-w-margin), max(y, vertical)
