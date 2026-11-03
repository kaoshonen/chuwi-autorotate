#!/usr/bin/env python3
"""Read-only input protocol tracker and per-contact noise calculations.

Extracted from the original MiniBook X recorder, including its protocol tests.
The guided JSON-lines session is in trackpad_session; discovery is in trackpad.
No input grabs, injected events or write ioctls are used.
"""

import array
import errno
import fcntl
import math
import os
import select
import struct
import sys
import time


EVENT = struct.Struct("@llHHi")  # Native Linux struct input_event (32/64 bit).
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_DROPPED = 0, 3
ABS_X, ABS_Y, ABS_MT_SLOT = 0x00, 0x01, 0x2F
ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x35, 0x36, 0x39
BTN_TOUCH, BTN_TOOL_FINGER = 0x14A, 0x145
TOOL_COUNTS = {BTN_TOOL_FINGER: 1, 0x14D: 2, 0x14E: 3, 0x14F: 4, 0x148: 5}
AXES = {ABS_X: "ABS_X", ABS_Y: "ABS_Y", ABS_MT_POSITION_X: "ABS_MT_POSITION_X",
        ABS_MT_POSITION_Y: "ABS_MT_POSITION_Y"}


class CaptureError(Exception):
    pass


def ioctl_read(fd, code, size):
    """Linux EVIOC* read request; there are deliberately no write ioctls here."""
    buf = bytearray(size)
    fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord("E") << 8) | code, buf, True)
    return buf


def abs_info(fd, axis):
    try:
        values = struct.unpack("@6i", ioctl_read(fd, 0x40 + axis, 24))
    except OSError as exc:
        if exc.errno in (errno.EINVAL, errno.ENOTTY):
            return None
        raise
    return dict(zip(("value", "minimum", "maximum", "fuzz", "flat", "resolution"), values))


def mt_values(fd, axis, count):
    values = array.array("i", [axis] + [0] * count)
    request = (2 << 30) | (len(values) * values.itemsize << 16) | (ord("E") << 8) | 0x0A
    fcntl.ioctl(fd, request, values, True)
    return list(values)[1:]


class EventDecoder:
    """Retain incomplete reads: input_event boundaries need not match read()."""
    def __init__(self):
        self.pending = b""

    def feed(self, data):
        self.pending += data
        end = len(self.pending) - len(self.pending) % EVENT.size
        events = [EVENT.unpack_from(self.pending, i)[2:] for i in range(0, end, EVENT.size)]
        self.pending = self.pending[end:]
        return events


class Tracker:
    """Protocol-B state; only SYN_REPORT returns a complete-frame observation."""
    def __init__(self, slots=None, keys=None, xy=None, current_slot=0):
        self.slots = slots
        self.current_slot = current_slot
        self.keys = keys or {}
        self.xy = list(xy or (None, None))
        self.serial = 0
        self.was_touching = False
        self.dropped = False

    def feed(self, kind, code, value):
        if kind == EV_SYN and code == SYN_DROPPED:
            self.dropped = True
            return None
        if self.dropped:
            if kind == EV_SYN and code == SYN_REPORT:
                self.dropped = False
                # Lost state is never trusted. Require a newly observed tracking
                # ID (MT) or touch-down (fallback) before accepting later samples.
                if self.slots is not None:
                    self.slots = [{"id": -1, "x": None, "y": None} for _ in self.slots]
                self.keys, self.xy, self.was_touching = {}, [None, None], False
                return {"status": "dropped"}
            return None
        if kind == EV_ABS:
            if self.slots is not None:
                if code == ABS_MT_SLOT:
                    self.current_slot = value
                elif 0 <= self.current_slot < len(self.slots):
                    slot = self.slots[self.current_slot]
                    if code == ABS_MT_TRACKING_ID:
                        # Reused slots must not inherit another contact's position.
                        if value != slot["id"]:
                            slot.update(id=value, x=None, y=None)
                    elif code == ABS_MT_POSITION_X:
                        slot["x"] = value
                    elif code == ABS_MT_POSITION_Y:
                        slot["y"] = value
            elif code in (ABS_X, ABS_Y):
                self.xy[code] = value
        elif kind == EV_KEY:
            self.keys[code] = bool(value)
        if kind != EV_SYN or code != SYN_REPORT:
            return None
        if self.slots is not None:
            if not 0 <= self.current_slot < len(self.slots):
                return {"status": "invalid_slot"}
            active = [(i, s) for i, s in enumerate(self.slots) if s["id"] >= 0]
            if len(active) == 1:
                index, slot = active[0]
                return {"status": "single", "contact": (index, slot["id"]),
                        "x": slot["x"], "y": slot["y"]}
            return {"status": "up" if not active else "multi"}
        touching = self.keys.get(BTN_TOUCH, False)
        if touching and not self.was_touching:
            self.serial += 1
        self.was_touching = touching
        counts = [count for key, count in TOOL_COUNTS.items() if self.keys.get(key)]
        if not touching:
            return {"status": "up"}
        if counts != [1]:
            return {"status": "multi" if any(c > 1 for c in counts) else "unconfirmed_single"}
        return {"status": "single", "contact": (0, self.serial), "x": self.xy[0], "y": self.xy[1]}


def percentile(values, fraction):
    ordered = sorted(values)
    location = (len(ordered) - 1) * fraction
    lower = math.floor(location)
    upper = math.ceil(location)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (location - lower)


def noise(values, axis):
    result = {"peak_to_peak_units": max(values) - min(values),
              "p5_p95_units": percentile(values, .95) - percentile(values, .05)}
    if axis["resolution"] > 0:
        result.update(peak_to_peak_mm=result["peak_to_peak_units"] / axis["resolution"],
                      p5_p95_mm=result["p5_p95_units"] / axis["resolution"])
    return result


class Trial:
    def __init__(self, label, start, settle, axes):
        self.label, self.start, self.settle, self.axes = label, start, settle, axes
        self.segment = None
        self.segments = []
        self.quarantined = False
        self.rejections = {}

    def finish_segment(self, reason):
        if self.segment is not None:
            self.segment["end_reason"] = reason
            self.segments.append(self.segment)
            self.segment = None

    def reject(self, reason):
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        if self.segment is not None:
            self.segment["rejected_reason"] = reason
        self.finish_segment(reason)
        self.quarantined = True

    def observe(self, frame, now):
        status = frame["status"]
        if status == "up":
            self.finish_segment("lift")
            self.quarantined = False
            return
        if status != "single":
            self.reject(status)
            return
        if self.quarantined:
            return
        if self.segment and self.segment["contact"] != frame["contact"]:
            self.finish_segment("contact_change")
        if self.segment is None:
            self.segment = {"contact": frame["contact"], "started_seconds": now - self.start,
                            "samples": [], "settle_excluded_frames": 0}
        x, y = frame["x"], frame["y"]
        if x is None or y is None:
            return
        if not all(axis["minimum"] <= val <= axis["maximum"]
                   for val, axis in zip((x, y), self.axes)):
            self.reject("out_of_range")
            return
        cutoff = self.start + self.segment["started_seconds"] + self.settle
        if now < cutoff:
            self.segment["settle_excluded_frames"] += 1
            return
        self.segment["samples"].append((now - self.start, x, y))

    def summary(self, elapsed):
        self.finish_segment("trial_end")
        contacts = []
        for seg in self.segments:
            samples = seg.pop("samples")
            entry = {**seg, "sample_count": len(samples)}
            entry["status"] = "rejected" if "rejected_reason" in seg else (
                "measured" if len(samples) >= 2 else "insufficient_samples")
            if samples:
                entry["sample_span_seconds"] = samples[-1][0] - samples[0][0]
            if entry["status"] == "measured":
                xs, ys = [s[1] for s in samples], [s[2] for s in samples]
                yaxis = self.axes[1]
                entry.update(x=noise(xs, self.axes[0]), y=noise(ys, yaxis),
                             observed_mean_y_percent=100 * (sum(ys) / len(ys) - yaxis["minimum"]) /
                             (yaxis["maximum"] - yaxis["minimum"]))
            contacts.append(entry)
        return {"label": self.label, "elapsed_seconds": elapsed,
                "rejected_frame_counts": self.rejections, "contacts": contacts}


def snapshot_tracker(fd, axis_data, slot_info):
    if slot_info is not None:
        count = slot_info["maximum"] + 1
        ids = mt_values(fd, ABS_MT_TRACKING_ID, count)
        xs, ys = mt_values(fd, ABS_MT_POSITION_X, count), mt_values(fd, ABS_MT_POSITION_Y, count)
        return Tracker([{"id": ident, "x": x, "y": y} for ident, x, y in zip(ids, xs, ys)],
                       current_slot=slot_info["value"])
    state = ioctl_read(fd, 0x18, 96)  # EVIOCGKEY: current key state, not keystroke logging.
    keys = {key: bool(state[key // 8] & (1 << (key % 8))) for key in (*TOOL_COUNTS, BTN_TOUCH)}
    return Tracker(keys=keys, xy=(axis_data[ABS_X]["value"], axis_data[ABS_Y]["value"]))


def drain(fd):
    # Bound flushing even if the device continuously produces input.
    for _ in range(32):
        try:
            if not os.read(fd, EVENT.size * 256):
                raise CaptureError("Touchpad removed (empty read).")
        except BlockingIOError:
            return
    raise CaptureError("Input backlog did not clear; lift all fingers and retry.")


def prompt(label, timeout, non_interactive):
    print(f"\n{label.upper()}: lift all fingers. Press Enter, then place ONE finger in the "
          f"{label} region and keep it still. Lift it at the end of the trial.", file=sys.stderr)
    if not non_interactive:
        if not select.select([sys.stdin], [], [], timeout)[0]:
            raise CaptureError(f"No Enter received within {timeout:g} seconds.")
        if not sys.stdin.readline():
            raise CaptureError("No interactive input; run in a terminal or use --non-interactive.")


def capture(fd, label, args, axis_data, slot_info):
    drain(fd)
    # Refresh ABS values and the current slot after the user-readiness pause.
    fresh = {axis: abs_info(fd, axis) for axis in axis_data}
    slot_info = abs_info(fd, ABS_MT_SLOT) if slot_info is not None else None
    tracker = snapshot_tracker(fd, fresh, slot_info)
    axes = [fresh[axis] for axis in ((ABS_MT_POSITION_X, ABS_MT_POSITION_Y)
                                    if slot_info is not None else (ABS_X, ABS_Y))]
    decoder = EventDecoder()
    start = time.monotonic()
    trial = Trial(label, start, args.settle, axes)
    deadline = start + args.duration + args.settle
    print(f"Place finger now; {args.settle:g}s settling + {args.duration:g}s measurement window.",
          file=sys.stderr)
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if not select.select([fd], [], [], min(.25, max(0, remaining)))[0]:
            continue
        try:
            data = os.read(fd, EVENT.size * 256)
        except BlockingIOError:
            continue
        except OSError as exc:
            raise CaptureError(f"Touchpad read failed/removed: {exc}") from exc
        if not data:
            raise CaptureError("Touchpad removed (empty read).")
        for event in decoder.feed(data):
            now = time.monotonic()
            if now >= deadline:
                break
            frame = tracker.feed(*event)
            if frame:
                if frame["status"] == "dropped":
                    raise CaptureError("SYN_DROPPED: input was lost; discard this capture and retry with all fingers lifted.")
                trial.observe(frame, now)
    print(f"{label.upper()} done. Lift all fingers.", file=sys.stderr)
    return trial.summary(time.monotonic() - start)


def self_test():
    import unittest
    from unittest.mock import patch
    from types import SimpleNamespace

    class OfflineTests(unittest.TestCase):
        def test_partial_reads(self):
            packed = EVENT.pack(0, 0, EV_ABS, ABS_MT_POSITION_X, 12)
            decoder = EventDecoder()
            self.assertEqual(decoder.feed(packed[:7]), [])
            self.assertEqual(decoder.feed(packed[7:] + packed), [(EV_ABS, ABS_MT_POSITION_X, 12)] * 2)

        def test_slots_lifts_and_reuse(self):
            tr = Tracker([{"id": -1, "x": None, "y": None} for _ in range(2)])
            self.assertIsNone(tr.feed(EV_ABS, ABS_MT_TRACKING_ID, 10))
            tr.feed(EV_ABS, ABS_MT_POSITION_X, 3)
            tr.feed(EV_ABS, ABS_MT_POSITION_Y, 4)
            frame = tr.feed(EV_SYN, SYN_REPORT, 0)
            self.assertEqual((frame["contact"], frame["x"], frame["y"]), ((0, 10), 3, 4))
            tr.feed(EV_ABS, ABS_MT_TRACKING_ID, -1)
            self.assertEqual(tr.feed(EV_SYN, SYN_REPORT, 0)["status"], "up")
            tr.feed(EV_ABS, ABS_MT_TRACKING_ID, 11)
            self.assertIsNone(tr.feed(EV_SYN, SYN_REPORT, 0)["x"])

        def test_settle_and_contact_segmentation(self):
            axis = {"minimum": 0, "maximum": 1000, "resolution": 10}
            trial = Trial("top", 0, 2, [axis, axis])
            for when, ident, x in [(0, 1, 100), (1, 1, 500), (2, 1, 102), (3, 1, 104)]:
                trial.observe({"status": "single", "contact": (0, ident), "x": x, "y": 100}, when)
            trial.observe({"status": "up"}, 4)
            for when, x in [(4, 700), (6, 701), (7, 703)]:
                trial.observe({"status": "single", "contact": (0, 2), "x": x, "y": 900}, when)
            result = trial.summary(8)
            self.assertEqual(len(result["contacts"]), 2)
            for contact in result["contacts"]:
                self.assertEqual(contact["x"]["peak_to_peak_units"], 2)
                self.assertAlmostEqual(contact["x"]["p5_p95_units"], 1.8)
                self.assertAlmostEqual(contact["x"]["peak_to_peak_mm"], .2)
            self.assertEqual(result["contacts"][1]["observed_mean_y_percent"], 90)

        def test_multitouch_rejects_entire_contact(self):
            axis = {"minimum": 0, "maximum": 100, "resolution": 0}
            trial = Trial("top", 0, 0, [axis, axis])
            frame = {"status": "single", "contact": (0, 1), "x": 1, "y": 2}
            trial.observe(frame, 0)
            trial.observe(frame, 1)
            trial.observe({"status": "multi"}, 2)
            trial.observe(frame, 3)  # Remaining finger cannot restart the segment.
            trial.observe({"status": "up"}, 4)
            trial.observe({**frame, "contact": (0, 2)}, 5)
            trial.observe({**frame, "contact": (0, 2)}, 6)
            result = trial.summary(7)
            self.assertEqual([c["status"] for c in result["contacts"]], ["rejected", "measured"])
            self.assertNotIn("x", result["contacts"][0])
            self.assertNotIn("peak_to_peak_mm", result["contacts"][1]["x"])

        def test_syn_dropped_does_not_reuse_stale_contact(self):
            tr = Tracker([{"id": 4, "x": 10, "y": 20}])
            tr.feed(EV_SYN, SYN_DROPPED, 0)
            tr.feed(EV_ABS, ABS_MT_POSITION_X, 99)
            self.assertEqual(tr.feed(EV_SYN, SYN_REPORT, 0)["status"], "dropped")
            tr.feed(EV_ABS, ABS_MT_POSITION_X, 30)
            self.assertEqual(tr.feed(EV_SYN, SYN_REPORT, 0)["status"], "up")
            tr.feed(EV_ABS, ABS_MT_TRACKING_ID, 5)
            tr.feed(EV_ABS, ABS_MT_POSITION_X, 31)
            tr.feed(EV_ABS, ABS_MT_POSITION_Y, 21)
            self.assertEqual(tr.feed(EV_SYN, SYN_REPORT, 0)["contact"], (0, 5))

        def test_absolute_fallback_requires_one_finger(self):
            tr = Tracker()
            for key in (BTN_TOUCH, BTN_TOOL_FINGER):
                tr.feed(EV_KEY, key, 1)
            tr.feed(EV_ABS, ABS_X, 20)
            tr.feed(EV_ABS, ABS_Y, 30)
            self.assertEqual(tr.feed(EV_SYN, SYN_REPORT, 0)["status"], "single")
            tr.feed(EV_KEY, 0x14D, 1)
            self.assertEqual(tr.feed(EV_SYN, SYN_REPORT, 0)["status"], "multi")

        def test_capture_aborts_on_syn_dropped(self):
            axis = {"value": 0, "minimum": 0, "maximum": 100, "resolution": 10}
            dropped = EVENT.pack(0, 0, EV_SYN, SYN_DROPPED, 0) + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0)
            with patch(__name__ + ".drain"), patch(__name__ + ".abs_info", return_value=axis), \
                 patch(__name__ + ".snapshot_tracker", return_value=Tracker()), \
                 patch("select.select", return_value=([99], [], [])), patch("os.read", return_value=dropped):
                with self.assertRaisesRegex(CaptureError, "SYN_DROPPED"):
                    capture(99, "test", SimpleNamespace(settle=2, duration=8), {ABS_X: axis, ABS_Y: axis}, None)

    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(OfflineTests))
    return 0 if result.wasSuccessful() else 1
