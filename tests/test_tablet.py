from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chuwi_tablet import (InputDevice, RecoveryStore, TabletError, TabletPolicy,
                         XInputBackend, classify_device, floating_position)
from chuwi_tablet import write_session_environment


KEYBOARD = InputDevice("13", "Built-in keyboard", "keyboard", "/dev/input/event3",
                       "/sys/devices/platform/i8042/serio0/input/input3", "platform-i8042-serio-0", True)
TOUCHPAD = InputDevice("7", "Built-in touchpad", "touchpad", "/dev/input/event4",
                      "/sys/devices/pci0000/i2c-3/input/input7", "pci-i2c-touchpad", True)


class FakeBackend:
    def __init__(self, touch=True, targets=None):
        self.targets = list(targets or [KEYBOARD, TOUCHPAD])
        self.states = {d.role: d.enabled for d in self.targets}
        self.touch = touch
        self.changes = []
        self.fail_disable = None
        self.fail_restore = False

    def preflight(self):
        self.verify_touch()
        return self.targets

    def change(self, device, enabled):
        self.changes.append((device.role, enabled))
        if (not enabled and device.role == self.fail_disable) or (enabled and self.fail_restore):
            raise TabletError("Simulated input failure")
        self.states[device.role] = enabled

    def verify_touch(self):
        if not self.touch:
            raise TabletError("Touchscreen unavailable")


class TabletPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = RecoveryStore(Path(self.temp.name))
        self.backend = FakeBackend()
        self.now = 0
        self.policy = TabletPolicy(self.backend, self.store, lambda: self.now)

    def advance(self, seconds):
        self.now += seconds
        self.policy.tick()

    def begin(self):
        return self.policy.begin(":panel", True)

    def test_watchdog_and_unlocked_screen_are_required(self):
        for armed, locked in ((False, False), (True, True)):
            with self.assertRaises(TabletError):
                self.policy.begin(":panel", armed, locked)
        self.assertEqual(self.backend.changes, [])
        self.assertFalse(self.store.path.exists())

    def test_disabled_touchscreen_prevents_all_input_changes(self):
        self.backend.touch = False
        with self.assertRaises(TabletError): self.begin()
        self.assertEqual(self.backend.changes, [])

    def test_first_activation_has_trial_and_private_recovery_record(self):
        self.assertEqual(self.begin()["phase"], "trial")
        self.assertEqual(self.policy.status()["remaining"], 10)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backend.states, {"keyboard":False,"touchpad":False})

    def test_trial_expires_even_with_continuous_heartbeats(self):
        self.begin()
        for _ in range(9):
            self.now += 1
            self.policy.renew(":panel")
        self.advance(1)
        self.assertEqual(self.policy.phase, "off")
        self.assertTrue(all(self.backend.states.values()))
        self.assertFalse(self.store.path.exists())

    def test_freeze_recovers_after_five_seconds(self):
        self.begin()
        self.advance(4.99)
        self.assertEqual(self.policy.phase, "trial")
        self.advance(.01)
        self.assertEqual(self.policy.phase, "off")
        self.assertTrue(all(self.backend.states.values()))

    def test_wrong_connection_cannot_renew_or_confirm(self):
        self.begin()
        for function in (self.policy.renew, self.policy.confirm):
            with self.assertRaises(TabletError): function(":another-panel")
        self.assertEqual(self.policy.phase, "trial")

    def test_confirmation_is_rejected_after_trial_deadline(self):
        self.begin()
        self.now = 10
        with self.assertRaises(TabletError): self.policy.confirm(":panel")
        self.assertEqual(self.policy.phase, "off")

    def test_confirmed_lock_still_requires_heartbeats(self):
        self.begin()
        self.policy.confirm(":panel")
        for _ in range(15):
            self.now += 1
            self.policy.renew(":panel")
        self.assertEqual(self.policy.phase, "locked")
        self.advance(5)
        self.assertEqual(self.policy.phase, "off")

    def test_confirmed_owner_can_relock_but_new_connection_must_test(self):
        self.begin()
        self.policy.confirm(":panel")
        self.policy.unlock()
        self.assertEqual(self.begin()["phase"], "locked")
        self.policy.owner_lost(":panel")
        self.assertEqual(self.policy.begin(":new-panel",True)["phase"],"trial")

    def test_closing_owner_restores_both_inputs(self):
        self.begin()
        self.policy.owner_lost(":other")
        self.assertEqual(self.policy.phase,"trial")
        self.policy.owner_lost(":panel")
        self.assertEqual(self.policy.phase,"off")
        self.assertTrue(all(self.backend.states.values()))

    def test_screen_lock_suspend_and_shutdown_restore(self):
        for reason in ("Screen locked", "Suspending", "Guardian stopped"):
            self.begin()
            self.policy.unlock(reason)
            self.assertEqual(self.policy.phase,"off")
            self.assertTrue(all(self.backend.states.values()))

    def test_partial_disable_failure_restores_earlier_device(self):
        self.backend.fail_disable = "touchpad"
        with self.assertRaises(TabletError): self.begin()
        self.assertTrue(all(self.backend.states.values()))
        self.assertFalse(self.store.path.exists())

    def test_pending_restore_is_retained_and_retried(self):
        self.begin()
        self.backend.fail_restore = True
        with self.assertRaises(TabletError): self.policy.unlock()
        self.assertEqual(self.policy.phase,"error")
        self.assertTrue(self.store.path.exists())
        with self.assertRaises(TabletError): self.begin()
        self.backend.fail_restore = False
        self.policy.tick()
        self.assertEqual(self.policy.phase,"off")
        self.assertFalse(self.store.path.exists())

    def test_previously_disabled_device_is_not_enabled_by_restore(self):
        self.backend = FakeBackend(targets=[KEYBOARD, replace(TOUCHPAD,enabled=False)])
        self.policy = TabletPolicy(self.backend,self.store,lambda:self.now)
        self.begin()
        self.policy.unlock()
        self.assertTrue(self.backend.states['keyboard'])
        self.assertFalse(self.backend.states['touchpad'])
        self.assertNotIn(('touchpad',True),self.backend.changes)

    def test_independent_recovery_works_without_policy_or_panel(self):
        self.begin()
        RecoveryStore(Path(self.temp.name)).restore(self.backend)
        self.assertTrue(all(self.backend.states.values()))
        self.assertFalse(self.store.path.exists())
        self.policy.tick()
        self.assertEqual(self.policy.phase,'off')

    def test_recovery_waits_for_current_input_transaction(self):
        self.begin()
        started=threading.Event()
        finished=threading.Event()
        def recover():
            started.set()
            RecoveryStore(Path(self.temp.name)).restore(self.backend)
            finished.set()
        with self.store.transaction():
            worker=threading.Thread(target=recover)
            worker.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(.05))
            self.assertFalse(self.backend.states['keyboard'])
        worker.join(2)
        self.assertTrue(finished.is_set())
        self.assertTrue(all(self.backend.states.values()))

    def test_journal_must_be_writable_before_disabling(self):
        with patch.object(self.store,"save",side_effect=OSError("read-only")):
            with self.assertRaises(OSError): self.begin()
        self.assertEqual(self.backend.changes,[])


class DeviceIdentityTests(unittest.TestCase):
    def test_session_connection_is_private_and_does_not_change_global_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            values={'DISPLAY':':0','XDG_SESSION_TYPE':'x11','XAUTHORITY':'/tmp/test authority',
                    'DBUS_SESSION_BUS_ADDRESS':'unix:abstract=/tmp/session-bus'}
            with patch.dict(os.environ,values), patch('chuwi_tablet.runtime_directory',return_value=Path(directory)):
                write_session_environment()
                path=Path(directory)/'session.env'
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                self.assertIn('DBUS_SESSION_BUS_ADDRESS="unix:abstract=/tmp/session-bus"',path.read_text())
                self.assertEqual(os.environ['DBUS_SESSION_BUS_ADDRESS'],values['DBUS_SESSION_BUS_ADDRESS'])

    def test_power_sleep_and_hid_button_devices_are_excluded(self):
        for path in ('/sys/devices/platform/PNP0C0C/input','/sys/devices/platform/INTC1070/input'):
            self.assertIsNone(classify_device({'ID_INPUT_KEYBOARD':'1','ID_BUS':'platform'},path,'keyboard'))

    def test_internal_keyboard_requires_bus_path_and_keyboard_role(self):
        props={"ID_INPUT_KEYBOARD":"1","ID_BUS":"i8042"}
        self.assertEqual(classify_device(props,KEYBOARD.syspath,'keyboard'),'keyboard')
        self.assertIsNone(classify_device(props,KEYBOARD.syspath,'pointer'))
        self.assertIsNone(classify_device({**props,'ID_BUS':'usb'},KEYBOARD.syspath,'keyboard'))
        self.assertIsNone(classify_device(props,'/sys/devices/usb1/keyboard','keyboard'))

    def test_external_mouse_and_bluetooth_keyboard_never_selected(self):
        for path in ('/sys/devices/pci/usb1/i2c-3/input','/sys/devices/bluetooth/input'):
            self.assertIsNone(classify_device({'ID_INPUT_TOUCHPAD':'1'},path,'pointer'))
            self.assertIsNone(classify_device({'ID_INPUT_KEYBOARD':'1','ID_BUS':'usb'},path,'keyboard'))

    def test_goodix_keyboard_node_is_excluded(self):
        props={'ID_INPUT_TOUCHSCREEN':'1','ID_INPUT_KEYBOARD':'1','ID_INPUT_TOUCHPAD':'1'}
        path='/sys/devices/pci/i2c-2/input/input10'
        self.assertIsNone(classify_device(props,path,'keyboard'))
        self.assertEqual(classify_device(props,path,'pointer'),'touchscreen')

    def test_recycled_id_cannot_address_replacement_external_device(self):
        backend=XInputBackend(runner=Mock())
        backend.devices=Mock(return_value=[])
        with patch('chuwi_tablet.Path.exists',return_value=False):backend.change(KEYBOARD,True)
        backend.run.assert_not_called()
        with self.assertRaises(TabletError): backend.change(KEYBOARD,False)

    def test_present_but_unidentified_hardware_keeps_recovery_pending(self):
        backend=XInputBackend(runner=Mock())
        backend.devices=Mock(return_value=[])
        with patch('chuwi_tablet.Path.exists',return_value=True):
            with self.assertRaises(TabletError):backend.change(KEYBOARD,True)
        backend.run.assert_not_called()

    def test_restoration_uses_current_id_after_renumbering(self):
        backend=XInputBackend(runner=Mock())
        backend.devices=Mock(side_effect=[
            [replace(KEYBOARD,xid='23',enabled=False)], [replace(KEYBOARD,xid='23',enabled=True)]])
        backend.change(KEYBOARD,True)
        backend.run.assert_called_once_with(['xinput','enable','23'])

    def test_ambiguous_identity_is_rejected(self):
        backend=XInputBackend(runner=Mock())
        backend.devices=Mock(return_value=[KEYBOARD,replace(KEYBOARD,xid='23')])
        with self.assertRaises(TabletError): backend.change(KEYBOARD,False)
        backend.run.assert_not_called()

    def test_unknown_or_multiple_targets_are_rejected(self):
        backend=XInputBackend(runner=Mock())
        for devices in ([KEYBOARD],[KEYBOARD,KEYBOARD,TOUCHPAD],[KEYBOARD,TOUCHPAD]):
            backend.devices=Mock(return_value=devices)
            with self.assertRaises(TabletError): backend.preflight()

    def test_floating_button_stays_inside_all_orientation_workareas(self):
        for workarea in ((0,32,1920,1168),(0,32,1200,1888),(1920,0,1200,1920),(0,0,760,570)):
            x,y=floating_position(workarea,(320,230))
            self.assertGreaterEqual(x,workarea[0]);self.assertGreaterEqual(y,workarea[1])
            self.assertLessEqual(x+320,workarea[0]+workarea[2]);self.assertLessEqual(y+230,workarea[1]+workarea[3])

    def test_keyboard_and_recovery_controls_use_opposite_edges_in_all_orientations(self):
        for area in ((0,32,1920,1168),(0,32,1200,1888),(1920,0,1200,1920),(0,0,760,570)):
            for keyboard_at_top in (False,True):
                x,y=floating_position(area,(340,320),bottom=keyboard_at_top)
                self.assertGreaterEqual(x,area[0]);self.assertGreaterEqual(y,area[1])
                self.assertLessEqual(x+340,area[0]+area[2]);self.assertLessEqual(y+320,area[1]+area[3])
                if keyboard_at_top:
                    self.assertGreaterEqual(y,area[1]+area[3]/3)
                else:
                    self.assertLessEqual(y+320,area[1]+area[3]*2/3)


if __name__ == '__main__':
    unittest.main()
