"""Inert fixtures for the source preview and GTK interaction tests."""
from copy import deepcopy
from gi.repository import GLib


def preview_status(value=8):
    return {"schema_version": 1, "can_apply": True, "saved_strength": value, "selected_default": value,
            "device": {"name": "Simulated MiniBook X touchpad", "node": "preview"},
            "properties_match": True, "reboot": "unknown", "errors": [],
            "diagnostics": {"preview": True, "hardware_actions": "disabled"}}


class PreviewClient:
    def __init__(self):
        self.status = preview_status()
        self.closed = False
        self.generation = 0
        self.callback = None
        self.trials = []

    def emit(self, callback, event):
        def deliver():
            if not self.closed:
                callback(deepcopy(event))
            return False
        GLib.idle_add(deliver)

    def submit(self, action, callback, *args):
        if action == "status":
            self.emit(callback, self.status)
        elif action == "apply":
            self.status = preview_status(int(args[0]))
            self.status["reboot"] = "needed"
            self.emit(callback, {"type": "applied", "status": self.status})
        elif action == "measure":
            self.generation += 1
            self.callback = callback
            self.trials = []
            self.emit(callback, {"type": "ready", "region": "top"})

    def ready(self, region):
        generation = self.generation
        callback = self.callback
        self.emit(callback, {"type": "progress", "region": region, "phase": "settling", "seconds_remaining": 2})
        def done():
            if self.closed or generation != self.generation:
                return False
            index = ("top", "middle", "bottom").index(region)
            result = {"label": region, "sufficiency": "sufficient", "sample_count": 80,
                      "spread": {a: {"peak_to_peak_mm": v, "peak_to_peak_units": v * 30}
                                 for a, v in (("x", (.6, .3, .1)[index]), ("y", (.8, .4, .1)[index]))},
                      "contacts": []}
            self.trials.append(result)
            self.emit(callback, {"type": "region_result", "result": result})
            if index < 2:
                self.emit(callback, {"type": "ready", "region": ("middle", "bottom")[index]})
            else:
                self.emit(callback, {"type": "result", "result": {"schema_version": 1, "preview": True, "trials": self.trials}})
            return False
        GLib.timeout_add(1000, done)

    def cancel(self):
        self.generation += 1
        if self.callback:
            self.emit(self.callback, {"type": "cancelled", "message": "Preview check cancelled."})

    def close(self):
        self.closed = True
        self.generation += 1
