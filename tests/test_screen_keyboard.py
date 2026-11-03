"""Keep keyboard requests asynchronous and independent of input recovery."""
from pathlib import Path
import runpy
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

panel=runpy.run_path(str(Path(__file__).resolve().parents[1]/'chuwi-control-panel'),run_name='screen_keyboard_tests')
ControlCenter=panel['ControlCenter']
Gio=panel['Gio']


class ScreenKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.ui=SimpleNamespace(keyboard_pending=False,closing=False,keyboard_buttons=[Mock(),Mock()],
                                keyboard_hint=Mock(),floating=None,position_floating=Mock(),
                                tablet=Mock(),busy=True)
        for name in ('toggle_screen_keyboard','keyboard_bus_ready','keyboard_done','keyboard_finished','unlock_tablet'):
            setattr(self.ui,name,MethodType(getattr(ControlCenter,name),self.ui))

    def test_slow_keyboard_request_does_not_block_unlock_or_use_rotation_worker(self):
        with patch.object(Gio,'bus_get') as connect:
            self.ui.toggle_screen_keyboard()
            self.ui.toggle_screen_keyboard()  # Ignore a double tap while pending.
        connect.assert_called_once()
        self.assertTrue(self.ui.keyboard_pending)
        self.ui.unlock_tablet()
        self.ui.tablet.unlock.assert_called_once()

    def test_native_toggle_is_bounded_asynchronous_and_does_not_activate_a_new_service(self):
        connection=Mock()
        with patch.object(Gio,'bus_get_finish',return_value=connection):
            self.ui.keyboard_bus_ready(None,object(),None)
        args=connection.call.call_args.args
        self.assertEqual(args[:4],('org.Cinnamon','/org/Cinnamon','org.Cinnamon','ToggleKeyboard'))
        self.assertEqual(args[6],Gio.DBusCallFlags.NO_AUTO_START)
        self.assertEqual(args[7],1500)
        self.assertEqual(args[9],self.ui.keyboard_done)

    def test_service_failure_keeps_recovery_available_and_allows_retry(self):
        self.ui.keyboard_pending=True
        self.ui.floating=Mock()
        self.ui.floating_keyboard_notice=Mock()
        connection=Mock()
        connection.call_finish.side_effect=RuntimeError('Cinnamon unavailable')
        self.ui.keyboard_done(connection,object(),None)
        self.assertFalse(self.ui.keyboard_pending)
        self.assertIn('unavailable',self.ui.keyboard_error)
        self.ui.floating_keyboard_notice.set_visible.assert_called_with(True)
        for button in self.ui.keyboard_buttons:button.set_sensitive.assert_called_with(True)
        self.ui.unlock_tablet()
        self.ui.tablet.unlock.assert_called_once()

    def test_connection_failure_can_be_retried_and_success_clears_error(self):
        with patch.object(Gio,'bus_get_finish',side_effect=RuntimeError('No session bus')):
            self.ui.keyboard_bus_ready(None,object(),None)
        self.assertIn('unavailable',self.ui.keyboard_error)
        self.ui.keyboard_finished()
        self.assertIsNone(self.ui.keyboard_error)

    def test_closed_panel_cannot_open_a_keyboard_from_late_callback(self):
        self.ui.closing=True
        with patch.object(Gio,'bus_get') as connect,patch.object(Gio,'bus_get_finish') as finish:
            self.ui.toggle_screen_keyboard()
            self.ui.keyboard_bus_ready(None,object(),None)
        connect.assert_not_called();finish.assert_not_called()


if __name__=='__main__':unittest.main()
