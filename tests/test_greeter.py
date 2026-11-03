"""Login hooks must select physical touch devices and never prevent login."""
import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

loader=importlib.machinery.SourceFileLoader('greeter_engine',str(Path(__file__).resolve().parents[1]/'chuwi-autorotate'))
spec=importlib.util.spec_from_loader('greeter_engine',loader)
engine=importlib.util.module_from_spec(spec);sys.modules[spec.name]=engine;loader.exec_module(engine)
runtime_text=engine.GREETER_SCRIPT_TEXT.split("<<'CHUWI_GREETER_PY'\n",1)[1].split('\nCHUWI_GREETER_PY',1)[0]

def runtime():
    namespace={'__name__':'greeter_runtime_test'}
    exec(compile(runtime_text,'greeter-runtime','exec'),namespace)
    return namespace


class GreeterRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.ns=runtime();self.calls=[];self.recycled=False;self.reads=0
        self.ns['SYS_INPUT']=self.base/'class/input'
        self.nodes={
            '6':('event7','pci/i2c-2/input/input10','ID_INPUT_TOUCHSCREEN=1\nID_PATH=i2c-screen'),
            '7':('event4','pci/i2c-3/input/input7','ID_INPUT_TOUCHPAD=1\nID_PATH=i2c-pad'),
            '15':('event9','usb1/input/input15','ID_INPUT_TOUCHSCREEN=1\nID_PATH=usb-screen'),
            '16':('event10','usb2/input/input16','ID_INPUT_MOUSE=1\nID_PATH=usb-mouse')}
        for event,path,props in self.nodes.values():
            target=self.base/'devices'/path;target.mkdir(parents=True)
            link=self.ns['SYS_INPUT']/event/'device';link.parent.mkdir(parents=True);link.symlink_to(target)
        self.layout='Screen 0: current 1920 x 1200\nDSI-1 connected primary 1920x1200+0+0 right (normal left inverted right)'
        self.input='\n'.join(('Goodix id=6 [slave  pointer (2)]','Touchpad id=7 [slave pointer (2)]',
                              'Goodix id=14 [slave keyboard (3)]','External touch id=15 [slave pointer (2)]','Mouse id=16 [slave pointer (2)]'))
        self.ns['capture']=self.capture

    def capture(self,args):
        self.calls.append(args)
        if args==['/usr/bin/xrandr','--query']:return self.layout
        if args[:3]==['/usr/bin/xrandr','--output','DSI-1']:return ''
        if args==['/usr/bin/xinput','--list','--short']:return self.input
        if args[:2]==['/usr/bin/xinput','list-props']:
            xid=args[2]
            if xid=='6':self.reads+=1
            event=self.nodes['15' if xid=='6' and self.recycled and self.reads>1 else xid][0]
            return f'Device Enabled (140): 1\nDevice Node (322): "/dev/input/{event}"\nCoordinate Transformation Matrix (141): 1,0,0,0,1,0,0,0,1\n'
        if args[0]=='/usr/bin/udevadm':
            event=args[-1].split('/')[-1]
            return next(d[2] for d in self.nodes.values() if d[0]==event)
        if args[:2]==['/usr/bin/xinput','set-prop']:return ''
        raise AssertionError(args)

    def test_only_verified_internal_touchscreen_pointer_is_mapped(self):
        self.ns['apply']({'display':'DSI-1','rotation':'right'})
        writes=[c for c in self.calls if c[:2]==['/usr/bin/xinput','set-prop']]
        self.assertEqual(len(writes),1)
        self.assertEqual(writes[0][2],'6')
        self.assertEqual(tuple(writes[0][4:]),tuple(f'{float(v):.8f}' for v in engine.TOUCH_MATRICES['right']))

    def test_recycled_id_is_not_used_for_replacement_external_input(self):
        self.recycled=True
        self.ns['apply']({'display':'DSI-1','rotation':'right'})
        self.assertFalse(any(c[:2]==['/usr/bin/xinput','set-prop'] for c in self.calls))

    def test_no_external_display_fallback_when_internal_panel_is_missing(self):
        self.layout='Screen 0: current 1920 x 1080\nHDMI-1 connected primary 1920x1080+0+0'
        for display in ('auto','DSI-1','HDMI-1'):
            self.calls=[];self.ns['apply']({'display':display,'rotation':'right'})
            self.assertEqual(self.calls,[['/usr/bin/xrandr','--query']])

    def test_matrix_accounts_for_external_monitor_viewport(self):
        layout='Screen 0: current 3840 x 1200\nDSI-1 connected 1920x1200+1920+0 right'
        for rotation in engine.ROTATIONS:
            expected=engine.viewport_touch_matrix(rotation,layout.splitlines()[1],3840,1200)
            self.assertEqual(self.ns['touch_matrix'](layout,'DSI-1',rotation),expected)

    def test_touch_disabled_config_changes_no_input_properties(self):
        self.ns['apply']({'display':'DSI-1','rotation':'right','touch_enabled':'false'})
        self.assertFalse(any(c[0]=='/usr/bin/xinput' for c in self.calls))

    def test_config_is_parsed_as_data_without_shell_evaluation(self):
        config=Mock();config.is_symlink.return_value=False
        config.stat.return_value=SimpleNamespace(st_uid=0,st_mode=0o100644)
        config.read_text.return_value="display='$(touch /tmp/not-a-command)'\nrotation=right\ntouch_enabled=true\n"
        self.ns['CONFIG']=config
        values=self.ns['read_config']()
        self.assertEqual(values['display'],'$(touch /tmp/not-a-command)')
        self.ns['apply'](values)
        self.assertEqual(self.calls,[['/usr/bin/xrandr','--query']])

    def test_writable_or_non_root_config_is_refused(self):
        config=Mock();config.is_symlink.return_value=False;self.ns['CONFIG']=config
        for uid,mode in ((1000,0o100644),(0,0o100666)):
            config.stat.return_value=SimpleNamespace(st_uid=uid,st_mode=mode)
            with self.assertRaises(RuntimeError):self.ns['read_config']()

    def test_wrapper_exits_successfully_after_runtime_failure_or_hang(self):
        for code in ('raise RuntimeError("test failure")','import time;time.sleep(5)'):
            script=engine.GREETER_SCRIPT_TEXT.replace(runtime_text,code).replace('8s','.1s')
            result=subprocess.run(['/bin/sh'],input=script,text=True,capture_output=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)


class GreeterInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.paths={'GREETER_SCRIPT_PATH':root/'bin/greeter','GREETER_CONFIG_PATH':root/'etc/greeter.conf',
                    'LIGHTDM_CONF_PATH':root/'lightdm/80-chuwi.conf','GREETER_ORIGINALS_PATH':root/'etc/originals.json',
                    'GREETER_BACKUP_ROOT':root/'backups'}
        self.patch=patch.multiple(engine,**self.paths);self.patch.start();self.addCleanup(self.patch.stop)
        self.conf=f'[Seat:*]\ndisplay-setup-script={engine.GREETER_SCRIPT_PATH}\ngreeter-setup-script={engine.GREETER_SCRIPT_PATH}\n'
        self.confpatch=patch.object(engine,'LIGHTDM_CONF_TEXT',self.conf);self.confpatch.start();self.addCleanup(self.confpatch.stop)
        self.manager=Mock();self.manager.exists.return_value=True;self.manager.read_text.return_value='/usr/sbin/lightdm'
        def path(value):
            if str(value)=='/etc/X11/default-display-manager':return self.manager
            if str(value) in ('/usr/bin/timeout','/usr/bin/python3','/usr/bin/xrandr','/usr/bin/xinput','/usr/bin/udevadm','/usr/sbin/lightdm'):
                return SimpleNamespace(is_file=lambda:True,name=Path(value).name)
            return Path(value)
        self.pathpatch=patch.object(engine,'Path',side_effect=path);self.pathpatch.start();self.addCleanup(self.pathpatch.stop)

    def apply(self,results=None):
        results=results or [subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,self.conf,'')]
        with patch.object(engine,'running_as_root',return_value=True),patch.object(engine,'run_capture',side_effect=results):
            return engine.install_greeter_files('DSI-1','right',True)

    def test_install_records_absence_and_restores_original_files(self):
        script=engine.GREETER_SCRIPT_PATH;script.parent.mkdir(mode=0o755);script.write_text('original helper');script.chmod(0o755)
        backup=self.apply()
        self.assertEqual((backup/'manifest.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(backup.stat().st_mode & 0o777,0o700)
        self.assertEqual(engine.GREETER_SCRIPT_PATH.stat().st_mode & 0o777,0o755)
        engine.restore_greeter_files(backup)
        self.assertEqual(script.read_text(),'original helper')
        self.assertFalse(engine.GREETER_CONFIG_PATH.exists())
        self.assertFalse(engine.LIGHTDM_CONF_PATH.exists())
        self.assertFalse(engine.GREETER_ORIGINALS_PATH.exists())

    def test_invalid_new_config_rolls_back_all_files(self):
        with self.assertRaises(engine.AutorotateError):
            self.apply([subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],1,'','invalid config')])
        self.assertTrue(all(not p.exists() for p in engine.greeter_paths()))

    def test_foreign_hook_is_preserved_without_creating_installation(self):
        with self.assertRaises(engine.AutorotateError):
            self.apply([subprocess.CompletedProcess([],0,'greeter-setup-script=/some/other/helper','')])
        self.assertFalse(engine.GREETER_BACKUP_ROOT.exists())
        self.assertTrue(all(not p.exists() for p in engine.greeter_paths()))

    def test_incomplete_backup_is_rejected_before_any_files_change(self):
        backup=self.apply();manifest=backup/'manifest.json'
        record=json.loads(manifest.read_text());record['files'][0]['existed']=True
        manifest.write_text(json.dumps(record))
        before=engine.LIGHTDM_CONF_PATH.read_bytes()
        with self.assertRaises(engine.AutorotateError):engine.restore_greeter_files(backup)
        self.assertEqual(engine.LIGHTDM_CONF_PATH.read_bytes(),before)

    def test_update_keeps_original_uninstall_baseline(self):
        first=self.apply();self.apply([subprocess.CompletedProcess([],0,self.conf,''),subprocess.CompletedProcess([],0,self.conf,'')])
        self.assertEqual(json.loads(engine.GREETER_ORIGINALS_PATH.read_text())['backup'],str(first))
        with patch.object(engine,'running_as_root',return_value=True):engine.command_greeter_uninstall(None)
        self.assertTrue(all(not p.exists() for p in engine.greeter_paths()))

    def test_external_or_invalid_connector_is_refused(self):
        for name in ('HDMI-1','--help','DSI-1;touch /tmp/unsafe'):
            with self.assertRaises(engine.AutorotateError):engine.resolve_greeter_display({},name)


if __name__=='__main__':unittest.main()
