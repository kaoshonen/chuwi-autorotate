"""Explicit identity enrollment for otherwise ambiguous internal HID devices."""
import json
import os
from pathlib import Path
from .config import atomic


def enrollment_path():
    return Path.home()/'.config/chuwi-autorotate/input-identities.json'


def enrolled():
    path=enrollment_path()
    if not path.exists():return {}
    if path.is_symlink() or path.stat().st_uid != os.getuid():raise RuntimeError('Unsafe input enrollment file')
    data=json.loads(path.read_text())
    if data.get('version') != 1:raise RuntimeError('Unknown input enrollment version')
    return data['devices']


def internal(properties, syspath, role):
    path=syspath.lower()
    if any(part in path for part in ('/usb','/bluetooth','/virtual/')):return False
    return ('/devices/' in path and ('/i2c-' in path or '/platform/' in path)
            and properties.get('ID_INPUT_'+role.upper()) == '1' and bool(properties.get('ID_PATH')))


def enrolled_role(properties, syspath, kind):
    for role,item in enrolled().items():
        if role not in ('keyboard','touchpad','touchscreen'):continue
        if internal(properties,syspath,role) and item == {'identity':properties.get('ID_PATH'), 'syspath':syspath}:
            if (role == 'keyboard') == (kind == 'keyboard'):return role
    return None


def enroll(role, node, identity):
    import re
    from .hardware import capture
    if not re.fullmatch('/dev/input/event[0-9]+',node):raise ValueError('Use an event node from udev input inventory')
    syspath=str((Path('/sys/class/input')/Path(node).name/'device').resolve(strict=True))
    properties=dict(line.split('=',1) for line in capture(['udevadm','info','--query=property','--name='+node]).splitlines() if '=' in line)
    if identity != properties.get('ID_PATH') or not internal(properties,syspath,role):
        raise ValueError('Identity does not match an eligible internal device')
    devices=enrolled();devices[role]={'identity':identity,'syspath':syspath}
    atomic(enrollment_path(),json.dumps({'version':1,'devices':devices},indent=2).encode())
    return devices[role]
