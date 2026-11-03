"""Exercise the real GTK controls with inert clients; never touches input hardware."""
import os
from pathlib import Path
import runpy
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import Mock, patch

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk
from chuwi.trackpad_preview import PreviewClient, preview_status
from chuwi.trackpad_ui import TrackpadPage

ROOT = Path(__file__).resolve().parents[1]


def pump(duration=.05):
    until = time.monotonic() + duration
    while time.monotonic() < until:
        while Gtk.events_pending(): Gtk.main_iteration_do(False)
        time.sleep(.005)


@unittest.skipUnless(os.environ.get("DISPLAY"), "Requires GTK display")
class GtkTrackpadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check()[0]: raise unittest.SkipTest("GTK display unavailable")
        cls.module = runpy.run_path(str(ROOT / "chuwi-trackpad-preview"), run_name="trackpad_gtk_test")

        cls.app = cls.module["Preview"]()
        cls.app.register(None)
        cls.app.do_activate(); pump(.3)

    @classmethod
    def tearDownClass(cls):
        cls.app.window.destroy()
        cls.app.pool.shutdown(wait=False, cancel_futures=True)
        pump()

    def setUp(self):
        previous = self.app.trackpad_page
        previous.close()
        self.app.stack.remove(previous.widget)
        self.app.trackpad_page = TrackpadPage(self.app, PreviewClient())
        self.page = self.app.trackpad_page
        self.app.stack.add_named(self.page.widget, "trackpad")
        self.app.window.show_all()
        self.app.stack.set_visible_child_name("trackpad")
        self.page.refresh(); pump(.3)

    def tearDown(self):
        self.page.close()
        pump()

    def test_linked_controls_reset_and_dirty_refresh(self):
        self.page.spin.set_value(17)
        self.assertEqual(self.page.scale.get_value(), 17)
        self.page.refresh_button.emit("clicked"); pump()
        self.assertEqual(self.page.spin.get_value_as_int(), 17)
        self.assertTrue(self.page.dirty)
        self.page.reset_button.emit("clicked")
        self.assertEqual(self.page.scale.get_value(), 8)
        self.assertTrue(self.page.dirty)
        self.page.scale.set_value(22)
        self.page.apply_button.emit("clicked"); pump()
        self.assertEqual(self.page.status["saved_strength"], 22)
        self.assertFalse(self.page.dirty)
        self.assertEqual(self.page.reboot_label.get_text(), "Reboot needed")

    def test_missing_ambiguous_and_auth_cancel_keep_draft(self):
        self.page.spin.set_value(19)
        self.page.applied({"type": "cancelled", "message": "Authentication cancelled; settings unchanged."}); pump()
        self.assertEqual(self.page.spin.get_value_as_int(), 19)
        self.assertEqual(self.page.status["saved_strength"], 8)
        self.page.receive_status({**preview_status(), "device": None, "can_apply": False, "errors": ["found 2 touchpads"]})
        self.assertFalse(self.page.apply_button.get_sensitive())
        self.assertFalse(self.page.check_button.get_sensitive())
        self.assertIn("found 2", self.page.reason_label.get_text())

    def test_measurement_progress_lock_cancel_and_unlock_responsive(self):
        self.page.check_button.emit("clicked"); pump()
        self.assertTrue(self.page.measuring)
        self.assertEqual(self.page.region, "top")
        self.page.ready_button.emit("clicked"); pump()
        self.assertFalse(self.page.ready_button.get_sensitive())
        self.assertIn("Settling", self.page.progress_label.get_text())
        self.app.header_unlock.emit("clicked")
        self.assertIn("Unlock remains responsive", self.app.message.get_text())
        self.page.set_locked(True); pump()
        self.assertFalse(self.page.measuring)
        self.assertFalse(self.page.check_button.get_sensitive())
        self.page.set_locked(False)
        self.assertTrue(self.page.check_button.get_sensitive())

    def test_complete_guided_results_and_close_cancels(self):
        self.page.measure(); pump()
        for region in ("top", "middle", "bottom"):
            self.assertEqual(self.page.region, region)
            self.page.ready(); pump(1.1)
        self.assertTrue(self.page.export_button.get_sensitive())
        self.assertEqual(len(self.page.result["trials"]), 3)
        self.assertIn("mm", self.page.result_label.get_text())
        self.page.measure(); pump()
        client = self.page.client
        self.page.close()
        self.assertTrue(client.closed)

    def test_minimum_and_desktop_layout(self):
        for width, height in ((760, 570), (1000, 800)):
            self.app.window.resize(width, height); pump(.2)
            actual_width, actual_height = self.app.window.get_size()
            self.assertLessEqual(actual_width, width)
            self.assertLessEqual(actual_height, height)
            for widget in (self.page.apply_button, self.page.spin, self.page.reset_button):
                allocation = widget.get_allocation()
                self.assertGreater(allocation.width, 0)
                translated = widget.translate_coordinates(self.app.window, 0, 0)
                self.assertLessEqual(translated[0] + allocation.width, width)
            if os.environ.get("CHUWI_PREVIEW_SCREENSHOTS"):
                surface = self.app.window.get_window()
                image = Gdk.pixbuf_get_from_window(surface, 0, 0, surface.get_width(), surface.get_height())
                image.savev(f"/tmp/chuwi-trackpad-preview-{width}.png", "png", [], [])
                adjustment = self.page.widget.get_vadjustment()
                adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size()); pump(.1)
                image = Gdk.pixbuf_get_from_window(surface, 0, 0, surface.get_width(), surface.get_height())
                image.savev(f"/tmp/chuwi-trackpad-check-{width}.png", "png", [], [])
                adjustment.set_value(0)

    def test_real_panel_unlock_independent_of_blocked_trackpad_worker(self):
        # Use production Unlock dispatch with a stand-in guardian, not the preview override.
        self.app.tablet = Mock()
        self.page.busy = True; self.page.measuring = True
        parent = self.module["module"]["ControlCenter"]
        parent.unlock_tablet(self.app)
        self.app.tablet.unlock.assert_called_once()
        self.assertFalse(self.app.busy)


if __name__ == "__main__": unittest.main()
