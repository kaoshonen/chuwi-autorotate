"""Bounded read-only JSON-lines measurement protocol (one authenticated process)."""
import json
import os
import select
import stat
import time

from . import trackpad_recorder as recorder
from .trackpad import TrackpadError

REGIONS = ("top", "middle", "bottom")


class Cancelled(Exception):
    pass


class Channel:
    """Use raw bounded reads; TextIO buffering can hide queued cancel messages."""
    def __init__(self, input_fd, output, clock=time.monotonic):
        self.input_fd, self.output, self.clock = input_fd, output, clock
        self.pending = b""
        self.deadline = clock() + 210

    def emit(self, event):
        try:
            self.output.write(json.dumps(event, allow_nan=False) + "\n")
            self.output.flush()
        except (BrokenPipeError, OSError) as exc:
            raise Cancelled("Control panel disconnected.") from exc

    def poll(self, wait=0):
        if self.clock() >= self.deadline:
            raise recorder.CaptureError("Measurement session timed out.")
        if not select.select([self.input_fd], [], [], wait)[0]:
            return []
        data = os.read(self.input_fd, 4096)
        if not data:
            raise Cancelled("Control panel disconnected.")
        self.pending += data
        if len(self.pending) > 4096:
            raise recorder.CaptureError("Protocol message too large.")
        messages = []
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise recorder.CaptureError("Invalid protocol message.") from exc
            if not isinstance(msg, dict) or msg.get("command") not in ("ready", "cancel"):
                raise recorder.CaptureError("Expected ready or cancel.")
            if msg["command"] == "cancel":
                raise Cancelled("Measurement cancelled.")
            messages.append(msg)
        return messages

    def ready(self, region):
        self.emit({"type": "ready", "region": region, "timeout_seconds": 45})
        deadline = self.clock() + 45
        while self.clock() < deadline:
            for message in self.poll(.1):
                if message.get("region") == region:
                    return
        raise recorder.CaptureError("No readiness confirmation within 45 seconds.")


def summarize(result):
    measured = [c for c in result["contacts"] if c["status"] == "measured"]
    # Few emitted frames cannot establish quietness. Never combine different finger positions.
    adequate = [c for c in measured if c["sample_count"] >= 10 and c["sample_span_seconds"] >= 6]
    index = REGIONS.index(result["label"])
    located = [c for c in adequate if index * 100 / 3 <= c["observed_mean_y_percent"] <= (index + 1) * 100 / 3]
    result["sample_count"] = sum(c["sample_count"] for c in measured)
    result["sufficiency"] = "sufficient" if located else "wrong_region" if adequate else "insufficient"
    result["spread"] = {}
    for axis in ("x", "y"):
        if measured:
            result["spread"][axis] = {key: max(c[axis][key] for c in measured) for key in measured[0][axis]}
    return result


def capture_region(fd, region, channel, axes, slot_info, clock=time.monotonic):
    recorder.drain(fd)
    fresh = {axis: recorder.abs_info(fd, axis) for axis in axes}
    if any(value is None for value in fresh.values()):
        raise recorder.CaptureError("Touchpad axes disappeared.")
    slot_info = recorder.abs_info(fd, recorder.ABS_MT_SLOT) if slot_info else None
    tracker = recorder.snapshot_tracker(fd, fresh, slot_info)
    selected = (recorder.ABS_MT_POSITION_X, recorder.ABS_MT_POSITION_Y) if slot_info else (recorder.ABS_X, recorder.ABS_Y)
    decoder = recorder.EventDecoder()
    trial = None
    contaminated = False
    deadline = clock() + 15
    last_progress = None
    channel.emit({"type": "progress", "region": region, "phase": "place_finger", "seconds_remaining": 15})
    # Snapshot allows a finger already placed before Ready; no frame replay or fabricated samples.
    initial = tracker.feed(recorder.EV_SYN, recorder.SYN_REPORT, 0)
    while clock() < deadline:
        channel.poll()
        now = clock()
        if initial is not None:
            frames, initial = [initial], None
        elif select.select([fd], [], [], .1)[0]:
            try:
                data = os.read(fd, recorder.EVENT.size * 256)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise recorder.CaptureError(f"Touchpad removed or unreadable: {exc}") from exc
            if not data:
                raise recorder.CaptureError("Touchpad removed.")
            frames = []
            for event in decoder.feed(data):
                if event[:2] == (recorder.EV_SYN, recorder.SYN_DROPPED):
                    raise recorder.CaptureError("SYN_DROPPED: lost events; discard this check and retry.")
                frame = tracker.feed(*event)
                if frame:
                    frames.append(frame)
        else:
            frames = []
        now = clock()
        for frame in frames:
            if frame["status"] == "dropped":
                raise recorder.CaptureError("SYN_DROPPED: lost events; discard this check and retry.")
            if trial is None:
                if frame["status"] == "up":
                    contaminated = False
                elif frame["status"] != "single":
                    contaminated = True
            if trial is None and frame["status"] == "single" and not contaminated:
                trial = recorder.Trial(region, now, 2, [fresh[a] for a in selected])
                deadline = now + 10
            if trial:
                trial.observe(frame, now)
        if trial:
            phase = "settling" if now - trial.start < 2 else "measuring"
            remaining = max(0, int((trial.start + 2 if phase == "settling" else deadline) - now + .999))
            progress = phase, remaining
            if progress != last_progress:
                channel.emit({"type": "progress", "region": region, "phase": phase, "seconds_remaining": remaining})
                last_progress = progress
    if trial is None:
        trial = recorder.Trial(region, clock(), 2, [fresh[a] for a in selected])
    return summarize(trial.summary(max(0, clock() - trial.start)))


def guided(backend, channel):
    # Check the pipe before opening hardware (including a cancellation during authentication).
    channel.poll()
    device = backend.discover()
    fd = os.open(device["node"], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        expected_dev = (backend.root / "sys/class/input" / os.path.basename(device["node"]) / "dev").read_text().strip()
        if not stat.S_ISCHR(info.st_mode) or f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}" != expected_dev:
            raise TrackpadError("Device node does not match the discovered touchpad.")
        axes = {axis: data for axis in recorder.AXES if (data := recorder.abs_info(fd, axis))}
        slots = recorder.abs_info(fd, recorder.ABS_MT_SLOT)
        if slots and (slots["minimum"] != 0 or not 0 <= slots["maximum"] < 64):
            raise recorder.CaptureError("Unsupported touchpad slots.")
        selected = (recorder.ABS_MT_POSITION_X, recorder.ABS_MT_POSITION_Y) if slots else (recorder.ABS_X, recorder.ABS_Y)
        if not all(a in axes and axes[a]["maximum"] > axes[a]["minimum"] for a in selected):
            raise recorder.CaptureError("Required position axes unavailable.")
        result = {"schema_version": 1, "device": device, "axis_metadata": axes,
                  "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "settings": {"settle_seconds_per_contact": 2, "duration_seconds": 8},
                  "limitations": ["Emitted events include active filtering, not raw sensor data.",
                                  "Intentional movement cannot be distinguished from jitter.",
                                  "Sparse events mean insufficient evidence, not zero jitter.",
                                  "Spreads are the largest per-contact spread, never across repositioned contacts.",
                                  "Millimetres rely on device-reported resolution; region placement uses Y thirds."],
                  "trials": []}
        channel.emit({"type": "started", "device_name": device["name"]})
        for region in REGIONS:
            channel.ready(region)
            measured = capture_region(fd, region, channel, axes, slots)
            result["trials"].append(measured)
            channel.emit({"type": "region_result", "result": measured})
        channel.emit({"type": "result", "result": result})
    finally:
        os.close(fd)
