"""Non-blocking panel client; input recovery never uses the rotation worker queue."""
import json
import os
from pathlib import Path
import subprocess
import threading

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from chuwi_tablet import BUS, OBJECT, SERVICE, write_session_environment


class TabletClient:
    def __init__(self, on_state, on_ready):
        self.on_state, self.on_ready = on_state, on_ready
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.present = False
        self.starting = False
        self.recovering = False
        self.closed = False
        self.renewing = False
        self.activation = 0
        self.automatic = False
        self.releasing = False
        self.state = {"phase": "off", "reason": "Keyboard and touchpad are available"}
        self.subscription = self.connection.signal_subscribe(BUS, BUS, "StateChanged", OBJECT, None,
                                                             Gio.DBusSignalFlags.NONE, self.changed)
        self.watch = Gio.bus_watch_name_on_connection(self.connection, BUS, Gio.BusNameWatcherFlags.NONE,
                                                      self.appeared, self.vanished)
        self.timer = GLib.timeout_add(1000, self.heartbeat)

    def set_state(self, state):
        self.state = state
        if not self.closed:
            self.on_state(state)

    def changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        state = json.loads(parameters.unpack()[0])
        if not (self.starting and state["phase"] == "off"):
            self.set_state(state)

    def appeared(self, _connection, _name, _owner):
        self.present = True
        if not self.starting:
            self.call("Status")

    def vanished(self, _connection, _name):
        was_active = self.state.get("phase") in ("trial", "locked", "recovering", "error")
        self.present = False
        if was_active and not self.closed:
            self.emergency_unlock("Guardian disconnected; restoring keyboard and touchpad")

    def call(self, method, done=None):
        def complete(connection, result, _data):
            self.renewing = False
            if method == "Release":
                self.releasing = False
            if self.closed:
                return
            try:
                state = json.loads(connection.call_finish(result).unpack()[0])
                self.set_state(state)
                if done:
                    done(state)
            except Exception as exc:
                if method == "Status" and self.state.get("phase") == "off":
                    return
                self.emergency_unlock(f"Tablet control interrupted: {exc}")
        self.connection.call(BUS, OBJECT, BUS, method, None, GLib.VariantType.new("(s)"),
                             Gio.DBusCallFlags.NO_AUTO_START, 4000 if method == "BeginTest" else 2500, None, complete, None)

    def start(self, automatic=False):
        if self.starting or self.state.get("phase") != "off":
            return
        self.starting = True
        self.automatic = automatic
        self.activation += 1
        activation = self.activation
        self.set_state({"phase": "arming", "reason": "Checking independent keyboard recovery…"})
        def work():
            try:
                from chuwi.ec import Client
                import runpy
                engine = runpy.run_path(str(Path(__file__).resolve().parent / "chuwi-autorotate"))
                config = engine["read_config"](create=False)
                if config.get("input_backend") != "xinput":
                    try:
                        helper = Client()
                        writable = helper.call("Capabilities").get("writable")
                    except Exception:
                        writable = False
                    if writable:
                        helper.call("Authorize", timeout=60000)
                    elif config.get("input_backend") == "ec":
                        raise RuntimeError("EC helper is unavailable or hardware is unapproved")
                write_session_environment()
                result = subprocess.run(["systemctl", "--user", "start", SERVICE], capture_output=True, text=True, timeout=15)
                if result.returncode:
                    raise RuntimeError(result.stderr or result.stdout)
                GLib.idle_add(self.ready, activation)
            except Exception as exc:
                GLib.idle_add(self.start_failed, str(exc), activation)
        threading.Thread(target=work, daemon=True).start()

    def ready(self, activation):
        if self.closed or activation != self.activation or self.state.get("phase") != "arming":
            return False
        self.starting = False
        self.present = True
        self.on_ready()  # The panel must map its floating recovery window before BeginTest.
        return False

    def start_failed(self, reason, activation):
        if self.closed or activation != self.activation:
            return False
        self.starting = False
        self.emergency_unlock(f"Tablet mode was not enabled: {reason}")
        return False

    def begin(self):
        if not self.closed and self.state.get("phase") == "arming":
            self.call("BeginAutomatic" if self.automatic else "BeginTest")

    def release(self):
        if self.present and not self.releasing and self.state.get("phase") in ("trial", "locked"):
            self.releasing = True
            self.call("Release")

    def confirm_touch(self):
        if self.state.get("phase") == "trial":
            self.call("Confirm")

    def heartbeat(self):
        if self.closed:
            return False
        if self.state.get("phase") in ("trial", "locked") and not self.renewing:
            self.renewing = True
            self.call("Renew")
        return True

    def unlock(self):
        self.activation += 1  # Cancel any delayed start/ready callback before restoring input.
        self.starting = False
        self.set_state({"phase": "recovering", "reason": "Restoring keyboard and touchpad…"})
        if self.present:
            self.call("Unlock")
        else:
            self.emergency_unlock()

    def emergency_unlock(self, reason="Restoring keyboard and touchpad…"):
        if self.recovering or self.closed:
            return
        self.recovering = True
        self.activation += 1
        self.starting = False
        self.set_state({"phase": "recovering", "reason": reason})
        executable = Path(__file__).resolve().parent / "chuwi-autorotate"
        def work():
            try:
                result = subprocess.run([str(executable), "tablet-mode", "off"], capture_output=True, text=True, timeout=22)
                state = {"phase": "off" if result.returncode == 0 else "error",
                         "reason": "Keyboard and touchpad restored" if result.returncode == 0 else (result.stderr or result.stdout).strip()}
            except Exception as exc:
                state = {"phase": "error", "reason": f"Input recovery pending: {exc}"}
            GLib.idle_add(self.recovered, state)
        threading.Thread(target=work, daemon=True).start()

    def recovered(self, state):
        self.recovering = False
        self.set_state(state)
        return False

    def close(self):
        self.closed = True
        GLib.source_remove(self.timer)
        Gio.bus_unwatch_name(self.watch)
        self.connection.signal_unsubscribe(self.subscription)
        if self.present:
            # A dropped panel connection or expired lease provides independent fallback.
            self.connection.call(BUS, OBJECT, BUS, "Unlock", None, None,
                                 Gio.DBusCallFlags.NO_AUTO_START, 1500, None, None, None)
