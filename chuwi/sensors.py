"""One claimed SensorProxy connection; versioned optional CHUWI snapshots."""
from dataclasses import dataclass, replace
import math
import threading
import time

BUS = "net.hadess.SensorProxy"
PATH = "/net/hadess/SensorProxy"
EXTENSION = "io.github.kaoshonen.ChuwiSensor1"
ORIENTATIONS = ("normal", "right-up", "bottom-up", "left-up")


@dataclass(frozen=True)
class Sample:
    orientation: str = "undefined"
    mode: str = "unknown"
    hinge_degrees: float | None = None
    gravity: tuple | None = None
    provider: str = "unknown"
    frame: str = ""
    input_owner: str = "unknown"
    valid: bool = False
    received: float = 0
    generation: int = 0
    error: str = "Waiting for sensor service"

    def fresh(self, now):
        return self.valid and (self.provider == "standard" or 0 <= now-self.received < 3)


def decode(payload, now=None, generation=0):
    now = time.monotonic() if now is None else now
    try:
        if type(payload.get("version")) is not int or payload.get("version") != 1:
            raise ValueError("Unsupported sensor interface version")
        if type(payload.get('valid')) is not bool or type(payload.get('hinge_valid')) is not bool:
            raise ValueError('Invalid snapshot validity flags')
        stamp = payload["sample_monotonic_us"]/1_000_000
        if not math.isfinite(stamp) or not 0 <= now-stamp < 3:
            raise ValueError("Stale sensor sample")
        gravity = tuple(float(v) for v in payload["display_gravity"])
        if len(gravity) != 3 or not all(math.isfinite(v) for v in gravity):
            raise ValueError("Invalid display gravity")
        if not .1 < math.sqrt(sum(v*v for v in gravity)) < 4:
            raise ValueError("Display gravity magnitude is invalid")
        angle = payload.get("hinge_degrees") if payload.get("hinge_valid") else None
        if angle is not None and (not math.isfinite(angle) or not 0 <= angle <= 360.5):
            raise ValueError("Invalid hinge angle")
        mode = payload.get("mode", "unknown")
        if mode not in ("laptop", "tablet") or angle is None:
            mode = "unknown"
        orientation = payload.get("orientation", "undefined")
        if orientation not in ORIENTATIONS:
            raise ValueError("Invalid orientation")
        return Sample(orientation, mode, angle, gravity, "extended", payload.get("frame", ""),
                      payload.get("input_owner", "unknown"), bool(payload.get("valid")), stamp, generation, "")
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return Sample(provider="extended", generation=generation, error=str(exc))


class SensorBroker:
    def __init__(self):
        self.sample = Sample()
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.stopping = False
        self.thread = None
        self.generation = 0
        self.context = None
        self.loop = None
        self.proxy = None
        self.extension = None
        self.claimed_owner = None
        self.last_activation = 0

    def snapshot(self):
        with self.lock:
            return self.sample

    def publish(self, sample):
        with self.lock:
            self.sample = sample
        self.ready.set()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="sensor-dbus")
        self.thread.start()
        return self

    def _run(self):
        from gi.repository import Gio, GLib
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        self.loop = GLib.MainLoop.new(self.context, False)
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, BUS, PATH, BUS, None)
            self.proxy.connect("notify::g-name-owner", self._owner_changed)
            self.proxy.connect("g-properties-changed", self._properties)
            self._owner_changed()
            source = GLib.timeout_source_new_seconds(2)
            source.set_callback(self._retry)
            source.attach(self.context)
            self.proxy.get_connection().signal_subscribe('org.freedesktop.login1', 'org.freedesktop.login1.Manager',
                'PrepareForSleep', '/org/freedesktop/login1', None, Gio.DBusSignalFlags.NONE, self._sleep)
            if not self.stopping:
                self.loop.run()
        except Exception as exc:
            self.publish(Sample(error=str(exc)))
        finally:
            if self.proxy and self.claimed_owner == self.proxy.get_name_owner():
                try:
                    self.proxy.call_sync("ReleaseAccelerometer", None, Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
                except Exception:
                    pass
            self.context.pop_thread_default()

    def _retry(self, *_):
        if not self.proxy.get_name_owner() and time.monotonic()-self.last_activation > 10:
            from gi.repository import Gio, GLib
            self.last_activation = time.monotonic()
            try:
                self.proxy.get_connection().call_sync('org.freedesktop.DBus','/org/freedesktop/DBus',
                    'org.freedesktop.DBus','StartServiceByName',GLib.Variant('(su)',(BUS,0)),None,
                    Gio.DBusCallFlags.NONE,3000,None)
            except Exception as exc:
                self.publish(Sample(error=str(exc)))
        if not self.claimed_owner and self.proxy.get_name_owner():
            self._owner_changed()
        return not self.stopping

    def _sleep(self, *_):
        self.generation += 1
        self.publish(Sample(generation=self.generation, error="Suspend/resume requires fresh sensor data"))
        if _ and hasattr(_[-1], 'unpack') and _[-1].unpack() == (False,):
            from gi.repository import Gio
            if self.claimed_owner:
                try:
                    self.proxy.call_sync('ReleaseAccelerometer',None,Gio.DBusCallFlags.NO_AUTO_START,1000,None)
                except Exception:
                    pass
            self._owner_changed()

    def _owner_changed(self, *_):
        from gi.repository import Gio, GLib
        self.generation += 1
        self.extension = None
        self.claimed_owner = None
        self.publish(Sample(generation=self.generation, error="Sensor service changed; waiting for fresh data"))
        owner = self.proxy.get_name_owner()
        if not owner or self.stopping:
            return
        try:
            connection = self.proxy.get_connection()
            xml = connection.call_sync(owner, PATH, "org.freedesktop.DBus.Introspectable", "Introspect",
                                       None, None, Gio.DBusCallFlags.NO_AUTO_START, 1500, None).unpack()[0]
            import xml.etree.ElementTree as ET
            if any(node.get("name") == EXTENSION for node in ET.fromstring(xml).findall("interface")):
                self.extension = Gio.DBusProxy.new_sync(connection, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                                                       None, owner, PATH, EXTENSION, None)
                self.extension.connect("g-properties-changed", self._properties)
            self.proxy.call_sync("ClaimAccelerometer", None, Gio.DBusCallFlags.NO_AUTO_START, 5000, None)
            self.claimed_owner = owner
            self._properties()
        except Exception as exc:
            self.publish(Sample(generation=self.generation, error=str(exc)))

    def _properties(self, source=None, *_):
        if source is not None and source not in (self.proxy, self.extension):
            return
        if not self.claimed_owner or self.claimed_owner != self.proxy.get_name_owner():
            return
        if self.extension:
            prop = self.extension.get_cached_property("Snapshot")
            if prop:
                self.publish(decode(prop.unpack(), generation=self.generation))
            return
        prop = self.proxy.get_cached_property("AccelerometerOrientation")
        available = self.proxy.get_cached_property("HasAccelerometer")
        orientation = prop.unpack() if prop else "undefined"
        valid = bool(self.proxy.get_name_owner() and available and available.unpack() and orientation in ORIENTATIONS)
        self.publish(Sample(orientation=orientation, provider="standard", frame="sensorproxy-standard-v1",
                            input_owner="unknown", valid=valid, received=time.monotonic(),
                            generation=self.generation, error="" if valid else "Accelerometer unavailable"))

    def close(self):
        self.stopping = True
        if self.loop:
            self.loop.quit()
        if self.thread:
            self.thread.join(timeout=7)


def sample_once(timeout=5):
    broker = SensorBroker().start()
    try:
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            broker.ready.wait(.1)
            sample = broker.snapshot()
            if sample.fresh(time.monotonic()):
                return sample
            broker.ready.clear()
        raise RuntimeError(broker.snapshot().error or "Sensor did not provide a valid orientation")
    finally:
        broker.close()
