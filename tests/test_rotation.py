import argparse
import importlib.machinery
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


def load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(Path(__file__).resolve().parents[1] / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


engine = load("rotation_engine", "chuwi-autorotate")
restore = load("restore_install", "restore-install.py")


class RotationTests(unittest.TestCase):
    def test_angle_threshold_waits_for_deliberate_turn(self):
        # Landscape starts at -X, portrait at -Y.
        def vector(degrees):
            return (-math.cos(math.radians(degrees)), -math.sin(math.radians(degrees)), 0)
        for degrees in (45, 50, 60, 64.9):
            self.assertFalse(engine.angle_allows_rotation("normal", vector(degrees), 65))
        for degrees in (65, 75, 90):
            self.assertTrue(engine.angle_allows_rotation("normal", vector(degrees), 65))

    def test_higher_threshold_requires_larger_turn(self):
        vector = (-math.cos(math.radians(70)), -math.sin(math.radians(70)), 0)
        self.assertTrue(engine.angle_allows_rotation("normal", vector, 65))
        self.assertFalse(engine.angle_allows_rotation("normal", vector, 75))

    def test_angle_mapping_matches_proxy_in_all_four_postures(self):
        postures = {"right-up":(-1,0,0), "normal":(0,-1,0),
                    "left-up":(1,0,0), "bottom-up":(0,1,0)}
        for orientation, vector in postures.items():
            for candidate in postures:
                self.assertEqual(engine.angle_allows_rotation(candidate, vector, 65), candidate == orientation)

    def test_flat_zero_and_invalid_readings_cannot_trigger(self):
        for vector in ((0,-.1,1),(0,0,0),(float('nan'),-1,0),(0,-1,float('inf'))):
            self.assertFalse(engine.angle_allows_rotation("normal",vector,65))
        self.assertFalse(engine.angle_allows_rotation("undefined",(0,-1,0),65))

    def test_angle_uses_sensor_axes_before_display_offset(self):
        config = {"orientation_offset":"right"}
        self.assertEqual(engine.map_sensor_to_rotation("normal",config),"right")
        self.assertTrue(engine.angle_allows_rotation("normal",(0,-1,0),65))

    def test_sensitivity_needs_continuous_steady_posture(self):
        settler = engine.RotationSettler()
        self.assertFalse(settler.update("normal",True,1.5,0))
        self.assertFalse(settler.update("normal",True,1.5,1.4))
        self.assertFalse(settler.update("normal",False,1.5,1.45))
        self.assertFalse(settler.update("normal",True,1.5,1.5))
        self.assertFalse(settler.update("normal",True,1.5,2.99))
        self.assertTrue(settler.update("normal",True,1.5,3))

    def test_partial_tilt_never_passes_by_waiting_longer(self):
        settler=engine.RotationSettler()
        for now in (0,1,10,100):
            self.assertFalse(settler.update("normal",False,1.5,now))
        self.assertIsNone(settler.deadline)

    def test_candidate_or_delay_change_restarts_settling(self):
        settler=engine.RotationSettler()
        settler.update("normal",True,1,0)
        self.assertFalse(settler.update("left",True,1,.9))
        self.assertFalse(settler.update("left",True,2,1))
        self.assertFalse(settler.update("left",True,2,2.99))
        self.assertTrue(settler.update("left",True,2,3))

    def test_sampler_applies_scale_offset_and_mount_matrix(self):
        with tempfile.TemporaryDirectory() as temporary:
            device=Path(temporary)/"iio:device0"; device.mkdir()
            for axis,value in zip("xyz",(10,20,30)):
                (device/f'in_accel_{axis}_raw').write_text(str(value))
            (device/'in_accel_scale').write_text('2')
            (device/'in_accel_offset').write_text('1')
            (device/'in_accel_mount_matrix').write_text('0,-1,0;1,0,0;0,0,1')
            with patch.object(engine,'command_exists',return_value=False):
                sensor=engine.IIOAngleSensor(Path(temporary))
                self.assertEqual(sensor.read(),(-42,22,62))
                (device/'in_accel_x_raw').unlink()
                with self.assertRaises(engine.AutorotateError): sensor.read()

    def test_sampler_refuses_ambiguous_accelerometers(self):
        with tempfile.TemporaryDirectory() as temporary:
            for index in range(2):
                device=Path(temporary)/f'iio:device{index}';device.mkdir()
                for axis in 'xyz': (device/f'in_accel_{axis}_raw').write_text('1')
            with patch.object(engine,'command_exists',return_value=False):
                with self.assertRaises(engine.AutorotateError): engine.IIOAngleSensor(Path(temporary)).read()

    def test_mount_matrix_rejects_incomplete_or_nonfinite_values(self):
        for text in ('1,0;0,1','nan,0,0;0,1,0;0,0,1','0,0,0;0,1,0;0,0,1'):
            with self.assertRaises(engine.AutorotateError): engine.parse_mount_matrix(text)

    def test_sensitivity_validation_is_atomic(self):
        for threshold,delay in (('nan','1'),('90','1'),('65','-1'),('65','inf')):
            with patch.object(engine,'write_config') as write:
                with self.assertRaises(engine.AutorotateError):
                    engine.command_sensitivity(argparse.Namespace(threshold=threshold,delay=delay))
                write.assert_not_called()

    def test_native_panel_mapping(self):
        config = {"orientation_offset": "normal"}
        for sensor, rotation in (("normal", "normal"), ("right-up", "right"),
                                 ("left-up", "left"), ("bottom-up", "inverted")):
            self.assertEqual(engine.map_sensor_to_rotation(sensor, config), rotation)

    def test_calibration_computes_offset_from_live_posture(self):
        display = engine.DisplayInfo("DSI-1", "right", True, True, True, "")
        for orientation, expected in (("right-up", "normal"), ("normal", "right")):
            with patch.object(engine, "read_config", return_value=engine.default_config_copy()), \
                 patch.object(engine, "detect_display", return_value=display), \
                 patch.object(engine, "read_sensor_once", return_value=orientation), \
                 patch.object(engine, "write_config") as write, patch.object(engine, "update_state"):
                engine.command_calibrate_home(None)
                self.assertEqual(write.call_args.args[0]["orientation_offset"], expected)

    def test_calibration_does_not_write_without_sensor(self):
        with patch.object(engine, "read_config", return_value={}), \
             patch.object(engine, "detect_display"), \
             patch.object(engine, "read_sensor_once", side_effect=engine.AutorotateError("missing")), \
             patch.object(engine, "write_config") as write:
            with self.assertRaises(engine.AutorotateError):
                engine.command_calibrate_home(None)
            write.assert_not_called()

    def test_detects_only_touchscreen_pointer(self):
        devices = """↳ Goodix Capacitive TouchScreen id=6 [slave  pointer (2)]
↳ XXXX0000:05 Touchpad id=7 [slave  pointer (2)]
↳ Goodix Capacitive TouchScreen id=14 [slave  keyboard (3)]
↳ Wacom Pen stylus id=15 [slave  pointer (2)]"""
        with patch.object(engine, "command_exists", return_value=True), \
             patch.object(engine, "run_capture", return_value=subprocess.CompletedProcess([], 0, devices, "")):
            self.assertEqual([d["id"] for d in engine.detect_touchscreens()], ["6"])

    def test_auto_selection_never_rotates_external_only_display(self):
        displays = engine.parse_xrandr_displays("HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right)")
        with self.assertRaises(engine.AutorotateError):
            engine.choose_display(displays)
        with self.assertRaises(engine.AutorotateError):
            engine.choose_display(displays, "HDMI-1")

    def test_explicit_inactive_output_rejected(self):
        displays = engine.parse_xrandr_displays("DSI-1 connected (normal left inverted right)")
        with self.assertRaises(engine.AutorotateError):
            engine.choose_display(displays, "DSI-1")

    def test_parses_normal_and_right_rotation(self):
        self.assertEqual(engine.parse_xrandr_rotation("primary 1920x1200+0+0 right (normal left inverted right)"), ("right", True))
        self.assertEqual(engine.parse_xrandr_rotation("1200x1920+0+0 (normal left inverted right)"), ("normal", True))

    def test_single_panel_touch_corners_all_rotations(self):
        expected = {"normal": ((0,0),(1,1)), "right": ((0,1),(1,0)),
                    "left": ((1,0),(0,1)), "inverted": ((1,1),(0,0))}
        for rotation, corners in expected.items():
            m = list(map(float, engine.viewport_touch_matrix(rotation, "DSI-1 connected 1920x1200+0+0", 1920, 1200)))
            actual = tuple((m[0]*x+m[1]*y+m[2], m[3]*x+m[4]*y+m[5]) for x,y in ((0,0),(1,1)))
            self.assertEqual(actual, corners)

    def test_touch_maps_to_panel_when_external_monitor_present(self):
        m = list(map(float, engine.viewport_touch_matrix("right", "DSI-1 connected 1920x1200+1920+0 right", 3840, 1200)))
        self.assertEqual(m, [0,.5,.5,-1,0,1,0,0,1])

    def test_lock_overrides_sensor_and_blacklist(self):
        config = {"rotation_locked":"true", "locked_rotation":"right", "blacklist_rotations":"right", "orientation_offset":"normal"}
        self.assertEqual(engine.automatic_target(config, "bottom-up"), "right")

    def test_unknown_sensor_keeps_home(self):
        self.assertEqual(engine.automatic_target({"home_rotation":"right"}, None), "right")
        self.assertIsNone(engine.parse_sensor_orientation("orientation changed: undefined"))

    def test_blacklisted_orientation_is_not_applied(self):
        self.assertIsNone(engine.automatic_target({"orientation_offset":"normal", "blacklist_rotations":"left"}, "left-up"))

    def test_manual_failure_restores_lock_settings(self):
        config = engine.default_config_copy()
        with patch.object(engine, "read_config", return_value=config), \
             patch.object(engine, "write_config") as write, \
             patch.object(engine, "apply_rotation", side_effect=engine.AutorotateError("failure")):
            with self.assertRaises(engine.AutorotateError):
                engine.command_rotate(argparse.Namespace(rotation="left"))
            self.assertEqual(write.call_args.args[0]["rotation_locked"], "false")

    def test_config_atomic_round_trip_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(engine, "CONFIG_DIR", base), patch.object(engine, "CONFIG_PATH", base / "config"), patch.object(engine, "STATE_DIR", base / "state"):
                config = engine.default_config_copy()
                config["future_setting"] = "preserved"
                engine.write_config(config)
                self.assertEqual(engine.read_config()["future_setting"], "preserved")
                self.assertEqual((base/"config").stat().st_mode & 0o777, 0o600)

    def test_rotation_delay_rejects_nonfinite_and_out_of_range(self):
        for value in ("nan", "inf", "-1", "10", "invalid"):
            with patch.object(engine, "read_config", return_value={}), patch.object(engine, "write_config") as write:
                with self.assertRaises(engine.AutorotateError):
                    engine.command_configure(argparse.Namespace(key="debounce_seconds",value=value))
                write.assert_not_called()

    def test_restore_handles_existing_and_absent_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            backup, home = base/"backup", base/"home"
            backup.mkdir()
            target = home/".local/bin/chuwi-autorotate"
            target.parent.mkdir(parents=True)
            target.write_text("new")
            (backup/"0").write_text("old")
            extra = home/".local/bin/chuwi-control-panel"
            extra.write_text("new")
            (backup/"manifest.json").write_text(json.dumps({"files":[
                {"path":".local/bin/chuwi-autorotate","existed":True,"backup":"0"},
                {"path":".local/bin/chuwi-control-panel","existed":False,"backup":"1"}],"service":{}}))
            restore.restore(backup,home,systemctl=False)
            self.assertEqual(target.read_text(),"old")
            self.assertFalse(extra.exists())


if __name__ == "__main__":
    unittest.main()
