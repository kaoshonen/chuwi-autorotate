"""Private D-Bus integration scenario. Never connects to the host system bus."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

bus=subprocess.Popen(['dbus-daemon','--session','--nofork','--print-address=1'],stdout=subprocess.PIPE,text=True)
try:
    address=bus.stdout.readline().strip()
    assert address.startswith('unix:')
    os.environ['DBUS_SYSTEM_BUS_ADDRESS']=address
    from gi.repository import Gio,GLib
    from chuwi.sensors import BUS,PATH,EXTENSION,SensorBroker
    connection=Gio.DBusConnection.new_for_address_sync(address,Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
    claims=[]
    data={}
    def sample(mode='tablet',valid=True):
        return {'version':GLib.Variant('u',1),'sample_monotonic_us':GLib.Variant('x',int(time.monotonic()*1e6)),
                'display_gravity':GLib.Variant('ad',[.4,-.8,.2]),'hinge_valid':GLib.Variant('b',True),
                'hinge_degrees':GLib.Variant('d',240.),'mode':GLib.Variant('s',mode),
                'orientation':GLib.Variant('s','normal'),'frame':GLib.Variant('s','test-frame'),
                'input_owner':GLib.Variant('s','application'),'valid':GLib.Variant('b',valid)}
    data.update(sample())
    xml=f'''<node><interface name="{BUS}"><method name="ClaimAccelerometer"/><method name="ReleaseAccelerometer"/>
    <property name="HasAccelerometer" type="b" access="read"/><property name="AccelerometerOrientation" type="s" access="read"/></interface>
    <interface name="{EXTENSION}"><property name="Snapshot" type="a{{sv}}" access="read"/></interface></node>'''
    node=Gio.DBusNodeInfo.new_for_xml(xml)
    def method(_c,sender,_p,_i,method,_args,invocation):
        claims.append((sender,method));invocation.return_value(None)
    def prop(_c,_s,_p,interface,name):
        return GLib.Variant('a{sv}',data) if interface==EXTENSION else GLib.Variant('b',True) if name=='HasAccelerometer' else GLib.Variant('s','normal')
    for interface in node.interfaces:connection.register_object(PATH,interface,method,prop,None)
    loop=GLib.MainLoop();thread=threading.Thread(target=loop.run,daemon=True);thread.start()
    def name(method,signature,values):
        return connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',method,GLib.Variant(signature,values),None,Gio.DBusCallFlags.NONE,3000,None)
    def wait(predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:time.sleep(.02)
        assert predicate(), 'Timed out waiting for sensor lifecycle'
    name('RequestName','(su)',(BUS,0))
    broker=SensorBroker().start()
    wait(lambda:broker.snapshot().valid)
    assert broker.snapshot().provider=='extended'
    assert broker.snapshot().gravity==(.4,-.8,.2)
    first=broker.snapshot().generation
    name('ReleaseName','(s)',(BUS,))
    wait(lambda:not broker.snapshot().valid)
    data.clear();data.update(sample('laptop'))
    name('RequestName','(su)',(BUS,0))
    wait(lambda:broker.snapshot().valid and broker.snapshot().generation>first)
    assert broker.snapshot().mode=='laptop'
    data.clear();data.update(sample('tablet'))
    connection.emit_signal(None,PATH,'org.freedesktop.DBus.Properties','PropertiesChanged',GLib.Variant('(sa{sv}as)',(EXTENSION,{'Snapshot':GLib.Variant('a{sv}',data)},[])))
    wait(lambda:broker.snapshot().mode=='tablet')
    time.sleep(3.1)
    assert not broker.snapshot().fresh(time.monotonic())
    broker.close()
    wait(lambda:any(method=='ReleaseAccelerometer' for sender,method in claims))
    assert sum(method=='ClaimAccelerometer' for sender,method in claims)==2,claims
    loop.quit();thread.join(1);connection.close_sync(None)
    print('Private D-Bus claims, replacement, properties, stale data and release passed')
finally:
    bus.terminate();bus.wait(timeout=5)
