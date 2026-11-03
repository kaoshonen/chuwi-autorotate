"""MiniBook X whole-pad filter: read-only discovery and transactional root operations.

Paths are injectable for disposable tests, never configurable by the privileged CLI.
"""
from contextlib import contextmanager
import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

AXES = ("00", "01", "35", "36")
RECOMMENDED = 8
PATTERN = "evdev:name:XXXX0000:05 0911:5288*:phys:i2c-XXXX0000:05:ev:*[89abcdef]:dmi:*:pnMiniBookX:*"
OLD_HEADER = "# Managed by chuwi_general/touchpad-jitter/touchpad-fix.sh v1"
HEADER = "# Managed by chuwi-trackpad v1"
COMMENTS = ("# Match old/new names on this MiniBook X; the final ev hex digit requires EV_ABS.\n"
            "# The relative mouse interface has no EV_ABS and must not receive this override.\n")


class TrackpadError(Exception):
    pass


def strength(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]?", str(value)) or not 1 <= int(value) <= 64:
        raise TrackpadError("Strength must be an integer from 1 to 64.")
    return int(value)


def render(value, header=HEADER):
    value = strength(value)
    return header + "\n" + COMMENTS + PATTERN + "\n" + "".join(
        f" EVDEV_ABS_{axis}=:::{value}\n" for axis in AXES) + "\n"


def parse_config(content):
    if content is None:
        return None
    for value in range(1, 65):
        if content in (render(value), render(value, OLD_HEADER)):
            return value
    raise TrackpadError("The jitter file contains unrelated or modified configuration; it was left unchanged.")


def digest(content):
    return hashlib.sha256((content or "").encode()).hexdigest()


def properties(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def read_file(path):
    if path.is_symlink():
        raise TrackpadError(f"Refusing symbolic link: {path}")
    try:
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 65536:
            raise TrackpadError(f"Unexpected configuration file: {path}")
        return path.read_text()
    except FileNotFoundError:
        return None


def atomic_write(path, content, mode=0o644):
    if path.is_symlink():
        raise TrackpadError(f"Refusing symbolic link: {path}")
    if content is None:
        path.unlink(missing_ok=True)
    else:
        fd, temporary = tempfile.mkstemp(prefix=".chuwi-trackpad-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class Backend:
    def __init__(self, root=Path("/"), runner=subprocess.run):
        self.root = Path(root)
        self.runner = runner
        self.target = self.root / "etc/udev/hwdb.d/63-chuwi-minibook-x-jitter.hwdb"
        self.state_dir = self.root / "var/lib/chuwi-trackpad"
        self.state_file = self.state_dir / "applied.json"

    def run(self, *args):
        result = self.runner(list(args), text=True, capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise TrackpadError(result.stderr.strip() or f"{args[0]} exited {result.returncode}")
        return result.stdout

    def device_properties(self, device):
        return properties(self.run("/usr/bin/udevadm", "info", "--query=property", "--path", device["sys_path"]))

    def discover(self):
        product = self.root / "sys/class/dmi/id/product_name"
        if not product.exists() or product.read_text().strip() != "MiniBook X":
            raise TrackpadError("This computer is not identified as a MiniBook X.")
        dmi = (product.parent / "modalias").read_text().strip()
        matches = []
        for event in sorted((self.root / "sys/class/input").glob("event*")):
            base = event / "device"
            try:
                identity = [(base / "id" / key).read_text().strip().lower() for key in ("bustype", "vendor", "product")]
                if identity != ["0018", "0911", "5288"]:
                    continue
                device = {"sys_path": str(event), "node": str(self.root / "dev/input" / event.name),
                          "name": (base / "name").read_text().strip(),
                          "phys": (base / "phys").read_text().strip(),
                          "ev": (base / "capabilities/ev").read_text().strip(), "identity": identity}
                props = self.device_properties(device)
                if props.get("ID_INPUT_TOUCHPAD") != "1":
                    continue
                device["lookup"] = f"evdev:name:{device['name']}:phys:{device['phys']}:ev:{device['ev']}:{dmi}"
                device["name_lookup"] = f"evdev:name:{device['name']}:{dmi}"
                if device["name"] not in ("XXXX0000:05 0911:5288", "XXXX0000:05 0911:5288 Touchpad") or not fnmatch.fnmatchcase(device["lookup"], PATTERN):
                    raise TrackpadError("Touchpad identity does not match the supported internal I²C device.")
                device["properties"] = props
                matches.append(device)
            except FileNotFoundError:
                continue
        if len(matches) != 1:
            raise TrackpadError(f"Expected one internal I²C touchpad; found {len(matches)}. Apply is unavailable.")
        return matches[0]

    def boot_id(self):
        return (self.root / "proc/sys/kernel/random/boot_id").read_text().strip()

    def status(self):
        result = {"schema_version": 1, "can_apply": False, "device": None, "saved_strength": None,
                  "selected_default": RECOMMENDED, "properties_match": False, "reboot": "unknown",
                  "errors": [], "diagnostics": {"configuration": str(self.target)}}
        try:
            content = read_file(self.target)
            result["saved_strength"] = parse_config(content)
            result["selected_default"] = result["saved_strength"] or RECOMMENDED
            config_ok = True
        except (OSError, TrackpadError) as exc:
            result["errors"].append(str(exc)); config_ok = False
        try:
            device = self.discover()
            props = device.pop("properties")
            result["device"] = device
            result["can_apply"] = config_ok
            selected = result["saved_strength"]
            result["properties_match"] = selected is not None and self.matching(props, selected)
            result["diagnostics"]["properties"] = {k: v for k, v in props.items() if k.startswith(("EVDEV_ABS_", "LIBINPUT_FUZZ_"))}
        except (OSError, TrackpadError, subprocess.SubprocessError) as exc:
            result["errors"].append(str(exc))
        try:
            metadata = json.loads(read_file(self.state_file) or "{}")
            if config_ok and content is None:
                result["reboot"] = "not_configured"
            if config_ok and metadata.get("digest") == digest(content) and metadata.get("boot_id"):
                result["reboot"] = "needed" if metadata["boot_id"] == self.boot_id() else "not_needed"
                result["last_backup"] = metadata.get("backup")
            result["diagnostics"]["apply_record"] = metadata
        except (ValueError, OSError, TrackpadError, AttributeError) as exc:
            result["diagnostics"]["apply_record_error"] = str(exc)
        return result

    @staticmethod
    def matching(props, value):
        return all(props.get(f"EVDEV_ABS_{a}") == f":::{value}" and props.get(f"LIBINPUT_FUZZ_{a}") == str(value) for a in AXES)

    def reload(self, device):
        self.run("/usr/bin/systemd-hwdb", "update")
        self.run("/usr/bin/udevadm", "trigger", "--action=change", device["sys_path"])
        self.run("/usr/bin/udevadm", "settle", "--timeout=10")
        # Re-discover after the trigger; a removed/replaced device must fail the transaction.
        current = self.discover()
        if any(current[k] != device[k] for k in ("sys_path", "name", "phys", "identity")):
            raise TrackpadError("Touchpad changed during the operation.")
        return current["properties"]

    @contextmanager
    def locked(self):
        self.state_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        if self.state_dir.is_symlink() or (self.state_dir.stat().st_mode & 0o022) or self.state_dir.stat().st_uid != os.geteuid():
            raise TrackpadError("Unsafe trackpad state directory.")
        fd = os.open(self.state_dir / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TrackpadError("Another trackpad operation is in progress.") from exc
            yield
        finally:
            os.close(fd)

    def apply(self, value):
        value = strength(value)  # Repeat validation inside the privileged helper.
        with self.locked():
            return self.transaction(render(value))

    def restore(self, backup_id):
        if not re.fullmatch(r"[0-9a-f]{32}", backup_id):
            raise TrackpadError("Invalid backup identifier.")
        with self.locked():
            saved = json.loads(read_file(self.state_dir / (backup_id + ".json")) or "null")
            if not isinstance(saved, dict) or saved.get("schema_version") != 1 or "configuration" not in saved:
                raise TrackpadError("Invalid or missing backup.")
            parse_config(saved["configuration"])
            return self.transaction(saved["configuration"])

    def transaction(self, content):
        value = parse_config(content)
        device = self.discover()
        before = read_file(self.target)
        parse_config(before)  # Never overwrite somebody else's configuration.
        old_record = read_file(self.state_file)
        token = uuid.uuid4().hex
        atomic_write(self.state_dir / (token + ".json"), json.dumps({
            "schema_version": 1, "configuration": before, "apply_record": old_record,
            "properties": device["properties"], "boot_id": self.boot_id()}, indent=2) + "\n", 0o600)
        try:
            atomic_write(self.target, content)
            props = self.reload(device)
            if value is not None and not self.matching(props, value):
                raise TrackpadError("The four system axes did not accept the saved strength (possibly a conflicting override).")
            if value is None:
                # Compare against a fresh compiled lookup after removing the managed override.
                expected = properties(self.run("/usr/bin/systemd-hwdb", "query", device["name_lookup"]))
                expected.update(properties(self.run("/usr/bin/systemd-hwdb", "query", device["lookup"])))
                for axis in AXES:
                    key = f"EVDEV_ABS_{axis}"
                    if props.get(key) != expected.get(key):
                        raise TrackpadError("Restored hardware database and device properties disagree.")
                    if key in expected:
                        fuzz = expected[key].split(":")[3]
                        if props.get(f"LIBINPUT_FUZZ_{axis}") != fuzz:
                            raise TrackpadError("Restored fuzz properties disagree with the hardware database.")
            atomic_write(self.state_file, json.dumps({"schema_version": 1, "boot_id": self.boot_id(),
                         "digest": digest(content), "backup": token}, indent=2) + "\n")
        except Exception as exc:
            try:
                atomic_write(self.target, before)
                atomic_write(self.state_file, old_record)
                restored = self.reload(device)
                for key, previous in device["properties"].items():
                    if key.startswith(("EVDEV_ABS_", "LIBINPUT_FUZZ_")) and restored.get(key) != previous:
                        raise TrackpadError(f"Rollback verification failed for {key}.")
            except Exception as rollback:
                raise TrackpadError(f"Save failed: {exc}. Rollback needs attention: {rollback}. Backup: {token}") from exc
            raise TrackpadError(f"Save failed; previous configuration restored: {exc}") from exc
        return {"type": "applied", "backup": token, "status": self.status()}
