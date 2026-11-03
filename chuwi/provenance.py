"""Non-activating proxy provenance checks; orientation labels are never evidence."""
import hashlib
from pathlib import Path
import re
from .hardware import capture


def inspect(runner=capture, root=Path('/')):
    unit=runner(['systemctl','show','iio-sensor-proxy.service','-p','ActiveState,ExecStart,FragmentPath'])
    match=re.search(r'path=([^ ;}]+)',unit)
    executable=match[1] if match else ''
    record={'service':unit,'executable':executable,'provider':'unknown','verified_stock':False}
    if not unit:return record
    if 'ActiveState=active' not in unit:
        record['provider']='inactive';return record
    if not executable:return record
    path=root/executable.lstrip('/')
    try:
        contents=path.read_bytes()
        record['sha256']=hashlib.sha256(contents).hexdigest()
        package=runner(['dpkg-query','-W','-f=${Package}\t${Version}\t${Source}\n','iio-sensor-proxy'])
        record['package']=package
        md5file=root/'var/lib/dpkg/info/iio-sensor-proxy.md5sums'
        expected=dict((name,digest) for digest,name in (line.split(maxsplit=1) for line in md5file.read_text().splitlines()))
        matching=expected.get(executable.lstrip('/'))==hashlib.md5(contents).hexdigest()
        origin=runner(['apt-cache','policy','iio-sensor-proxy'])
        distro='ubuntu' in origin.lower() or 'debian' in origin.lower()
        stock=matching and distro and package.startswith('iio-sensor-proxy\t') and not re.search(r'chuwi|minibook|git',package,re.I)
        record.update(verified_stock=stock,provider='distro-standard' if stock else 'unknown or modified',package_checksum_matches=matching)
    except (OSError,ValueError):pass
    return record


def require_application_owner():
    from .session import read
    controller=read('controller')
    if controller.get('sensor_provider')=='extended':
        if controller.get('input_owner')!='application':
            raise RuntimeError('The sensor proxy controls input; application locking is disabled')
        return
    provider=inspect()
    if provider['provider']!='inactive' and not provider['verified_stock']:
        raise RuntimeError('Sensor proxy input ownership is unverified; use the application-managed extension or restore the distro proxy')
