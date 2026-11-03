"""Dedicated trackpad worker; never shares the rotation or tablet recovery executor."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading


class Client:
    def __init__(self, dispatch, helper=None):
        self.dispatch = dispatch
        self.helper = Path(helper or Path(__file__).resolve().parents[1] / "chuwi-trackpad")
        self.jobs = queue.Queue()
        self.closed = False
        self.cancelled = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()
        threading.Thread(target=self._worker, name="chuwi-trackpad", daemon=True).start()

    def submit(self, action, callback, *args):
        if action == "measure":
            self.cancelled.clear()
        self.jobs.put((action, callback, args))

    def _send(self, callback, event):
        def deliver():
            if not self.closed:
                callback(event)
            return False
        self.dispatch(deliver)

    def _worker(self):
        while True:
            job = self.jobs.get()
            if job is None or self.closed:
                return
            action, callback, args = job
            try:
                command = ["/usr/bin/python3", "-I", str(self.helper), action, *map(str, args)]
                if action != "status":
                    command.insert(0, "/usr/bin/pkexec")
                if action == "measure":
                    self._measure(command, callback)
                else:
                    # Mutating helpers complete their bounded transaction/rollback, even if the window closes.
                    result = subprocess.run(command, capture_output=True, text=True,
                                            timeout=30 if action == "status" else None)
                    if result.returncode in (126, 127):
                        self._send(callback, {"type": "cancelled", "message": "Authentication cancelled or unavailable; settings unchanged."})
                    elif result.stdout.strip():
                        self._send(callback, json.loads(result.stdout))
                    else:
                        raise RuntimeError(result.stderr.strip() or "Trackpad helper returned no result.")
            except Exception as exc:
                self._send(callback, {"type": "error", "message": str(exc)})

    def _measure(self, command, callback):
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1)
        with self.process_lock:
            self.process = process
            os.set_blocking(process.stdin.fileno(), False)
        # Drain stderr independently so authentication diagnostics cannot fill a pipe.
        errors = []
        def drain_errors():
            for line in process.stderr:
                if len(errors) < 20:
                    errors.append(line)
        reader = threading.Thread(target=drain_errors, daemon=True)
        reader.start()
        terminal = False
        try:
            if self.closed or self.cancelled.is_set():
                self.cancel()
            for line in process.stdout:
                event = json.loads(line)
                terminal |= event.get("type") in ("result", "error", "cancelled")
                self._send(callback, event)
            code = process.wait()
            reader.join(timeout=1)
            if not terminal:
                message = "Measurement cancelled; settings unchanged." if self.cancelled.is_set() or code in (126, 127) else "".join(errors).strip() or "Measurement helper disconnected."
                self._send(callback, {"type": "cancelled" if self.cancelled.is_set() or code in (126, 127) else "error", "message": message})
        finally:
            with self.process_lock:
                self.process = None
                if not process.stdin.closed:
                    process.stdin.close()
            process.stdout.close()
            # EOF on stdin ends capture even if the UI disappears. Never grab the device.
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()  # Read-only helper/authentication only.
                except OSError:
                    pass  # An elevated helper still observes EOF and its session deadline.
            if process.poll() is not None:
                process.stderr.close()

    def ready(self, region):
        self._command({"command": "ready", "region": region})

    def _command(self, message):
        with self.process_lock:
            process = self.process
            if process and not process.stdin.closed:
                try:
                    os.write(process.stdin.fileno(), (json.dumps(message) + "\n").encode())
                except (OSError, ValueError):
                    pass

    def cancel(self):
        self.cancelled.set()
        with self.process_lock:
            process = self.process
            if process and not process.stdin.closed:
                process.stdin.close()  # EOF also works while the auth prompt is still open.
                # pkexec may still be waiting for authentication. It is safe to terminate a read-only operation.
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass

    def close(self):
        self.closed = True
        self.cancel()
        self.jobs.put(None)
