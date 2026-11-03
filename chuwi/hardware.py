"""Bounded read-only Linux inventory and telemetry. No hardware probe writes."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

from .profiles import identify, read

STATUSES = ("Working", "Optional", "Missing", "Unsupported", "Conflicting", "Requires attention")


def capture(args, timeout=3):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"})
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class Reading:
    value: object = None
    unit: str = ""
    status: str = "Missing"
    source: str = ""
    detail: str = "Unavailable"


def attribute(path, unit="", divisor=1):
    if path is None:
        return asdict(Reading(unit=unit))
    try:
        text = Path(path).read_text().strip()
        value = float(text)/divisor if unit else text
        if isinstance(value, float) and not __import__('math').isfinite(value):
            raise ValueError("Nonfinite measurement")
        return asdict(Reading(value, unit, "Working", str(path), ""))
    except PermissionError:
        return asdict(Reading(unit=unit, status="Requires attention", source=str(path), detail="Permission denied"))
    except (OSError, ValueError):
        return asdict(Reading(unit=unit, source=str(path)))


def value(value, source="", detail=""):
    return asdict(Reading(value, status="Working" if value not in (None, "unknown") else "Missing", source=source, detail=detail))


def first(paths):
    return next(iter(sorted(paths)), None)


class Hardware:
    def __init__(self, root=Path("/"), runner=capture):
        self.root, self.run = Path(root), runner
        self.power_cache = None
        self.power_at = 0
        self.machine = identify(self.root)
        self.last_values = {}
        self.throttle_previous = None

    def snapshot(self, display=None, runtime=None):
        sys = self.root / "sys"
        machine = self.machine
        ec = sys / "devices/platform/minibook_ec"
        system = {key: value(machine.get(key) or None) for key in ("model", "cpu", "bios", "desktop", "session")}
        system.update(kernel=value(platform.release()), ec=attribute(ec / "ec_version"))
        thermals = {key: attribute(None, unit) for key, unit in
                    (("cpu", "°C"), ("soc", "°C"), ("charger", "°C"), ("fan", "RPM"), ("pwm", "%"))}
        for zone in sorted((sys / "class/thermal").glob("thermal_zone*")):
            name = read(zone / "type")
            key = {"minibook_soc": "soc", "minibook_charger": "charger", "x86_pkg_temp": "cpu"}.get(name)
            if key:
                thermals[key] = attribute(zone / "temp", "°C", 1000)
        for hwmon in sorted((sys / "class/hwmon").glob("hwmon*")):
            name = read(hwmon / "name")
            if name == "coretemp" and thermals["cpu"]["value"] is None:
                labels = [p for p in hwmon.glob("temp*_label") if "Package" in read(p)]
                temp = hwmon / (labels[0].name.replace("_label", "_input")) if labels else first(hwmon.glob("temp*_input"))
                thermals["cpu"] = attribute(temp, "°C", 1000)
            if name == "minibook_fan":
                thermals["fan"] = attribute(hwmon / "fan1_input", "RPM")
                thermals["pwm"] = attribute(hwmon / "pwm1", "%", 2.55)
                if thermals["fan"]["value"] == 0:
                    thermals["fan"]["status"] = "Requires attention"
                    thermals["fan"]["detail"] = "Driver reports zero: stopped or unreadable"
        for key in ('soc', 'charger'):
            thermals[key]['detail'] = (thermals[key]['detail']+'; ' if thermals[key]['detail'] else '')+'Estimated EC area temperature; not CPU die temperature'
        counters = sorted((sys / "devices/system/cpu").glob("cpu*/thermal_throttle/*throttle_count"))
        thermals["throttle_count"] = value(None, detail="No reliable throttling counter")
        thermals["throttle_delta"] = value(None, detail="Requires consecutive readable observations")
        if counters:
            readings = [attribute(p, "events") for p in counters]
            if all(r["value"] is not None for r in readings):
                current = max(r["value"] for r in readings)
                delta = max(0,current-self.throttle_previous) if self.throttle_previous is not None else None
                self.throttle_previous = current
                thermals["throttle_count"] = value(current,detail="Maximum cumulative counter; not current throttling state")
                thermals["throttle_delta"] = value(delta, detail="Counter change since previous observation")
        now = time.monotonic()
        if self.power_cache is None or now-self.power_at >= 30:
            self.power_cache, self.power_at = self.power(), now
        for key, reading in thermals.items():
            if reading['value'] is not None:
                self.last_values[key] = (reading.copy(), now)
            elif key in self.last_values:
                previous, stamp = self.last_values[key]
                reading.update(status='Requires attention', detail=f'Stale: last readable value {previous["value"]} {previous["unit"]}, {now-stamp:.0f}s ago')
        runtime = runtime or {}
        input_state = {"hinge": value(runtime.get("hinge_degrees")), "mode": value(runtime.get("posture", "unknown")),
                       "keyboard": attribute(ec / "keyboard_enabled"), "touchpad": attribute(ec / "touchpad_enabled"),
                       "touchscreen": value(runtime.get("touchscreen_state"))}
        if self.root == Path('/') and self.machine['session'] == 'x11':
            try:
                from chuwi_tablet import XInputBackend
                devices=XInputBackend().devices()
                for role in ('keyboard','touchpad','touchscreen'):
                    matches=[device for device in devices if device.role == role]
                    if input_state[role]['value'] is None and len(matches)==1:
                        input_state[role]=value('Enabled' if matches[0].enabled else 'Disabled',matches[0].identity)
            except Exception as exc:
                input_state['touchscreen']['detail']=str(exc)
        from .ec import EC
        input_state['writable']=value(EC(self.root).compatible())
        led = sys / "class/leds/minibook::kbd_backlight"
        input_state.update(backlight=attribute(led / "brightness", "level"),
                           max_brightness=attribute(led / "max_brightness", "level"),
                           auto_off_timer=attribute(led / "auto_off_timer"))
        display = display or {}
        screen = {key: value(display.get(key)) for key in ("name", "rotation", "refresh_hz", "panel_orientation")}
        screen.update(calibration=value(runtime.get("calibration", "unknown")), service=value(runtime.get("service", "unknown")))
        return {"schema_version": 1, "machine": machine, "system": system, "thermals": thermals,
                "power": self.power_cache, "input": input_state, "display": screen}

    def power(self):
        sys = self.root / "sys"
        batteries = [p for p in (sys / "class/power_supply").glob("*") if read(p / "type") == "Battery"]
        battery = first(batteries)
        sleep = read(sys / "power/mem_sleep")
        selected = re.search(r"\[([^]]+)\]", sleep)
        residency = sys / 'kernel/debug/pmc_core/slp_s0_residency_usec'
        power = {"s0ix_residency": attribute(residency, 'µs'),
                 "sleep_note": value('Selected s2idle does not establish S0ix residency'),
                 "battery_power": attribute(battery / 'power_now' if battery else None, 'W', 1_000_000),
                 "energy": attribute(battery / 'energy_now' if battery else None, 'Wh', 1_000_000),
                 "charge": attribute(battery / "capacity" if battery else None, "%"),
                 "state": attribute(battery / "status" if battery else None),
                 "sleep_active": value(selected[1] if selected else None),
                 "sleep_available": value(sleep.replace("[", "").replace("]", "").split()),
                 "sustained_limit": attribute(None, "W")}
        for node in sorted((sys / "class/powercap").glob("intel-rapl:*")):
            if not read(node / "name").startswith("package"):
                continue
            for name in node.glob("constraint_*_name"):
                if read(name) == "long_term":
                    power["sustained_limit"] = attribute(name.with_name(name.name.replace("_name", "_power_limit_uw")), "W", 1_000_000)
                    return power
        return power


def diagnostic(name, status, evidence, recommendation=""):
    assert status in STATUSES
    return {"name": name, "status": status, "evidence": evidence, "explanation": recommendation, "recommended_action": recommendation}


def audit(config=None, runtime=None, root=Path("/"), runner=capture):
    from .display import parse_verbose
    machine = identify(root)
    layout = runner(["xrandr", "--verbose"]) if machine["session"] == "x11" else ""
    displays = parse_verbose(layout)
    requested = (config or {}).get("display", "auto")
    internal = [d for d in displays if d["internal"] and d["active"] and (requested == "auto" or requested == d["name"])]
    display = internal[0] if len(internal) == 1 else {}
    snapshot = Hardware(root, runner).snapshot(display, runtime)
    checks = [diagnostic("Machine", "Working" if machine["profile"] != "generic" else "Unsupported", machine),
              diagnostic("Session", "Working" if machine["session"] == "x11" and machine["desktop"] != "unknown" else "Unsupported", machine["desktop"] + "/" + machine["session"]),
              diagnostic("Internal display", "Working" if display else "Missing", display or "No unique active internal display"),
              diagnostic("Refresh rate (read-only)", "Working" if display.get("refresh_hz") else "Missing", display.get("refresh_hz")),
              diagnostic("Calibration", "Working" if (config or {}).get("home_rotation") in ("normal", "right", "left", "inverted") else "Requires attention", (config or {}).get("home_rotation", "auto"), "Run calibrate-home in the correct laptop orientation")]
    for command in ("python3", "xrandr", "xinput", "udevadm", "systemctl"):
        checks.append(diagnostic(command, "Working" if shutil.which(command) else "Missing", shutil.which(command)))
    sys = root / "sys"
    sensors = [read(p / "name") for p in (sys / "bus/iio/devices").glob("iio:device*")]
    checks.append(diagnostic("IIO accelerometers", "Working" if sensors else "Optional", sensors,
                             "The extended proxy may own sensors outside IIO"))
    for module in ("minibook_ec", "goodix_ts", "i2c_designware_spklen", "dptf_enabler"):
        present = (sys / "module" / module).exists()
        checks.append(diagnostic(module, "Working" if present else "Optional", "Loaded" if present else "Not loaded"))
    for group in ("thermals", "input", "power"):
        for name, reading in snapshot[group].items():
            checks.append(diagnostic(group + "." + name, "Optional" if reading["status"] == "Missing" else reading["status"], reading))
    for service in ("iio-sensor-proxy", "thermald"):
        state = runner(["systemctl", "show", service, "-p", "ActiveState,FragmentPath,ExecStart"])
        checks.append(diagnostic(service, "Working" if "ActiveState=active" in state else "Requires attention", state or "Service query unavailable"))
    from .provenance import inspect
    provenance=inspect(runner,root)
    checks.append(diagnostic('Proxy provenance','Working' if provenance['verified_stock'] else 'Requires attention',provenance,
                             'Verify the versioned extension through introspection; orientation labels do not identify a provider'))
    packages = runner(["dpkg-query", "-W", "-f=${Package}\t${Version}\n", "iio-sensor-proxy", "thermald", "chuwi-iio-sensor-proxy"])
    checks.append(diagnostic("Sensor/thermal packages", "Working" if packages else "Requires attention", packages))
    secure = runner(["mokutil", "--sb-state"])
    checks.append(diagnostic("Secure Boot", "Working" if "enabled" in secure else "Requires attention", secure or "Unknown; module trust must be verified"))
    checks.append(diagnostic("DKMS", "Working" if shutil.which("dkms") else "Optional", runner(["dkms", "status"]), "Build and verify signatures for each target kernel before approval to load"))
    checks.append(diagnostic("Kernel headers", "Working" if (root / "lib/modules" / platform.release() / "build").exists() else "Optional", platform.release()))
    processes = runner(["ps", "-eo", "comm="]).splitlines()
    conflicts = [p.strip() for p in processes if p.strip() in ("iio-niri", "iio-sway", "iio-hyprland", "rotate-screen", "csd-orientation", "gsd-orientation")]
    checks.append(diagnostic("Rotation managers", "Conflicting" if conflicts else "Working", conflicts or "No known competing process found; this is not exhaustive"))
    for device in sorted((sys/'bus/i2c/devices').glob('*')):
        if 'goodix' in str((device/'driver').resolve()).lower() or 'GDIX' in device.name:
            checks.append(diagnostic('Touchscreen driver','Working',{'device':device.name,'driver':str((device/'driver').resolve())}))
    failed=[]
    for log in sorted((root/'var/lib/dkms').glob('*/*/*/*/log/make.log'))[-8:]:
        try:
            with log.open('rb') as stream:
                stream.seek(max(0,log.stat().st_size-8192));tail=stream.read().decode(errors='replace')
            errors=[line for line in tail.splitlines() if re.search(r'error:|fatal:|Error [1-9]',line)]
            if errors:failed.append({'log':str(log),'errors':errors[-8:]})
        except PermissionError:failed.append({'log':str(log),'error':'Permission denied'})
    checks.append(diagnostic('DKMS build failures','Requires attention' if failed else 'Optional',failed or 'No readable failure evidence'))
    checks.append(diagnostic("Tablet provider", "Working" if (runtime or {}).get("sensor_provider") == "extended" else "Optional",
                             (runtime or {}).get("sensor_provider", "Unknown without a running controller"), "Never infer proxy type or tablet mode from right-up"))
    logs = runner(['journalctl','-k','-b','--no-pager','-n','300','-o','cat'])
    symptoms = [line for line in logs.splitlines() if re.search(r'goodix|i2c|suspend|resume|s0ix|PM: suspend', line, re.I)][-60:]
    checks.append(diagnostic('Touch and suspend evidence','Requires attention' if symptoms else 'Optional',symptoms,
                             'Correlate timestamps with a reproduced fault before selecting replacement drivers'))
    thermal_logs = runner(['journalctl','-u','thermald','-b','--no-pager','-n','35','-o','cat'])
    checks.append(diagnostic('Stock thermald evidence','Working' if thermal_logs else 'Optional',thermal_logs,
                             'Collect temperature, battery power, fan observations and throttle deltas under comparable workloads; no write test'))
    return {"schema_version": 1, "hardware": snapshot, "checks": checks, "read_only": True}
