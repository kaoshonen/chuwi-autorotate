"""GTK page for manual filter tuning and a guided, read-only jitter check."""
import json
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango
from .trackpad_client import Client


class PadIllustration(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.region = None
        self.set_size_request(170, 104)
        self.get_accessible().set_name("Trackpad: top, middle and bottom measurement positions")
        self.connect("draw", self.draw)

    def highlight(self, region):
        self.region = region
        self.queue_draw()

    def draw(self, _widget, cr):
        width = self.get_allocated_width()
        cr.translate((width - 160) / 2, 4)
        for i, region in enumerate(("top", "middle", "bottom")):
            active = region == self.region
            cr.set_source_rgb(*((.24, .39, .75) if active else (.90, .93, .97)))
            cr.rectangle(0, i * 31, 158, 30); cr.fill()
            cr.set_source_rgb(*((1, 1, 1) if active else (.29, .35, .47)))
            cr.select_font_face("Sans"); cr.set_font_size(12)
            cr.move_to(58, i * 31 + 20); cr.show_text(region.title())
            if active:
                cr.arc(24, i * 31 + 15, 6, 0, 6.283); cr.fill()
        cr.set_source_rgb(.36, .44, .56); cr.set_line_width(1.5)
        cr.rectangle(0, 0, 158, 93); cr.stroke()
        return False


class TrackpadPage:
    def __init__(self, app, client=None):
        self.app = app
        self.client = client or Client(GLib.idle_add)
        self.status = {}
        self.dirty = False
        self.updating = False
        self.busy = False
        self.measuring = False
        self.locked = False
        self.closed = False
        self.region = None
        self.result = None
        self.loaded = False
        self.widget, page = app.page("A steadier pointer", "Tune small-movement filtering and compare jitter across your trackpad.")
        self.page = page

        card = self.card(page)
        self.text(card, "Whole-pad jitter filter", "section-title")
        self.text(card, "Stronger filtering suppresses small movements across the entire pad and may reduce fine positioning.", "subtle")
        self.adjustment = Gtk.Adjustment(value=8, lower=1, upper=64, step_increment=1, page_increment=4)
        row = Gtk.Box(spacing=12)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.adjustment)
        self.scale.set_digits(0); self.scale.set_draw_value(False); self.scale.set_hexpand(True)
        self.scale.add_mark(8, Gtk.PositionType.BOTTOM, "Recommended")
        self.scale.get_accessible().set_name("Trackpad filtering strength")
        self.spin = Gtk.SpinButton(adjustment=self.adjustment, climb_rate=1, digits=0)
        self.spin.set_numeric(True)
        self.spin.get_accessible().set_name("Trackpad strength, 1 to 64")
        row.pack_start(self.scale, True, True, 0); row.pack_end(self.spin, False, False, 0)
        card.pack_start(row, False, False, 0)
        self.adjustment.connect("value-changed", self.changed)
        self.draft_label = self.text(card, "Recommended starting strength: 8", "subtle")
        buttons = Gtk.Box(spacing=8)
        self.apply_button = self.button(buttons, "Apply", self.apply)
        self.apply_button.get_style_context().add_class("suggested-action")
        self.reset_button = self.button(buttons, "Reset to recommended (8)", lambda: self.adjustment.set_value(8))
        card.pack_start(buttons, False, False, 0)
        self.text(card, "Apply asks for administrator authentication. Reboot afterward so the desktop adopts the saved filter.", "subtle")

        status_card = self.card(page)
        self.device_label = self.text(status_card, "Reading touchpad status…")
        self.saved_label = self.text(status_card, "Saved strength: —", "subtle")
        self.properties_label = self.text(status_card, "System properties: —", "subtle")
        self.reboot_label = self.text(status_card, "Reboot status: —", "subtle")
        self.reason_label = self.text(status_card, "", "subtle")
        self.refresh_button = self.button(status_card, "Refresh status", self.refresh)
        self.refresh_button.set_halign(Gtk.Align.START)

        check_card = self.card(page)
        self.text(check_card, "Check jitter", "section-title")
        self.text(check_card, "For an intermittent problem, run this while jitter is visible. Hold one finger still in each highlighted region: 2 seconds to settle, then 8 seconds to measure. Results guide manual tuning; they never change strength.", "subtle")
        illustration_row = Gtk.Box(spacing=14)
        self.illustration = PadIllustration()
        illustration_row.pack_start(self.illustration, False, False, 0)
        self.progress_label = self.text(illustration_row, "Ready to compare top, middle and bottom.", "subtle")
        self.progress_label.set_hexpand(True)
        check_card.pack_start(illustration_row, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.check_button = self.button(actions, "Check jitter", self.measure)
        self.ready_button = self.button(actions, "I’m ready", self.ready)
        self.cancel_button = self.button(actions, "Cancel", self.cancel)
        check_card.pack_start(actions, False, False, 0)
        self.result_label = self.text(check_card, "", "subtle")
        self.export_button = self.button(check_card, "Export results as JSON…", self.export)
        self.export_button.set_halign(Gtk.Align.START)
        self.text(check_card, "Measurements use emitted touch events, which may already be filtered. Few samples do not prove the pad is steady. Millimetres depend on the device’s reported resolution.", "subtle")

        expander = Gtk.Expander(label="Diagnostics")
        self.diagnostics = Gtk.Label(xalign=0, selectable=True, wrap=True)
        self.diagnostics.set_max_width_chars(45)
        self.diagnostics.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        expander.add(self.diagnostics); page.pack_start(expander, False, False, 0)
        self.message = self.text(page, "", "subtle")
        self.update_controls()

    @staticmethod
    def card(parent):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("card")
        parent.pack_start(card, False, False, 0)
        return card

    @staticmethod
    def text(parent, value, style=None):
        widget = Gtk.Label(label=value, xalign=0, wrap=True)
        widget.set_max_width_chars(48)
        widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if style:
            widget.get_style_context().add_class(style)
        parent.pack_start(widget, False, False, 0)
        return widget

    @staticmethod
    def button(parent, title, callback):
        widget = Gtk.Button(label=title)
        widget.connect("clicked", lambda *_: callback())
        parent.pack_start(widget, False, False, 0)
        return widget

    def changed(self, *_):
        if not self.updating:
            self.dirty = True
        self.draft_label.set_text(f"Selected: {int(self.adjustment.get_value())}" + (" · Not saved" if self.dirty else ""))

    def update_controls(self):
        eligible = bool(self.status.get("can_apply"))
        self.apply_button.set_sensitive(eligible and not self.busy)
        self.scale.set_sensitive(not self.busy)
        self.spin.set_sensitive(not self.busy)
        self.reset_button.set_sensitive(not self.busy)
        self.refresh_button.set_sensitive(not self.busy)
        self.check_button.set_sensitive(bool(self.status.get("device")) and not self.busy and not self.locked)
        self.ready_button.set_sensitive(self.measuring and self.region is not None and not self.locked)
        self.cancel_button.set_sensitive(self.measuring)
        self.export_button.set_sensitive(self.result is not None and not self.busy)

    def refresh(self):
        if self.busy or self.closed:
            return
        self.busy = True; self.update_controls()
        self.client.submit("status", self.receive_status)

    def receive_status(self, status):
        self.busy = False
        if status.get("type") in ("error", "cancelled"):
            self.message.set_text(status["message"])
            self.status = {}
            self.reason_label.set_text("Status unavailable; refresh before applying.")
            self.update_controls(); return
        self.status = status
        if not self.dirty:
            self.updating = True
            self.adjustment.set_value(status.get("selected_default", 8))
            self.updating = False
            self.changed_without_edit()
        self.loaded = True
        device = status.get("device")
        self.device_label.set_text("Internal I²C touchpad detected" if device else "Touchpad unavailable or ambiguous")
        saved = status.get("saved_strength")
        self.saved_label.set_text(f"Saved strength: {saved}" if saved else "Saved strength: none · Recommended: 8")
        self.properties_label.set_text("All four system axes match the saved strength" if status.get("properties_match") else "System axis properties do not all match a saved strength")
        self.reboot_label.set_text({"needed": "Reboot needed", "not_needed": "Saved before this boot · No further reboot needed",
                                   "not_configured": "No saved filter to activate", "unknown": "Reboot status unknown · Earlier fix has no matching apply record"}.get(status.get("reboot"), "Reboot status unknown"))
        self.reason_label.set_text(" ".join(status.get("errors", [])))
        self.diagnostics.set_text(json.dumps(status, indent=2, ensure_ascii=False))
        self.update_controls()

    def changed_without_edit(self):
        self.draft_label.set_text(f"Selected: {int(self.adjustment.get_value())}" + (" · Not saved" if self.dirty else ""))

    def apply(self):
        if self.busy or not self.status.get("can_apply"):
            return
        self.spin.update()
        self.busy = True; self.update_controls()
        self.message.set_text("Waiting for administrator authentication…")
        self.client.submit("apply", self.applied, int(self.adjustment.get_value()))

    def applied(self, event):
        self.busy = False
        if event.get("type") == "applied":
            self.dirty = False
            self.receive_status(event["status"])
            self.message.set_text("Strength saved. Reboot when convenient to activate it in the desktop.")
        else:
            self.message.set_text(event.get("message", "Unable to save strength."))
            self.refresh()
        self.update_controls()

    def set_locked(self, locked):
        self.locked = locked
        if locked and self.measuring:
            self.cancel()
        if locked:
            self.progress_label.set_text("Unlock keyboard & touchpad before checking jitter.")
        elif not self.measuring:
            self.progress_label.set_text("Ready to compare top, middle and bottom.")
        self.update_controls()

    def measure(self):
        if self.busy or self.locked or not self.status.get("device"):
            return
        self.busy = self.measuring = True
        self.region = None; self.result = None
        self.result_label.set_text("")
        self.progress_label.set_text("Authenticate once to read touchpad movement. No settings will change.")
        self.client.submit("measure", self.measure_event)
        self.update_controls()

    def measure_event(self, event):
        kind = event.get("type")
        if kind == "ready":
            self.region = event["region"]
            self.illustration.highlight(self.region)
            self.progress_label.set_text(f"Lift all fingers, choose I’m ready, then hold one finger still at the {self.region}.")
        elif kind == "progress":
            self.region = None
            self.illustration.highlight(event["region"])
            title = {"place_finger": "Place one finger now", "settling": "Settling — hold still", "measuring": "Measuring — hold still"}[event["phase"]]
            self.progress_label.set_text(f"{event['region'].title()}: {title}\n{event['seconds_remaining']} seconds")
        elif kind == "region_result":
            result = event["result"]
            previous = self.result_label.get_text()
            self.result_label.set_text(previous + ("\n" if previous else "") + self.format_result(result))
        elif kind in ("result", "error", "cancelled"):
            self.busy = self.measuring = False; self.region = None
            self.illustration.highlight(None)
            if kind == "result":
                self.result = event["result"]
                self.progress_label.set_text("Check complete. Compare regions before adjusting strength.")
            else:
                self.progress_label.set_text(event.get("message", "Check stopped."))
        self.update_controls()

    @staticmethod
    def format_result(result):
        spread = result.get("spread", {})
        axes = []
        for axis in ("x", "y"):
            if axis in spread:
                values = spread[axis]
                key, unit = ("peak_to_peak_mm", "mm") if "peak_to_peak_mm" in values else ("peak_to_peak_units", "units")
                axes.append(f"{axis.upper()} {values[key]:.2f} {unit}")
        quality = {"sufficient": "sufficient samples", "insufficient": "insufficient data", "wrong_region": "finger appears outside this region"}[result["sufficiency"]]
        return f"{result['label'].title()}: " + (" · ".join(axes) or "No measurable spread") + f" · {result['sample_count']} samples · {quality}"

    def ready(self):
        if self.region and self.measuring and not self.locked:
            self.client.ready(self.region)
            self.region = None
            self.update_controls()

    def cancel(self):
        self.client.cancel()
        self.region = None
        self.progress_label.set_text("Stopping measurement…")
        self.update_controls()

    def export(self):
        if self.result is None:
            return
        dialog = Gtk.FileChooserDialog(title="Export jitter results", transient_for=self.app.window,
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT)
        dialog.set_current_name("minibook-jitter.json")
        dialog.set_do_overwrite_confirmation(True)
        try:
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                with open(dialog.get_filename(), "w") as stream:
                    json.dump(self.result, stream, indent=2, allow_nan=False)
                    stream.write("\n")
                self.message.set_text("Measurement results exported.")
        except OSError as exc:
            self.message.set_text(f"Export failed: {exc}")
        finally:
            dialog.destroy()

    def close(self):
        self.closed = True
        self.client.close()
