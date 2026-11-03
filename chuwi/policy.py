"""Pure posture/rotation decisions shared by the daemon and recovery UI."""
from dataclasses import dataclass
import math
import time

ROTATIONS = ("normal", "right", "inverted", "left")
SENSORS = ("normal", "right-up", "bottom-up", "left-up")


@dataclass(frozen=True)
class Decision:
    posture: str
    rotation: str | None
    lock_inputs: bool
    reason: str = ""


def decide(config, sensor, profile, *, manual=False, inhibited=False, now=None):
    now = time.monotonic() if now is None else now
    home = config.get("home_rotation", "auto")
    home = home if home in ROTATIONS else None
    fresh = sensor.fresh(now)
    posture = "tablet" if manual else sensor.mode if fresh else "unknown"
    if inhibited:
        posture = "inhibited"
    locked = config.get("rotation_locked") == "true"
    rotation = config.get("locked_rotation", home) if locked else None
    if rotation not in ROTATIONS:
        rotation = home if locked else None
    if locked:
        return Decision(posture, rotation, posture == "tablet" and fresh and sensor.input_owner == "application" and not inhibited, "Manual rotation lock")
    if posture in ("laptop", "inhibited"):
        return Decision(posture, home, False, "Calibrated laptop orientation")
    if posture == "unknown" and profile == "minibook-x-n150":
        return Decision(posture, home, False, "Select tablet mode; reliable hinge data is unavailable")
    if not fresh or sensor.orientation not in SENSORS:
        return Decision(posture, None, False, "Waiting for valid sensor data")
    anchor = config.get("sensor_frame", "")
    if (anchor and anchor != sensor.frame) or (sensor.provider == "extended" and not anchor):
        return Decision(posture, home, False, "Calibrate HOME for this sensor coordinate frame")
    if config.get("calibration_valid") == "false":
        return Decision(posture, home, False, "Display or sensor calibration changed; validate HOME")
    offset = config.get("orientation_offset", "right")
    if offset not in ROTATIONS:
        return Decision(posture, home, False, "Invalid orientation calibration")
    rotation = ROTATIONS[(SENSORS.index(sensor.orientation) + ROTATIONS.index(offset)) % 4]
    blacklist = config.get("blacklist_rotations", "").replace(",", " ").split()
    return Decision(posture, None if rotation in blacklist else rotation,
                    posture == "tablet" and sensor.input_owner == "application")


class RotationConflict:
    """Stop competing with another manager rather than repeatedly modesetting."""
    def __init__(self):
        self.events = []
        self.inhibited = False

    def observe(self, unexpected, now):
        self.events = [value for value in self.events if now-value < 15]
        if unexpected:
            self.events.append(now)
        if len(self.events) >= 3:
            self.inhibited = True
        return self.inhibited
