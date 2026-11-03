import importlib.machinery
import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gi.repository import GLib
from chuwi_tablet import TabletError

loader=importlib.machinery.SourceFileLoader('tablet_guard_test',str(Path(__file__).resolve().parents[1]/'chuwi-tablet-guard'))
spec=importlib.util.spec_from_loader('tablet_guard_test',loader)
guard=importlib.util.module_from_spec(spec)
loader.exec_module(guard)


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.guard=guard.Guardian.__new__(guard.Guardian)
        self.guard.policy=Mock()
        self.guard.policy.status.return_value={'phase':'off','reason':'ready'}
        self.guard.connection=Mock()
        self.guard.loop=Mock()
        self.guard.armed=True
        self.guard.sleeping=False
        self.guard.last_status=None

    def test_screen_lock_restores_but_unlock_event_does_not_lock(self):
        self.guard.screen_changed(None,None,None,None,None,GLib.Variant('(b)',(False,)))
        self.guard.policy.unlock.assert_not_called()
        self.guard.screen_changed(None,None,None,None,None,GLib.Variant('(b)',(True,)))
        self.guard.policy.unlock.assert_called_once()

    def test_suspend_and_resume_both_restore_input(self):
        for sleeping in (True,False):
            self.guard.sleep_changed(None,None,None,None,None,GLib.Variant('(b)',(sleeping,)))
        self.assertEqual(self.guard.policy.unlock.call_count,2)

    def test_owner_disconnect_is_forwarded_to_policy(self):
        self.guard.name_changed(None,None,None,None,None,GLib.Variant('(sss)',(':panel',':panel','')))
        self.guard.policy.owner_lost.assert_called_once_with(':panel')

    def test_lost_screensaver_monitor_stops_and_restores(self):
        self.guard.name_changed(None,None,None,None,None,GLib.Variant('(sss)',(guard.SCREENSAVER,':old','')))
        self.assertFalse(self.guard.armed)
        self.guard.policy.unlock.assert_called_once()
        self.guard.loop.quit.assert_called_once()

    def test_watchdog_pings_only_after_policy_tick(self):
        events=[]
        self.guard.policy.tick.side_effect=lambda:events.append('tick')
        with patch.object(guard,'notify',side_effect=lambda _:events.append('watchdog')):
            self.guard.tick()
        self.assertEqual(events,['tick','watchdog'])

    def test_missing_watchdog_prevents_arming(self):
        with patch.dict(os.environ,{'XDG_SESSION_TYPE':'x11','WATCHDOG_USEC':'0'}):
            self.guard.name_acquired(None,None)
        self.guard.policy.unlock.assert_not_called()
        self.guard.loop.quit.assert_called_once()

    def test_input_method_failure_is_a_dbus_error(self):
        self.guard.policy.begin.side_effect=TabletError('Recovery unavailable')
        self.guard.screen_locked=Mock(return_value=False)
        invocation=Mock()
        self.guard.method(None,':panel',None,None,'BeginTest',None,invocation)
        invocation.return_dbus_error.assert_called_once()
        invocation.return_value.assert_not_called()

    def test_notify_uses_systemd_datagram_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'notify')
            with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as receiver:
                receiver.bind(path);receiver.settimeout(1)
                with patch.dict(os.environ,{'NOTIFY_SOCKET':path}):guard.notify('WATCHDOG=1')
                self.assertEqual(receiver.recv(128),b'WATCHDOG=1')


if __name__=='__main__':unittest.main()
