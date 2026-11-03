import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]

class RootHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.Service=runpy.run_path(str(ROOT/'optional/chuwi-hardware-helper'))['Service']

    def test_inactive_remote_wayland_and_locked_callers_denied(self):
        for change in ({'Active':False},{'Remote':True},{'Type':'wayland'},{'LockedHint':True}):
            service=self.Service.__new__(self.Service)
            properties={'Active':True,'Remote':False,'Type':'x11','LockedHint':False,**change}
            service.call=Mock(side_effect=[(123,),('/session',),(properties,)])
            with self.assertRaises(RuntimeError):service.active(':1.1')

    def test_denied_setinputs_does_not_write(self):
        service=self.Service.__new__(self.Service);service.lease=Mock()
        service.active=Mock(side_effect=RuntimeError('inactive caller'))
        invocation=Mock();parameters=Mock();parameters.unpack.return_value=(False,False)
        service.method(None,':1.1',None,None,'SetInputs',parameters,invocation)
        service.lease.set.assert_not_called();invocation.return_dbus_error.assert_called_once()

    def test_suspend_and_session_loss_restore_independently(self):
        service=self.Service.__new__(self.Service);service.lease=Mock();service.lease.owner=':1.1'
        service.sleep_changed();service.lease.restore.assert_called_once()
        service.lease.reset_mock();service.active=Mock(side_effect=RuntimeError('locked'))
        service.session_changed();service.lease.restore.assert_called_once()

    def test_acquire_requires_prior_authorization(self):
        service=self.Service.__new__(self.Service);service.lease=Mock();service.active=Mock()
        service.uid=Mock(return_value=1000);service.authorized_users={}
        with self.assertRaises(RuntimeError):service.acquire(':1.1')
        service.lease.acquire.assert_not_called()


class BindingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.namespace=runpy.run_path(str(ROOT/'optional/chuwi-sensor-bindings'))
        self.restore=self.namespace['restore'];self.state=root/'bindings.json'
        self.devices=root/'devices';self.devices.mkdir();self.driver=root/'mxc4005';self.driver.mkdir()
        (self.driver/'bind').touch()
        self.patch=patch.dict(self.restore.__globals__,STATE=self.state,RUNTIME=root/'buses',DEVICES=self.devices,DRIVER=self.driver,
                              trusted=lambda p:json.loads(p.read_text()))
        self.patch.start();self.addCleanup(self.patch.stop)
        device=self.devices/'i2c-MDA6655:00';device.mkdir();(device/'modalias').write_text('acpi:MDA6655:')
        self.device=device
        self.state.write_text(json.dumps({'version':1,'bound':[device.name]}))

    def test_verified_original_binding_clears_journal(self):
        (self.device/'driver').symlink_to(self.driver,target_is_directory=True)
        self.restore();self.assertFalse(self.state.exists())

    def test_failed_binding_readback_retains_journal(self):
        with self.assertRaises(FileNotFoundError):self.restore()
        self.assertTrue(self.state.exists())

    def test_unrelated_device_is_never_rebound(self):
        self.state.write_text(json.dumps({'version':1,'bound':['some-touchpad']}))
        with self.assertRaises(RuntimeError):self.restore()
        self.assertEqual((self.driver/'bind').read_text(),'');self.assertTrue(self.state.exists())
