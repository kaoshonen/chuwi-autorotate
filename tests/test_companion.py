import dataclasses
import json
import math
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from chuwi import config, display, ec, hardware, policy, profiles, sensors

ROOT = Path(__file__).resolve().parents[1]


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = {'home_rotation':'right', 'orientation_offset':'normal', 'sensor_frame':'mxc6655-display-v1'}
        self.sample = sensors.Sample('normal', 'tablet', 270, (0,-1,0), 'extended', 'mxc6655-display-v1', 'application', True, 10)

    def test_all_home_anchors_and_quadrants(self):
        for home in range(4):
            for anchor in range(4):
                cfg = dict(self.config, orientation_offset=policy.ROTATIONS[(home-anchor)%4])
                for quadrant in range(4):
                    sample = dataclasses.replace(self.sample, orientation=policy.SENSORS[quadrant])
                    result = policy.decide(cfg,sample,'minibook-x-n150',now=10)
                    self.assertEqual(result.rotation,policy.ROTATIONS[(home+quadrant-anchor)%4])

    def test_laptop_precedes_orientation(self):
        result = policy.decide(self.config, dataclasses.replace(self.sample,mode='laptop'), 'minibook-x-n150',now=10)
        self.assertEqual((result.rotation,result.lock_inputs),('right',False))

    def test_n150_stock_holds_home_but_legacy_rotates(self):
        stock=dataclasses.replace(self.sample,mode='unknown',provider='standard',frame='sensorproxy-standard-v1')
        cfg=dict(self.config,sensor_frame='')
        self.assertEqual(policy.decide(cfg,stock,'minibook-x-n150',now=10).rotation,'right')
        self.assertEqual(policy.decide(cfg,stock,'minibook-legacy',now=10).rotation,'normal')
        self.assertEqual(policy.decide(cfg,stock,'minibook-x-n150',manual=True,now=10).rotation,'normal')

    def test_provider_change_requires_validation(self):
        for frame in ('', 'old-frame'):
            result=policy.decide(dict(self.config,sensor_frame=frame),self.sample,'minibook-x-n150',now=10)
            self.assertEqual(result.rotation,'right');self.assertFalse(result.lock_inputs)

    def test_manual_lock_does_not_bypass_sensor_ownership(self):
        cfg=dict(self.config,rotation_locked='true',locked_rotation='left')
        for sample in (dataclasses.replace(self.sample,input_owner='upstream'), dataclasses.replace(self.sample,valid=False)):
            result=policy.decide(cfg,sample,'minibook-x-n150',now=10)
            self.assertEqual(result.rotation,'left');self.assertFalse(result.lock_inputs)

    def test_stale_and_emergency_restore(self):
        self.assertFalse(policy.decide(self.config,self.sample,'minibook-x-n150',now=13).lock_inputs)
        result=policy.decide(self.config,self.sample,'minibook-x-n150',now=10,inhibited=True)
        self.assertEqual((result.posture,result.rotation,result.lock_inputs),('inhibited','right',False))

    def test_blacklist(self):
        self.assertIsNone(policy.decide(dict(self.config,blacklist_rotations='normal,left'),self.sample,'minibook-x-n150',now=10).rotation)

    def test_external_rotation_conflict_and_bounded_history(self):
        c=policy.RotationConflict()
        for stamp in (0,5): self.assertFalse(c.observe(True,stamp))
        self.assertTrue(c.observe(True,10))
        c=policy.RotationConflict()
        for stamp in (0,20,40): self.assertFalse(c.observe(True,stamp))


class SensorTests(unittest.TestCase):
    def payload(self):
        return dict(version=1, sample_monotonic_us=10_000_000, display_gravity=[.4,-.8,.2],hinge_valid=True,
                    hinge_degrees=240.,mode='tablet',orientation='normal',frame='test',input_owner='application',valid=True)

    def test_real_gravity_is_preserved(self):
        self.assertEqual(sensors.decode(self.payload(),10).gravity,(.4,-.8,.2))

    def test_invalid_geometry_removes_posture(self):
        data=self.payload();data['hinge_valid']=False
        self.assertEqual(sensors.decode(data,10).mode,'unknown')

    def test_invalid_and_stale_snapshots(self):
        for key,value in [('version',2),('sample_monotonic_us',6_000_000),('sample_monotonic_us',11_000_000),
                          ('display_gravity',[float('nan'),0,0]),('display_gravity',[0,0]),('hinge_degrees',400),('orientation','right')]:
            with self.subTest(key=key,value=value):
                data=self.payload();data[key]=value
                self.assertFalse(sensors.decode(data,10).valid)

    def test_old_proxy_notifications_discarded(self):
        broker=sensors.SensorBroker(); broker.proxy=object(); broker.extension=object()
        broker._properties(object())
        self.assertFalse(broker.snapshot().valid)

    def test_sleep_invalidates(self):
        broker=sensors.SensorBroker();broker.publish(sensors.decode(self.payload(),10));broker._sleep()
        self.assertFalse(broker.snapshot().valid);self.assertEqual(broker.snapshot().generation,1)


class TransformTests(unittest.TestCase):
    def screen(self, **kwargs):
        return dict(name='DSI-1',internal=True,active=True,refresh_hz=50.0,rotation='normal',geometry=(1200,1920,1920,0),transform=display.IDENTITY,reflection=(False,False),**kwargs)

    def test_corners_center_viewport_and_saved_calibration(self):
        d=self.screen(); matrix=display.touch_mapping(d,(3120,1920))
        self.assertAlmostEqual(matrix[2],1920/3120);self.assertAlmostEqual(matrix[0],1200/3120)
        d['rotation']='right';d['geometry']=(1920,1200,0,0)
        self.assertEqual(display.touch_mapping(d,(1920,1200)),display.ROTATION['right'])
        calibration=(.9,0,.05,0,.9,.05,0,0,1)
        mapping=display.touch_mapping(d,(1920,1200),calibration)
        recovered=display.multiply(display.inverse(display.ROTATION['right']),mapping)
        for a,b in zip(recovered,calibration):self.assertAlmostEqual(a,b)

    def test_affine_reflection_preserved(self):
        d=self.screen();d['transform']=(.5,0,3,0,.75,5,0,0,1);d['reflection']=(True,False)
        matrix=display.touch_mapping(d,(3120,1920))
        self.assertAlmostEqual(matrix[0],-600/3120)
        self.assertAlmostEqual(matrix[2],(1920+603)/3120)
        self.assertEqual(display.rotation_command(d,'right'),['xrandr','--output','DSI-1','--rotate','right'])

    def test_singular_perspective_and_external_refused(self):
        for matrix in ((0,0,0,0,0,0,0,0,1),(1,0,0,0,1,0,.1,0,1)):
            d=self.screen();d['transform']=matrix
            with self.assertRaises(ValueError):display.rotation_command(d,'right')
        engine=runpy.run_path(str(ROOT/'chuwi-autorotate'))
        with patch.dict(os.environ,XDG_SESSION_TYPE='wayland'):
            with self.assertRaises(engine['AutorotateError']):engine['apply_rotation']('normal',{})
        d=self.screen();d['refresh_hz']=None
        with self.assertRaises(ValueError):display.rotation_command(d,'normal')
        d=self.screen();d['internal']=False
        with self.assertRaises(ValueError):display.rotation_command(d,'normal')

    def test_mode_and_panel_metadata_are_distinct(self):
        text='DSI-1 connected primary 1920x1200+0+0 (0x4a) right (normal left inverted right x axis y axis)\n\tTransform: 1 0 0\n\t 0 1 0\n\t 0 0 1\n\tpanel orientation: Normal\n  1200x1920 50.00*+\nHDMI-1 disconnected (normal left inverted right x axis y axis)\n'
        d=display.parse_verbose(text)[0]
        self.assertEqual(d['rotation'],'right');self.assertEqual(d['panel_orientation'],'Normal');self.assertEqual(d['refresh_hz'],50)
        self.assertEqual(d['reflection'],(False,False))


class FakeEC:
    def __init__(self):self.current=(True,False);self.failure=False;self.calls=[]
    def compatible(self):return True
    def states(self):return self.current
    def set_inputs(self,k,t):
        self.calls.append((k,t))
        if self.failure:raise RuntimeError('device disappeared')
        self.current=(k,t)


class ECTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'recovery.json';self.device=FakeEC();self.now=0
        self.lease=ec.Lease(self.device,self.path,lambda:self.now)

    def test_snapshot_precedes_coupled_write_and_expiry_restores_both(self):
        self.lease.acquire(':1.1',1000);self.assertEqual(json.loads(self.path.read_text())['touchpad'],False)
        self.lease.set(':1.1',False,False);self.now=6;self.lease.tick()
        self.assertEqual(self.device.current,(True,False));self.assertFalse(self.path.exists())

    def test_partial_failure_retains_journal_and_inhibits_reentry(self):
        self.lease.acquire(':1.1',1000);self.device.failure=True
        with self.assertRaises(RuntimeError):self.lease.set(':1.1',False,False)
        self.assertTrue(self.path.exists())
        with self.assertRaises(RuntimeError):self.lease.acquire(':1.2',1000)
        self.device.failure=False;self.lease.restore();self.assertFalse(self.path.exists())

    def test_foreign_owner_cannot_write_or_renew(self):
        self.lease.acquire(':1.1',1000)
        with self.assertRaises(RuntimeError):self.lease.set(':1.2',False,False)
        with self.assertRaises(RuntimeError):self.lease.renew(':1.2')
        self.assertEqual(self.device.calls,[])

    def test_helper_crash_recovered_by_independent_instance(self):
        self.lease.acquire(':1.1',1000);self.lease.set(':1.1',False,False)
        ec.Lease(self.device,self.path).restore()
        self.assertEqual(self.device.current,(True,False))

    def test_ec_input_order_and_readback(self):
        adapter=ec.EC(Path(self.tmp.name));adapter.base.mkdir(parents=True)
        for name in ('keyboard_enabled','touchpad_enabled'):(adapter.base/name).write_text('1')
        adapter.set_inputs(False,True);self.assertEqual(adapter.states(),(False,True))

    def test_missing_approval_denies(self):
        self.assertFalse(ec.EC(Path(self.tmp.name)).compatible())


class InventoryAndInstallTests(unittest.TestCase):
    def test_missing_optional_sensors_nonfatal(self):
        with tempfile.TemporaryDirectory() as d:
            result=hardware.Hardware(Path(d)).snapshot()
            self.assertEqual(result['thermals']['fan']['status'],'Missing')
            self.assertIsNone(result['input']['backlight']['value'])

    def test_permission_denied_distinguished(self):
        with patch.object(Path,'read_text',side_effect=PermissionError):
            result=hardware.attribute(Path('/example'))
        self.assertEqual(result['status'],'Requires attention');self.assertEqual(result['detail'],'Permission denied')

    def test_config_migration_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config';original=b'# user setting\nhome_rotation=right\norientation_offset=normal\nrotation_threshold_degrees=65\n'
            p.write_bytes(original);backup=config.migrate(p)
            self.assertEqual(Path(backup).read_bytes(),original)
            self.assertTrue(p.read_bytes().startswith(original));self.assertIsNone(config.migrate(p))

    def test_newer_config_refused_without_change(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config';p.write_text('config_version=99\n')
            with self.assertRaises(ValueError):config.migrate(p)
            self.assertEqual(p.read_text(),'config_version=99\n')

    def test_audit_and_install_dry_run_do_not_create_user_files(self):
        with tempfile.TemporaryDirectory() as d:
            env={**os.environ,'HOME':d,'XDG_CONFIG_HOME':d+'/config','XDG_STATE_HOME':d+'/state','XDG_RUNTIME_DIR':d+'/run','DISPLAY':'','XDG_SESSION_TYPE':'wayland'}
            for args in (['python3',str(ROOT/'chuwi-autorotate'),'audit','--json'],['bash',str(ROOT/'install.sh'),'--dry-run']):
                result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue(json.loads(result.stdout)['read_only'])
                self.assertEqual(list(Path(d).iterdir()),[])

    def test_prohibited_writes_not_in_runtime(self):
        for file in (ROOT/'chuwi-autorotate',*list((ROOT/'chuwi').glob('*.py'))):
            source=file.read_text()
            for token in ('--newmode','--addmode','i915.vbt_firmware','--rate','--refresh'):
                self.assertNotIn(token,source)

class RecoveryWorkflowTests(unittest.TestCase):
    def test_sleep_failure_and_independent_restore(self):
        from chuwi.sleep import SleepTrial
        with tempfile.TemporaryDirectory() as d:
            mode=Path(d)/'mem_sleep';journal=Path(d)/'journal'
            mode.write_text('[s2idle] deep')
            class SimulatedSleepTrial(SleepTrial):
                def select(self, selected):self.mode_path.write_text('['+selected+']')
            trial=SimulatedSleepTrial(mode,journal);trial.prepare()
            self.assertEqual(mode.read_text(),'[deep]')
            SimulatedSleepTrial(mode,journal).restore()
            self.assertEqual(mode.read_text(),'[s2idle]');self.assertFalse(journal.exists())

    def test_sleep_missing_deep_does_not_write(self):
        from chuwi.sleep import SleepTrial
        with tempfile.TemporaryDirectory() as d:
            mode=Path(d)/'mem_sleep';mode.write_text('[s2idle]')
            with self.assertRaises(RuntimeError):SleepTrial(mode,Path(d)/'journal').prepare()
            self.assertEqual(mode.read_text(),'[s2idle]')

    def test_osk_compare_before_restore(self):
        from chuwi.osk import KeyboardPreferences
        class Settings:
            current=False
            def get_boolean(self,key):return self.current
            def set_boolean(self,key,value):self.current=value;return True
        with tempfile.TemporaryDirectory() as d:
            state=Settings();manager=KeyboardPreferences(lambda _:state,Path(d)/'keyboard')
            manager.enable('cinnamon');self.assertTrue(state.current)
            state.current=False;manager.restore();self.assertFalse(state.current)
            state.current=True;manager.enable('cinnamon');manager.restore();self.assertTrue(state.current)

    def test_desktop_adapters_and_external_identity_exclusion(self):
        from chuwi.inputs import internal
        self.assertEqual(profiles.settings_command('xfce','display'),['xfce4-display-settings'])
        self.assertEqual(profiles.LOCKERS['cinnamon'][0][0],'org.cinnamon.ScreenSaver')
        for path in ('/sys/devices/pci/usb1/input','/sys/devices/virtual/input','/sys/devices/bluetooth/input'):
            self.assertFalse(internal({'ID_PATH':'p','ID_INPUT_KEYBOARD':'1'},path,'keyboard'))

    def test_install_restores_package_directory(self):
        restore=runpy.run_path(str(ROOT/'restore-install.py'))['restore']
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)/'home';backup=Path(d)/'backup';backup.mkdir()
            original=backup/'0';original.mkdir();(original/'old.py').write_text('original')
            current=home/'.local/bin/chuwi';current.mkdir(parents=True);(current/'new.py').write_text('new')
            (backup/'manifest.json').write_text(json.dumps({'files':[{'path':'.local/bin/chuwi','existed':True,'backup':'0'}]}))
            restore(backup,home,systemctl=False)
            self.assertEqual((current/'old.py').read_text(),'original');self.assertFalse((current/'new.py').exists())

if __name__=='__main__':unittest.main()
