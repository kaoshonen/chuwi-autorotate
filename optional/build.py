#!/usr/bin/python3
"""Verify, patch and stage individually licensed packages. Never installs or loads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
LOCK=json.loads((ROOT/'optional/components.lock.json').read_text())


def run(args, **kwargs):
    subprocess.run(list(map(str,args)),check=True,**kwargs)


def prepare(archive, destination):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != LOCK['sha256']:
        raise RuntimeError('Pinned archive checksum mismatch')
    destination.mkdir(parents=True,exist_ok=False)
    with tarfile.open(archive) as source:
        for member in source:
            parts=Path(member.name).parts
            relative=Path(*parts[1:])
            allowed=any(relative == Path(c['path']) or Path(c['path']) in relative.parents for c in LOCK['components'].values() if 'path' in c)
            if not allowed and str(relative) != 'LICENSE.md':continue
            if '..' in parts or member.issym() or member.islnk():
                raise RuntimeError('Unexpected source archive path')
            member.name=str(relative)
            source.extract(member,destination,filter='data')
    for component in ('ec','proxy'):
        patch=ROOT/'optional'/LOCK['components'][component]['patch']
        run(['patch','--batch','--fuzz=0','-p1','-i',patch],cwd=destination)


def copy(source, target):
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)


def metadata(stage,name,description,depends='',conflicts=''):
    control=stage/'DEBIAN/control';control.parent.mkdir(parents=True)
    text=f'Package: {name}\nVersion: 0.3.0-1\nArchitecture: amd64\nMaintainer: CHUWI companion maintainers <noreply@example.invalid>\nDescription: {description}\n'
    if depends:text+=f'Depends: {depends}\n'
    if conflicts:text+=f'Conflicts: {conflicts}\n'
    control.write_text(text)


def package(component, source, output, proxy_build=None):
    output.mkdir(parents=True,exist_ok=True)
    stage=output/(component+'-root')
    if stage.exists():raise RuntimeError('Use a fresh staging directory')
    stage.mkdir()
    name={'ec':'chuwi-minibook-ec-dkms','proxy':'chuwi-iio-sensor-proxy','helper':'chuwi-hardware-helper'}[component]
    if component=='helper':
        metadata(stage,name,'Optional leased EC control and independent recovery','python3, python3-gi, polkitd, systemd')
        for file in (ROOT/'chuwi').glob('*.py'):copy(file,stage/'usr/lib/chuwi-autorotate/chuwi'/file.name)
        for filename in ('chuwi-hardware-helper','chuwi-sleep-test'):
            copy(ROOT/'optional'/filename,stage/'usr/libexec'/filename)
            copy(ROOT/'optional'/(filename+'.service'),stage/'usr/lib/systemd/system'/(filename+'.service'))
        for suffix,directory in (('conf','usr/share/dbus-1/system.d'),('policy','usr/share/polkit-1/actions'),('service','usr/share/dbus-1/system-services')):
            filename='io.github.kaoshonen.ChuwiHardware1.'+suffix
            copy(ROOT/'optional'/filename,stage/directory/filename)
        copy(ROOT/'LICENSE',stage/'usr/share/doc'/name/'copyright')
        with tarfile.open(output/(name+'-source.tar.gz'),'w:gz') as archive:
            for file in [*list((ROOT/'chuwi').glob('*.py')), ROOT/'LICENSE', ROOT/'optional/build.py', ROOT/'optional/components.lock.json', ROOT/'optional/README.md',
                         *[p for p in (ROOT/'optional').glob('*') if p.is_file() and (p.name.startswith('chuwi-hardware-helper') or p.name.startswith('chuwi-sleep-test') or p.name.startswith('io.github.kaoshonen.ChuwiHardware1.'))]]:
                archive.add(file,arcname=str(file.relative_to(ROOT)))
    else:
        spec=LOCK['components'][component]
        component_source=source/spec['path']
        notice=source/'LICENSE.md'
        copy(notice,stage/'usr/share/doc'/name/'upstream-LICENSE.md')
        copy(ROOT/'optional'/spec['patch'],stage/'usr/share/doc'/name/'changes.patch')
        if component=='ec':
            copy(ROOT/'optional/licenses/GPL-2',stage/'usr/share/doc'/name/'copyright')
            metadata(stage,name,'Optional gated MiniBook EC DKMS sources; no automatic load','dkms')
            target=stage/'usr/src/minibook_ec-1.0-chuwi1';target.mkdir(parents=True)
            for file in component_source.iterdir():
                if file.suffix in ('.c','.h') and not file.name.endswith('.mod.c') or file.name in ('Kbuild','dkms.conf'):
                    copy(file,target/file.name)
            dkms=target/'dkms.conf'
            text=dkms.read_text().replace('PACKAGE_VERSION="1.0"','PACKAGE_VERSION="1.0-chuwi1"').replace('AUTOINSTALL="yes"','AUTOINSTALL="no"')
            dkms.write_text(text)
        else:
            if not proxy_build:raise RuntimeError('--proxy-build is required')
            metadata(stage,name,'Optional application-managed dual sensor proxy','libglib2.0-0t64 (>= 2.76), libgudev-1.0-0, libdrm2, libpolkit-gobject-1-0, python3, systemd','iio-sensor-proxy')
            copy(proxy_build/'src/iio-sensor-proxy',stage/'usr/libexec/chuwi-iio-sensor-proxy')
            for filename in ('chuwi-iio-sensor-proxy.service','chuwi-sensor-restore.service'):
                copy(ROOT/'optional'/filename,stage/'usr/lib/systemd/system'/filename)
            copy(ROOT/'optional/chuwi-sensor-bindings',stage/'usr/libexec/chuwi-sensor-bindings')
            copy(proxy_build/'data/net.hadess.SensorProxy.conf',stage/'usr/share/dbus-1/system.d/net.hadess.SensorProxy.conf')
            activation=stage/'usr/share/dbus-1/system-services/net.hadess.SensorProxy.service'
            activation.parent.mkdir(parents=True,exist_ok=True)
            activation.write_text('[D-BUS Service]\nName=net.hadess.SensorProxy\nExec=/usr/libexec/chuwi-iio-sensor-proxy --application-managed --lazy\nUser=root\nSystemdService=chuwi-iio-sensor-proxy.service\n')
            for file in (component_source/'data').glob('*.rules'):
                target=stage/'usr/lib/udev/rules.d'/file.name
                copy(file,target)
                target.write_text(target.read_text().replace('TAG+="systemd", ENV{SYSTEMD_WANTS}+="iio-sensor-proxy.service"','TAG+="systemd"'))
            for file in (component_source/'data').glob('*.policy'):copy(file,stage/'usr/share/polkit-1/actions'/file.name)
            copy(component_source/'COPYING',stage/'usr/share/doc'/name/'copyright')
        with tarfile.open(output/(name+'-source.tar.gz'),'w:gz') as archive:
            for file in component_source.rglob('*'):
                if file.is_file() and (file.suffix in ('.o','.ko','.mod') or file.name.startswith('.') or file.name.endswith('.mod.c') or file.name in ('modules.order','Module.symvers')):continue
                if file.is_file():archive.add(file,arcname=str(file.relative_to(source)))
            archive.add(notice,arcname='LICENSE.md')
            if component=='ec':archive.add(ROOT/'optional/licenses/GPL-2',arcname='COPYING')
    copy(ROOT/'optional/components.lock.json',stage/'usr/share/doc'/name/'components.lock.json')
    for path in stage.rglob('*'):
        if path.is_dir():path.chmod(0o755)
        elif path.is_file():path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    run(['dpkg-deb','--root-owner-group','--build',stage,output/(name+'_0.3.0-1_amd64.deb')])
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*') if p.is_file() and p.name!='SHA256SUMS.json'}
    (output/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    prepare_parser=sub.add_parser('prepare');prepare_parser.add_argument('archive',type=Path);prepare_parser.add_argument('destination',type=Path)
    pack=sub.add_parser('package');pack.add_argument('component',choices=['ec','proxy','helper']);pack.add_argument('--source',required=True,type=Path);pack.add_argument('--output',required=True,type=Path);pack.add_argument('--proxy-build',type=Path)
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.archive,args.destination)
    else:package(args.component,args.source,args.output,args.proxy_build)

if __name__=='__main__':main()
