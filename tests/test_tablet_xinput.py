"""Exercise the real parser across XInput's attached/disabled/floating transition."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chuwi_tablet import RecoveryStore, TabletPolicy, XInputBackend


class XInputTranscript:
    def __init__(self, base):
        self.sysroot=base/'class/input'
        self.sysroot.mkdir(parents=True)
        self.devices={
            '13':('AT Translated Set 2 keyboard','keyboard','event3','platform/i8042/serio0/input/input3',
                  {'ID_INPUT_KEYBOARD':'1','ID_BUS':'i8042','ID_PATH':'platform-i8042-serio-0'}),
            '7':('I2C Touchpad','pointer','event4','pci/i2c-3/input/input7',
                 {'ID_INPUT_TOUCHPAD':'1','ID_PATH':'pci-i2c-touchpad'}),
            '6':('Goodix Capacitive TouchScreen','pointer','event7','pci/i2c-2/input/input10',
                 {'ID_INPUT_TOUCHSCREEN':'1','ID_PATH':'pci-i2c-touchscreen'}),
            '14':('Goodix Capacitive TouchScreen','keyboard','event7','pci/i2c-2/input/input10',
                  {'ID_INPUT_TOUCHSCREEN':'1','ID_INPUT_KEYBOARD':'1','ID_PATH':'pci-i2c-touchscreen'}),
            '15':('AT Translated Set 2 keyboard','keyboard','event9','pci/usb1/input/input15',
                  {'ID_INPUT_KEYBOARD':'1','ID_BUS':'usb','ID_PATH':'usb-external-keyboard'})}
        self.enabled={xid:True for xid in self.devices}
        self.changes=[]
        for name,kind,event,path,props in self.devices.values():
            target=base/'devices'/path;target.mkdir(parents=True,exist_ok=True)
            link=self.sysroot/event/'device';link.parent.mkdir(exist_ok=True)
            if not link.exists():link.symlink_to(target)

    def run(self,args):
        if args==['xinput','--list','--short']:
            lines=[]
            for xid,(name,kind,event,path,props) in self.devices.items():
                suffix=f'slave  {kind} (2)' if self.enabled[xid] else 'floating slave'
                lines.append(f'↳ {name}\tid={xid}\t[{suffix}]')
            return '\n'.join(lines)
        if args[:2]==['xinput','list-props']:
            xid=args[2];event=self.devices[xid][2]
            return f'Device Enabled (190): {int(self.enabled[xid])}\nDevice Node (321): "/dev/input/{event}"\n'
        if args[:2] in (['xinput','disable'],['xinput','enable']):
            self.changes.append(tuple(args))
            self.enabled[args[2]]=args[1]=='enable'
            return ''
        if args[0]=='udevadm':
            node=args[-1].split('=',1)[1]
            item=next(d for d in self.devices.values() if '/dev/input/'+d[2]==node)
            return '\n'.join(key+'='+value for key,value in item[-1].items())
        raise AssertionError(args)


class FloatingDeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.transcript=XInputTranscript(self.base)
        self.backend=XInputBackend(self.transcript.run,self.transcript.sysroot)

    def test_disabled_keyboard_and_touchpad_still_have_verified_identities(self):
        targets=self.backend.preflight()
        self.assertEqual({d.xid for d in targets},{'13','7'})
        for target in targets:self.backend.change(target,False)
        floating={d.role:d for d in self.backend.devices()}
        self.assertFalse(floating['keyboard'].enabled)
        self.assertFalse(floating['touchpad'].enabled)
        self.assertTrue(floating['touchscreen'].enabled)
        for target in targets:self.backend.change(target,True)
        self.assertTrue(all(self.transcript.enabled.values()))
        self.assertTrue(all(command[-1] in ('13','7') for command in self.transcript.changes))

    def test_full_trial_restores_real_floating_transcript(self):
        self.base.joinpath('runtime').mkdir()
        store=RecoveryStore(self.base/'runtime')
        now=[0]
        policy=TabletPolicy(self.backend,store,lambda:now[0])
        policy.begin(':panel',True)
        for second in range(1,10):
            now[0]=second;policy.renew(':panel')
        now[0]=10;policy.tick()
        self.assertEqual(policy.phase,'off')
        self.assertFalse(store.path.exists())
        self.assertTrue(all(self.transcript.enabled.values()))

    def test_independent_helper_recognizes_floating_devices_without_cached_properties(self):
        self.base.joinpath('runtime').mkdir()
        store=RecoveryStore(self.base/'runtime')
        targets=self.backend.preflight();store.save(targets)
        for target in targets:self.backend.change(target,False)
        fresh=XInputBackend(self.transcript.run,self.transcript.sysroot)
        RecoveryStore(self.base/'runtime').restore(fresh)
        self.assertTrue(all(self.transcript.enabled.values()))


if __name__=='__main__':unittest.main()
