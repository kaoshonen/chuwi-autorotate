"""Run the real installer in a disposable HOME with all system tools stubbed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
STUB='''#!/usr/bin/python3
import json,os,pathlib,subprocess,sys
name=pathlib.Path(sys.argv[0]).name
with open(os.environ['COMMAND_LOG'],'a') as log:log.write(json.dumps([name,*sys.argv[1:]])+'\\n')
if name=='install':
 if os.environ.get('FAIL_INSTALL') and any(arg.endswith('.svg') for arg in sys.argv):sys.exit(1)
 sys.exit(subprocess.call(['/usr/bin/install',*sys.argv[1:]]))
if name=='systemctl':
 if 'is-active' in sys.argv:print('inactive');sys.exit(3)
 if 'is-enabled' in sys.argv:print('disabled');sys.exit(1)
 if 'show' in sys.argv:print('ActiveState=inactive')
if name=='xrandr':print('Screen 0: current 1920 x 1200\\nDSI-1 connected 1920x1200+0+0 right (normal left inverted right x axis y axis)\\n  1200x1920 50.00*')
'''

class InstallerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.home=self.base/'home';self.home.mkdir()
        self.bin=self.base/'bin';self.bin.mkdir();runtime=self.base/'runtime';runtime.mkdir(mode=0o700)
        for name in ('systemctl','xrandr','xinput','udevadm','mokutil','dpkg-query','apt-cache','journalctl','ps','dkms','install','update-desktop-database','gtk-update-icon-cache'):
            path=self.bin/name;path.write_text(STUB);path.chmod(0o755)
        self.env={**os.environ,'HOME':str(self.home),'PATH':str(self.bin)+':/usr/bin:/bin',
                  'XDG_RUNTIME_DIR':str(runtime),'XDG_SESSION_TYPE':'x11','DISPLAY':':stub',
                  'COMMAND_LOG':str(self.base/'commands')}

    def run_script(self,script,**extra):
        return subprocess.run(['bash',str(ROOT/script)],env={**self.env,**extra},capture_output=True,text=True,timeout=45)

    def test_fresh_install_uninstall_and_preserved_configuration(self):
        result=self.run_script('install.sh');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        config=self.home/'.config/chuwi-autorotate/config.conf'
        self.assertIn('enabled=false',config.read_text())
        self.assertTrue((self.home/'.local/bin/chuwi/sensors.py').exists())
        self.assertTrue((self.home/'.local/bin/chuwi-trackpad').exists())
        self.assertTrue((self.home/'.local/bin/chuwi/trackpad_session.py').exists())
        self.assertFalse((self.home/'.local/bin/chuwi-trackpad-preview').exists())
        result=self.run_script('uninstall.sh');self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(config.exists());self.assertFalse((self.home/'.local/bin/chuwi').exists())
        self.assertFalse((self.home/'.local/bin/chuwi-trackpad').exists())
        commands=[json.loads(line) for line in (self.base/'commands').read_text().splitlines()]
        self.assertFalse(any(command[0]=='xrandr' and '--rotate' in command for command in commands))
        self.assertFalse(any(command[0]=='systemctl' and 'start' in command for command in commands))

    def test_interrupted_upgrade_restores_previous_files_and_calibration(self):
        result=self.run_script('install.sh');self.assertEqual(result.returncode,0,result.stderr)
        engine=self.home/'.local/bin/chuwi-autorotate';previous=engine.read_bytes()+b'\n# previous installation marker\n';engine.write_bytes(previous)
        trackpad=self.home/'.local/bin/chuwi-trackpad';old_trackpad=trackpad.read_bytes()+b'\n# old helper\n';trackpad.write_bytes(old_trackpad)
        config=self.home/'.config/chuwi-autorotate/config.conf';original=b'home_rotation=right\norientation_offset=normal\nrotation_threshold_degrees=65\nenabled=false\n';config.write_bytes(original)
        result=self.run_script('install.sh',FAIL_INSTALL='1')
        self.assertNotEqual(result.returncode,0);self.assertIn('restoring installation snapshot',result.stderr)
        self.assertEqual(engine.read_bytes(),previous);self.assertEqual(config.read_bytes(),original)
        self.assertEqual(trackpad.read_bytes(),old_trackpad)
