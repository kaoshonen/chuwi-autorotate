"""Test touch confirmation at the UI event boundary without touching hardware."""
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

root=Path(__file__).resolve().parents[1]
panel=runpy.run_path(str(root/'chuwi-control-panel'),run_name='tablet_ui_event_tests')
legacy=runpy.run_path(str(root/'chuwi-autorotate-panel'),run_name='tablet_legacy_tests')
Gdk=panel['Gdk']


class TouchConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.ui=SimpleNamespace(tablet=Mock(),tablet_state={'phase':'trial'},touch_confirm=Mock())
        self.ui.touch_confirm.get_allocated_width.return_value=280
        self.ui.touch_confirm.get_allocated_height.return_value=60
        self.device=Mock()
        self.device.get_source.return_value=Gdk.InputSource.TOUCHSCREEN
        self.event=SimpleNamespace(type=Gdk.EventType.TOUCH_END,send_event=False,get_source_device=lambda:self.device,
                                   button=1,get_button=lambda:(True,self.event.button))
        self.gesture=Mock()
        self.gesture.get_current_sequence.return_value=object()
        self.gesture.get_last_event.return_value=self.event
        self.gesture.get_touch_only.return_value=True

    def confirm(self,x=10,y=10):
        panel['ControlCenter'].confirm_tablet_touch(self.ui,self.gesture,1,x,y)

    def test_touch_end_inside_button_can_confirm_trial(self):
        self.confirm()
        self.ui.tablet.confirm_touch.assert_called_once()

    def test_touchscreen_emulated_primary_release_can_confirm_without_sequence(self):
        self.gesture.get_current_sequence.return_value=None
        self.event.type=Gdk.EventType.BUTTON_RELEASE
        self.event.button=1
        self.confirm()
        self.ui.tablet.confirm_touch.assert_called_once()

    def test_emulated_release_still_rejects_mouse_secondary_and_injected_events(self):
        self.gesture.get_current_sequence.return_value=None
        self.event.type=Gdk.EventType.BUTTON_RELEASE
        self.event.button=1
        self.device.get_source.return_value=Gdk.InputSource.MOUSE
        self.confirm()
        self.device.get_source.return_value=Gdk.InputSource.TOUCHSCREEN
        self.event.button=3
        self.confirm()
        self.event.button=1
        self.event.send_event=True
        self.confirm()
        self.ui.tablet.confirm_touch.assert_not_called()

    def test_mouse_and_injected_events_cannot_confirm(self):
        self.device.get_source.return_value=Gdk.InputSource.MOUSE
        self.confirm()
        self.device.get_source.return_value=Gdk.InputSource.TOUCHSCREEN
        self.event.send_event=True
        self.confirm()
        self.ui.tablet.confirm_touch.assert_not_called()

    def test_no_sequence_and_touch_begin_cannot_confirm(self):
        self.event.type=Gdk.EventType.TOUCH_BEGIN
        self.confirm()
        self.event.type=Gdk.EventType.TOUCH_END
        self.gesture.get_current_sequence.return_value=None
        self.confirm()
        self.ui.tablet.confirm_touch.assert_not_called()

    def test_drag_outside_button_cannot_confirm(self):
        for x,y in ((-1,10),(280,10),(10,-1),(10,60)):self.confirm(x,y)
        self.ui.tablet.confirm_touch.assert_not_called()

    def test_expired_trial_and_non_touch_gesture_cannot_confirm(self):
        self.ui.tablet_state={'phase':'off'}
        self.confirm()
        self.ui.tablet_state={'phase':'trial'}
        self.gesture.get_touch_only.return_value=False
        self.confirm()
        self.ui.tablet.confirm_touch.assert_not_called()


class LegacyTabletTests(unittest.TestCase):
    def test_old_tablet_entry_point_cannot_change_osk_or_bypass_guardian(self):
        function=legacy['set_tablet_mode_process']
        enable,restore,protected=Mock(),Mock(),Mock()
        with patch.dict(function.__globals__,enable_onboard_keyboard_process=enable,
                        restore_onboard_keyboard_process=restore,set_tablet_lock_process=protected):
            function(True,True)
            function(False,True)
        enable.assert_not_called()
        restore.assert_not_called()
        self.assertEqual(protected.call_count,2)


if __name__=='__main__':unittest.main()
