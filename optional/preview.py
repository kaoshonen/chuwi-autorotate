#!/usr/bin/python3
"""Read-only optional package preview or explicit rollback-material capture."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def capture(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=60)
    if result.returncode:raise RuntimeError(result.stderr or result.stdout)
    return result.stdout.strip()


def preview(package):
    identity=capture(['dpkg-deb','-f',str(package),'Package'])
    allowed={'chuwi-iio-sensor-proxy','chuwi-minibook-ec-dkms','chuwi-hardware-helper'}
    if identity not in allowed:raise RuntimeError('Not a companion optional package')
    print(json.dumps({'package':identity,'sha256':hashlib.sha256(package.read_bytes()).hexdigest(),
        'dependencies':capture(['dpkg-deb','-f',str(package),'Depends']),
        'changes':capture(['apt-get','--simulate','install',str(package.resolve())]),
        'service_changes': 'Proxy replacement removes stock iio-sensor-proxy; activation is a separate approved step' if identity=='chuwi-iio-sensor-proxy' else 'No service activation or automatic module load; separate approval required',
        'read_only':True},indent=2))


def preserve(destination):
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    version=capture(['dpkg-query','-W','-f=${Version}','iio-sensor-proxy'])
    # Exact installed version must remain available, or the replacement is blocked.
    subprocess.run(['apt-get','download','iio-sensor-proxy='+version],cwd=destination,check=True)
    files=[]
    for pattern in ('/etc/systemd/system/iio-sensor-proxy.service*','/etc/dbus-1/system.d/*SensorProxy*',
                    '/usr/lib/systemd/system/iio-sensor-proxy.service','/usr/share/dbus-1/system-services/net.hadess.SensorProxy.service'):
        import glob
        for path in glob.glob(pattern):
            source=Path(path);target=destination/'files'/source.relative_to('/')
            target.parent.mkdir(parents=True,exist_ok=True)
            if source.is_dir():shutil.copytree(source,target,symlinks=True)
            elif source.is_symlink():target.symlink_to(source.readlink())
            else:shutil.copy2(source,target)
            files.append(path)
    devices=[]
    for device in Path('/sys/bus/i2c/devices').glob('i2c-MDA6655:*'):
        driver=device/'driver'
        devices.append({'name':device.name,'syspath':str(device.resolve()),'driver':str(driver.resolve()) if driver.exists() else None})
    records={'version':version,'files':files,'bindings':devices,
             'unit':capture(['systemctl','cat','iio-sensor-proxy.service']),
             'service':capture(['systemctl','show','iio-sensor-proxy.service','-p','ActiveState,UnitFileState,FragmentPath'])}
    records['packages']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.glob('*.deb')}
    (destination/'manifest.json').write_text(json.dumps(records,indent=2)+'\n')
    print('Rollback material captured; no package or service changed:',destination)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='operation',required=True)
    p=sub.add_parser('package');p.add_argument('package',type=Path)
    p=sub.add_parser('preserve-stock');p.add_argument('destination',type=Path)
    args=parser.parse_args()
    if args.operation=='package':preview(args.package)
    else:preserve(args.destination)
