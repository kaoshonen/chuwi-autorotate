import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chuwi import trackpad as tp
from chuwi import trackpad_recorder as recorder
from chuwi import trackpad_session as session
from chuwi.trackpad_client import Client


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.fail_update = 0
        self.conflict = False
        self.put("sys/class/dmi/id/product_name", "MiniBook X\n")
        self.put("sys/class/dmi/id/modalias", "dmi:bvnAMI:pnMiniBookX:pvr1:")
        self.put("proc/sys/kernel/random/boot_id", "boot-one")
        (self.root / "etc/udev/hwdb.d").mkdir(parents=True)
        self.add_device("event4", False)
        self.add_device("event5", True)
        self.backend = tp.Backend(self.root, self.run_command)

    def put(self, relative, text):
        path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)

    def add_device(self, event, touchpad, name="XXXX0000:05 0911:5288"):
        base = f"sys/class/input/{event}/device"
        for name_part, value in {"name": name, "phys": "i2c-XXXX0000:05", "capabilities/ev": "1b" if touchpad else "17",
                                 "id/bustype": "0018", "id/vendor": "0911", "id/product": "5288"}.items():
            self.put(base + "/" + name_part, value)

    def run_command(self, args, **kwargs):
        self.calls.append(args)
        output = ""
        if args[0].endswith("systemd-hwdb") and args[1] == "update" and self.fail_update:
            self.fail_update -= 1
            return subprocess.CompletedProcess(args, 1, "", "fixture rebuild failure")
        if args[1] == "info":
            event = Path(args[-1])
            touch = (event / "device/capabilities/ev").read_text() == "1b"
            output = "ID_INPUT_TOUCHPAD=1\n" if touch else "ID_INPUT_MOUSE=1\n"
            value = tp.parse_config(tp.read_file(self.backend.target))
            if self.conflict and value == 12:
                value = 4
            if value:
                output += "".join(f"EVDEV_ABS_{a}=:::{value}\nLIBINPUT_FUZZ_{a}={value}\n" for a in tp.AXES)
        return subprocess.CompletedProcess(args, 0, output, "")

    def test_both_names_and_duplicate_mouse_exclusion(self):
        for name in ("XXXX0000:05 0911:5288", "XXXX0000:05 0911:5288 Touchpad"):
            self.add_device("event5", True, name)
            found = self.backend.discover()
            self.assertTrue(found["node"].endswith("event5")); self.assertEqual(found["name"], name)

    def test_missing_and_ambiguous_disable_apply(self):
        self.add_device("event5", False)
        self.assertFalse(self.backend.status()["can_apply"])
        self.add_device("event5", True); self.add_device("event6", True)
        status = self.backend.status()
        self.assertFalse(status["can_apply"]); self.assertIn("found 2", status["errors"][0])

    def test_wrong_bus_phys_name_or_machine_refused(self):
        for relative, value in (("sys/class/input/event5/device/id/bustype", "0003"),
                                ("sys/class/input/event5/device/phys", "usb-1"),
                                ("sys/class/input/event5/device/name", "External pad"),
                                ("sys/class/dmi/id/product_name", "Other computer")):
            path = self.root / relative; before = path.read_text(); path.write_text(value)
            with self.assertRaises(tp.TrackpadError): self.backend.apply(8)
            path.write_text(before)
        self.assertFalse(self.backend.target.exists())

    def test_strength_validation(self):
        for value in (1, 8, "64"):
            self.assertEqual(tp.strength(value), int(value))
        for value in (0, 65, -1, "8.0", 1.5, "8;id", "08", True, None, " 8"):
            with self.assertRaises(tp.TrackpadError): self.backend.apply(value)
        self.assertFalse(self.backend.target.exists())

    def test_default_and_existing_file_adoption_preserves_older_overrides(self):
        self.assertEqual(self.backend.status()["selected_default"], 8)
        self.backend.target.write_text(tp.render(8, tp.OLD_HEADER))
        siblings = [self.backend.target.with_name(name) for name in ("61-chuwi-minibook-x-touchpad.hwdb", "62-chuwi-minibook-x-multitouch.hwdb")]
        for path in siblings: path.write_text("previous override\n")
        result = self.backend.apply(12)
        self.assertEqual(self.backend.target.read_text(), tp.render(12))
        self.assertTrue(result["status"]["properties_match"])
        for path in siblings: self.assertEqual(path.read_text(), "previous override\n")
        backup = json.loads((self.backend.state_dir / (result["backup"] + ".json")).read_text())
        self.assertEqual(backup["configuration"], tp.render(8, tp.OLD_HEADER))
        self.assertEqual((self.backend.state_dir / (result["backup"] + ".json")).stat().st_mode & 0o777, 0o600)

    def test_foreign_or_mixed_file_refused(self):
        for content in ("custom configuration", tp.render(8).replace("36=:::8", "36=:::9"), tp.render(8) + "# custom\n"):
            self.backend.target.write_text(content)
            self.assertFalse(self.backend.status()["can_apply"])
            with self.assertRaises(tp.TrackpadError): self.backend.apply(12)
            self.assertEqual(self.backend.target.read_text(), content)

    def test_symlink_refused(self):
        other = self.root / "other"; other.write_text(tp.render(8))
        self.backend.target.symlink_to(other)
        with self.assertRaises(tp.TrackpadError): self.backend.apply(12)
        self.assertEqual(other.read_text(), tp.render(8))

    def test_reboot_tracking_requires_matching_digest(self):
        self.backend.target.write_text(tp.render(8, tp.OLD_HEADER))
        self.assertEqual(self.backend.status()["reboot"], "unknown")
        self.backend.apply(12)
        self.assertEqual(self.backend.status()["reboot"], "needed")
        self.put("proc/sys/kernel/random/boot_id", "boot-two")
        self.assertEqual(self.backend.status()["reboot"], "not_needed")
        self.backend.target.write_text(tp.render(14))
        self.assertEqual(self.backend.status()["reboot"], "unknown")

    def test_rebuild_failure_rolls_back_file_and_record(self):
        self.backend.apply(8)
        previous_record = self.backend.state_file.read_bytes()
        self.fail_update = 1
        with self.assertRaisesRegex(tp.TrackpadError, "previous configuration restored"):
            self.backend.apply(12)
        self.assertEqual(self.backend.target.read_text(), tp.render(8))
        self.assertEqual(self.backend.state_file.read_bytes(), previous_record)

    def test_conflicting_properties_roll_back(self):
        self.backend.apply(8); self.conflict = True
        with self.assertRaisesRegex(tp.TrackpadError, "conflicting override"):
            self.backend.apply(12)
        self.assertEqual(self.backend.target.read_text(), tp.render(8))

    def test_failed_atomic_write_leaves_prior_settings(self):
        self.backend.apply(8)
        real_write = tp.atomic_write
        failed = False
        def write(path, content, *args):
            nonlocal failed
            if path == self.backend.target and not failed:
                failed = True; raise OSError("fixture disk full")
            return real_write(path, content, *args)
        with patch.object(tp, "atomic_write", side_effect=write):
            with self.assertRaisesRegex(tp.TrackpadError, "previous configuration restored"):
                self.backend.apply(12)
        self.assertEqual(self.backend.target.read_text(), tp.render(8))

    def test_failed_rollback_reports_backup(self):
        self.backend.apply(8); self.fail_update = 2
        with self.assertRaisesRegex(tp.TrackpadError, "Rollback needs attention.*Backup:"):
            self.backend.apply(12)
        self.assertEqual(self.backend.target.read_text(), tp.render(8))

    def test_restore_existing_and_absent_prior_file(self):
        first = self.backend.apply(8)
        second = self.backend.apply(12)
        self.backend.restore(second["backup"])
        self.assertEqual(self.backend.target.read_text(), tp.render(8))
        self.backend.restore(first["backup"])
        self.assertFalse(self.backend.target.exists())
        self.assertEqual(self.backend.status()["reboot"], "needed")

    def test_restore_rejects_traversal_and_modified_backup(self):
        for token in ("../secret", "", "x" * 32):
            with self.assertRaises(tp.TrackpadError): self.backend.restore(token)
        saved = self.backend.apply(8)
        path = self.backend.state_dir / (saved["backup"] + ".json")
        path.write_text(json.dumps({"schema_version": 1, "configuration": "foreign"}))
        with self.assertRaises(tp.TrackpadError): self.backend.restore(saved["backup"])

    def test_reload_targets_only_touchpad(self):
        self.backend.apply(8)
        triggers = [call for call in self.calls if "trigger" in call]
        self.assertEqual(len(triggers), 1)
        self.assertTrue(triggers[0][-1].endswith("event5"))


class ProtocolTests(unittest.TestCase):
    def channel(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader); self.addCleanup(os.close, writer)
        output = io.StringIO()
        return session.Channel(reader, output), writer, output

    def test_original_recorder_protocol_suite(self):
        self.assertEqual(recorder.self_test(), 0)

    def test_ready_progress_and_cancel_protocol(self):
        channel, writer, output = self.channel()
        os.write(writer, b'{"command":"ready","region":"top"}\n')
        channel.ready("top")
        self.assertEqual(json.loads(output.getvalue())["type"], "ready")
        os.write(writer, b'{"command":"cancel"}\n')
        with self.assertRaises(session.Cancelled): channel.poll()

    def test_partial_protocol_and_multiple_messages(self):
        channel, writer, _ = self.channel()
        os.write(writer, b'{"command":')
        self.assertEqual(channel.poll(), [])
        os.write(writer, b'"ready","region":"top"}\n{"command":"cancel"}\n')
        with self.assertRaises(session.Cancelled): channel.poll()

    def test_communication_loss(self):
        reader, writer = os.pipe(); os.close(writer)
        self.addCleanup(os.close, reader)
        with self.assertRaises(session.Cancelled): session.Channel(reader, io.StringIO()).poll()

    def test_bounded_readiness_and_session_timeout(self):
        channel, _, _ = self.channel()
        channel.clock = Mock(side_effect=[0, 0, 46])
        with self.assertRaisesRegex(recorder.CaptureError, "readiness"):
            with patch.object(channel, "poll", return_value=[]): channel.ready("top")
        channel.clock = lambda: channel.deadline + 1
        with self.assertRaisesRegex(recorder.CaptureError, "timed out"): channel.poll()

    def test_insufficient_and_region_validation(self):
        axis = {"minimum": 0, "maximum": 100, "resolution": 10}
        for sample_count, y, expected in ((2, 10, "insufficient"), (12, 10, "sufficient"), (12, 90, "wrong_region")):
            trial = recorder.Trial("top", 0, 0, [axis, axis])
            for i in range(sample_count):
                trial.observe({"status": "single", "contact": (0, 1), "x": 10 + i, "y": y}, i)
            self.assertEqual(session.summarize(trial.summary(12))["sufficiency"], expected)

    def test_capture_progress_then_device_removal_or_lost_events(self):
        axis = {"value": 10, "minimum": 0, "maximum": 100, "resolution": 10}
        frame = recorder.EVENT.pack(0, 0, recorder.EV_SYN, recorder.SYN_DROPPED, 0) + recorder.EVENT.pack(0, 0, 0, 0, 0)
        for data, message in ((b"", "removed"), (frame, "SYN_DROPPED")):
            channel = Mock()
            tracker = recorder.Tracker(keys={recorder.BTN_TOUCH: True, recorder.BTN_TOOL_FINGER: True}, xy=[10, 10])
            with patch.object(recorder, "drain"), patch.object(recorder, "abs_info", return_value=axis), \
                 patch.object(recorder, "snapshot_tracker", return_value=tracker), \
                 patch.object(session.select, "select", return_value=([99], [], [])), patch.object(session.os, "read", return_value=data):
                with self.assertRaisesRegex(recorder.CaptureError, message):
                    session.capture_region(99, "top", channel, {0: axis, 1: axis}, None)
            events = [call.args[0] for call in channel.emit.call_args_list]
            self.assertEqual(events[0]["phase"], "place_finger")
            self.assertTrue(any(event["phase"] == "settling" for event in events))

    def test_guided_always_closes_device(self):
        backend = Mock(); backend.root = Path("/fixture")
        backend.discover.return_value = {"node": "/dev/input/event5"}
        with patch.object(session.os, "open", return_value=88), patch.object(session.os, "close") as close, \
             patch.object(session.os, "fstat", side_effect=OSError("removed")):
            with self.assertRaises(OSError): session.guided(backend, Mock())
        close.assert_called_once_with(88)

    def test_guided_all_regions_and_cancellation_close_device(self):
        axis = {"value": 0, "minimum": 0, "maximum": 100, "resolution": 10}
        backend = Mock(); backend.root = Path("/fixture")
        backend.discover.return_value = {"node": "/dev/input/event5", "name": "Fixture"}
        for cancel in (False, True):
            channel = Mock()
            if cancel:
                channel.ready.side_effect = session.Cancelled("cancelled")
            with patch.object(session.os, "open", return_value=88), patch.object(session.os, "close") as close, \
                 patch.object(session.os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(13, 69))), \
                 patch.object(Path, "read_text", return_value="13:69"), \
                 patch.object(recorder, "abs_info", side_effect=lambda _fd, code: None if code == recorder.ABS_MT_SLOT else axis), \
                 patch.object(session, "capture_region", side_effect=lambda _fd, region, *_: {"label": region}) as capture:
                if cancel:
                    with self.assertRaises(session.Cancelled): session.guided(backend, channel)
                    capture.assert_not_called()
                else:
                    session.guided(backend, channel)
                    self.assertEqual([call.args[0] for call in channel.ready.call_args_list], ["top", "middle", "bottom"])
                    self.assertEqual([call.args[0]["type"] for call in channel.emit.call_args_list],
                                     ["started", "region_result", "region_result", "region_result", "result"])
            close.assert_called_once_with(88)

    def test_capture_cancel_during_active_contact(self):
        axis = {"value": 0, "minimum": 0, "maximum": 100, "resolution": 10}
        channel = Mock(); channel.poll.side_effect = session.Cancelled("cancelled")
        with patch.object(recorder, "drain"), patch.object(recorder, "abs_info", return_value=axis), \
             patch.object(recorder, "snapshot_tracker", return_value=recorder.Tracker()):
            with self.assertRaises(session.Cancelled):
                session.capture_region(88, "top", channel, {0: axis, 1: axis}, None)


class WorkerTests(unittest.TestCase):
    def test_authentication_cancellation_is_reported_without_retries(self):
        import threading
        ready = threading.Event(); events = []
        def receive(event): events.append(event); ready.set()
        with patch("chuwi.trackpad_client.subprocess.run", return_value=subprocess.CompletedProcess([], 126, "", "cancelled")) as run:
            client = Client(lambda callback: callback(), helper="/fixture/chuwi-trackpad")
            try:
                client.submit("apply", receive, 12)
                self.assertTrue(ready.wait(2))
                self.assertEqual(events[0]["type"], "cancelled")
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][:3], ["/usr/bin/pkexec", "/usr/bin/python3", "-I"])
            finally: client.close()


if __name__ == "__main__": unittest.main()
