"""Exercise GTK's real button event routing with an inert tablet client."""
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch

import gi
gi.require_version('Gtk','3.0');gi.require_version('Gdk','3.0')
from gi.repository import Gtk,Gdk


@unittest.skipUnless(os.environ.get('DISPLAY'),'Requires an X11 desktop for GTK event dispatch')
class GtkTouchDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check()[0]:raise unittest.SkipTest('GTK display unavailable')
        devices=Gdk.Display.get_default().get_device_manager().list_devices(Gdk.DeviceType.SLAVE)
        cls.touch=next((d for d in devices if d.get_source()==Gdk.InputSource.TOUCHSCREEN),None)
        cls.mouse=next((d for d in devices if d.get_source()==Gdk.InputSource.MOUSE),None)
        if cls.touch is None or cls.mouse is None:raise unittest.SkipTest('Touchscreen and mouse sources required')
        module=runpy.run_path(str(Path(__file__).resolve().parents[1]/'chuwi-control-panel'),run_name='tablet_dispatch_tests')
        cls.app=module['ControlCenter']()
        cls.app.set_application_id('io.github.kaoshonen.ChuwiTabletDispatchTest'+str(os.getpid()))
        cls.app.register(None)
        cls.app.build_window()
        cls.app.tablet=Mock()  # Never connect these test events to the guardian or real input lock.
        cls.app.tablet_changed({'phase':'trial','remaining':10,'reason':'Touch dispatch test'})
        while Gtk.events_pending():Gtk.main_iteration_do(False)

    @classmethod
    def tearDownClass(cls):
        cls.app.floating.destroy()
        cls.app.window.destroy()
        cls.app.pool.shutdown(wait=False)

    def setUp(self):
        self.app.tablet.reset_mock()
        self.app.touch_gesture.reset()

    def dispatch_click(self,source):
        for event_type in (Gdk.EventType.BUTTON_PRESS,Gdk.EventType.BUTTON_RELEASE):
            event=Gdk.Event.new(event_type)
            event.window=self.app.touch_confirm.get_event_window()
            event.send_event=False
            event.time=Gdk.CURRENT_TIME
            event.x=20;event.y=20;event.button=1
            event.set_device(source.get_associated_device())
            event.set_source_device(source)
            Gtk.main_do_event(event)

    def test_touchscreen_emulated_click_reaches_confirmation_through_gtk(self):
        self.assertEqual(self.app.touch_gesture.get_propagation_phase(),Gtk.PropagationPhase.CAPTURE)
        self.dispatch_click(self.touch)
        self.app.tablet.confirm_touch.assert_called_once()

    def test_mouse_click_through_gtk_cannot_confirm(self):
        self.dispatch_click(self.mouse)
        self.app.tablet.confirm_touch.assert_not_called()

    def test_plain_clicked_signal_cannot_confirm(self):
        self.app.touch_confirm.emit('clicked')
        self.app.tablet.confirm_touch.assert_not_called()

    def test_keyboard_buttons_dispatch_independently_without_taking_text_focus(self):
        self.assertFalse(self.app.floating.get_accept_focus())
        self.assertEqual(len(self.app.keyboard_buttons),2)
        self.app.busy=True
        try:
            with patch.object(self.app,'toggle_screen_keyboard') as request:
                for button in self.app.keyboard_buttons:
                    self.assertFalse(button.get_focus_on_click())
                    self.assertTrue(button.get_sensitive())
                    button.emit('clicked')
                self.assertEqual(request.call_count,2)
            self.app.floating_unlock.emit('clicked')
            self.app.tablet.unlock.assert_called_once()
        finally:
            self.app.busy=False


if __name__=='__main__':unittest.main()
