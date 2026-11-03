"""Read-only RandR event subscription; bounded fallback handles unavailable Xlib."""
import ctypes
import ctypes.util
import os
import select
import threading


class DisplayEvents:
    def __init__(self):
        self.changed=threading.Event();self.stopped=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True,name='display-events')

    def start(self):self.thread.start();return self
    def consume(self):
        changed=self.changed.is_set();self.changed.clear();return changed
    def close(self):self.stopped.set();self.thread.join(timeout=1)

    def _run(self):
        connection=None
        try:
            x=ctypes.CDLL(ctypes.util.find_library('X11'))
            r=ctypes.CDLL(ctypes.util.find_library('Xrandr'))
            x.XOpenDisplay.argtypes=[ctypes.c_char_p];x.XOpenDisplay.restype=ctypes.c_void_p
            x.XDefaultRootWindow.argtypes=[ctypes.c_void_p];x.XDefaultRootWindow.restype=ctypes.c_ulong
            x.XConnectionNumber.argtypes=[ctypes.c_void_p];x.XConnectionNumber.restype=ctypes.c_int
            for name in ('XFlush','XPending','XCloseDisplay'):
                getattr(x,name).argtypes=[ctypes.c_void_p]
            x.XNextEvent.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
            r.XRRSelectInput.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int]
            connection=x.XOpenDisplay(os.environ.get('DISPLAY','').encode())
            if not connection:return
            r.XRRSelectInput(connection,x.XDefaultRootWindow(connection),15)
            x.XFlush(connection);fd=x.XConnectionNumber(connection)
            event=(ctypes.c_long*24)()
            while not self.stopped.is_set():
                if select.select([fd],[],[],.2)[0]:
                    while x.XPending(connection):
                        x.XNextEvent(connection,ctypes.byref(event));self.changed.set()
        except Exception:
            return
        finally:
            if connection:x.XCloseDisplay(connection)
