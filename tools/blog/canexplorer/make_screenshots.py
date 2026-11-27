"""Generate the CANExplorer screenshots of the blog post "From the OBD port to a DBC file".

The traffic comes from a fictional test car (test_car.dbc, the "ground truth" the reader is supposed to discover),
so every value in the screenshots matches the post: the driver's door is bit 8 of 0x500, the vehicle speed is the
Motorola field 0x500[23|16@0+] with factor 0.01, followed by a nibble alive counter and a CRC-8. found.dbc is the
database the reader ends up with.

Needs a CANExplorer checkout; run it with CANExplorer's environment (no hardware, no display needed):

    cd ../CANExplorer
    QT_QPA_PLATFORM=offscreen uv run python ../dbchub/tools/blog/canexplorer/make_screenshots.py [--only NAME,…]
"""

import asyncio
import math
import os
import shutil
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import can
import qasync
from PyQt6 import sip
from PyQt6.QtCore import QObject, QSettings, QUrl
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtQuick import QQuickWindow

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[2] / 'site' / 'assets' / 'img' / 'blog' / 'canexplorer'

os.environ.setdefault('QT_QUICK_BACKEND', 'rhi')
os.environ.setdefault('QSG_RHI_BACKEND', 'opengl')
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
# Throwaway settings and key store: never the user's
QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
os.environ['CANEXPLORER_KEYSTORE'] = str(Path(tempfile.mkdtemp()) / 'keystore.json')

ONLY = set()
if '--only' in sys.argv:
    ONLY = set(sys.argv[sys.argv.index('--only') + 1].split(','))

from can_explorer.core.bushealth import ERR_CNT, ERR_CRTL, ERR_PROT, ERR_RESTARTED  # noqa: E402
from can_explorer.core.decoding import open_signal_database  # noqa: E402
from can_explorer.core.reverse.checksums import sae_j1850  # noqa: E402
from can_explorer.core.reverse.markers import Marker  # noqa: E402
from can_explorer.core.reverse.synthetic import Behaviour, generate  # noqa: E402
from can_explorer.main_application import MainApplication  # noqa: E402
from can_explorer.sys.environment import sanitize_snap_environment  # noqa: E402

sanitize_snap_environment()
app = MainApplication()
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)
window = sip.cast(app._qml_engine.rootObjects()[0], QQuickWindow)
window.resize(1440, 900)
manager = app.connection_manager

# The tabs of the Reverse engineering view, in order
TAB = {
    name: index
    for index, name in enumerate(
        ('bits', 'activity', 'analysis', 'crypto', 'gateway', 'secoc', 'security', 'reference', 'markers', 'related',
         'reaction', 'console')
    )
}

DURATION = 60.0
NOTCH_AT = 30.0
DOOR_TOGGLES = (32.0, 38.0, 44.0, 50.0, 56.0)  # Driver's door opened, closed, opened …


def items(type_name: str):
    found = window.findChildren(QObject)
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        found.append(item)
        stack.extend(item.childItems())
    return [item for item in found if type_name in item.metaObject().className()]


def current_view():
    return [view for view in items('ConnectionView') if view.isVisible()][0]


def show_page(page: str):
    current_view().setProperty('page', page)


async def shot(name: str, delay: float = 0.5):
    await asyncio.sleep(delay)
    if ONLY and name not in ONLY:
        return
    if window.height() > 900:
        root = window.contentItem().parentItem() or window.contentItem()
        result = root.grabToImage()
        done = asyncio.Event()
        result.ready.connect(done.set)
        await asyncio.wait_for(done.wait(), 5)
        # The grab has no window background: paint the theme's under it
        image = QImage(result.image().size(), QImage.Format.Format_ARGB32)
        image.fill(QColor('#171827'))
        painter = QPainter(image)
        painter.drawImage(0, 0, result.image())
        painter.end()
        image.save(str(OUT / f'{name}.png'))
    else:
        window.grabWindow().save(str(OUT / f'{name}.png'))
    print('saved', name)


async def until(condition, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError('gave up waiting')
        await asyncio.sleep(0.05)


def test_car_traffic(start: float, duration: float, seed: int, door_toggles=()):
    """Frames of the test car; the door switch of 0x500 follows door_toggles (seconds from start) and the CRC of
    the frame is recomputed after it."""
    def sweep(period, amplitude=0.95):
        return Behaviour('sine', period=period, amplitude=amplitude)

    behaviours = {
        'Powertrain.EngineSpeed': sweep(21, 0.9),
        'Powertrain.Throttle': Behaviour('ramp', period=15, amplitude=0.9),
        'Powertrain.Torque': sweep(23, 0.9),
        'Powertrain.Counter': Behaviour('counter'),
        'Powertrain.BrakePressed': Behaviour('constant'),
        'Powertrain.Checksum': Behaviour('checksum'),
        'Steering.SteeringAngle': sweep(31, 0.6),
        'BodyStatus.DoorFrontLeft': Behaviour('constant'),
        'BodyStatus.VehicleSpeed': sweep(27),
        'BodyStatus.AliveCounter': Behaviour('counter'),
        'BodyStatus.Checksum': Behaviour('constant'),  # Motorola: the CRC is set below
        'HvBattery.Temperature': Behaviour('sine', period=40, amplitude=0.6),
    }
    for group in range(3):
        behaviours[f'HvBattery.CellA{group}'] = sweep(7 + 2 * group, 0.8)
        behaviours[f'HvBattery.CellB{group}'] = sweep(9 + 2 * group, 0.8)
    traffic = generate(open_signal_database(HERE / 'test_car.dbc'), duration, behaviours, seed=seed, start=start)
    toggles = [start + seconds for seconds in door_toggles]
    door = 0
    for msg in traffic.messages:
        while toggles and msg.timestamp >= toggles[0]:
            door ^= 1
            toggles.pop(0)
        if msg.arbitration_id == 0x500:
            msg.data[1] = (msg.data[1] & 0xFE) | door
            msg.data[7] = sae_j1850(bytes(msg.data[:7]))
    return traffic


def cluster_echo(traffic):
    """The instrument cluster re-broadcasts the vehicle speed on 0x510: Intel, half the resolution, 20 ms late."""
    frames = []
    for stamp, raw in traffic.raw['BodyStatus.VehicleSpeed'][::2]:
        value = int(raw) // 2
        frames.append(
            can.Message(
                timestamp=stamp + 0.02,
                arbitration_id=0x510,
                is_extended_id=False,
                data=bytes([value & 0xFF, (value >> 8) & 0xFF, 0x00, 0x00]),
            )
        )
    return frames


async def new_connection_shot():
    dialog = items('NewConnectionDialog')[0]
    dialog.open()
    await asyncio.sleep(0.3)
    children = dialog.findChildren(QObject)
    name_field = next(c for c in children if 'TextField' in c.metaObject().className() and c.property('visible'))
    name_field.setProperty('text', 'Test car (OBD port)')
    boxes = [c for c in children if 'ComboBox' in c.metaObject().className()]
    # The interface: SocketcanBus is preselected when python-can offers it
    channel_box = next(b for b in boxes if b.property('editable'))
    channel_box.setProperty('editText', 'can0')
    await shot('new-connection')
    dialog.close()


async def reverse_scenario():
    start = time.time() - DURATION
    traffic = test_car_traffic(start, DURATION, seed=7, door_toggles=DOOR_TOGGLES)
    door_times = [start + seconds for seconds in DOOR_TOGGLES]

    manager.add_connection({'name': 'Test car (OBD port)', 'interface': 'VirtualBus', 'channel': f'blog-{uuid.uuid4().hex[:6]}'})
    connection = manager.connections[-1]
    connection.open()
    window.setProperty('selectedIndex', len(manager.connections) - 1)
    show_page('reverse')
    await asyncio.sleep(0.4)
    reverse = connection.reverse
    tabs = current_view().findChild(QObject, 'reverseTabs')

    door = 0
    for msg in traffic.messages:
        if msg.timestamp >= start + NOTCH_AT and not reverse.notched:
            reverse.notch()
        while door_times and msg.timestamp >= door_times[0]:
            door ^= 1
            reverse.markers._markers.append(
                Marker(door_times.pop(0) + 0.3, 'door opened' if door else 'door closed')
            )
        connection.session._dispatch(msg)
    connection._refresh()
    connection.session.bus_statistics.reset()
    connection._sample_bus_statistics()

    show_page('overview')
    await shot('overview', 0.8)

    # Bits: after the notch only the door bit of 0x500 stands out; the speed as a Motorola bit field
    show_page('reverse')
    reverse.select('500')
    reverse.set_field({'byte_order': 'big_endian'})
    reverse.select_bits(23, 24)
    reverse.set_field({'scale': 0.01, 'unit': 'km/h', 'name': 'VehicleSpeed'})
    await shot('reverse-engineering', 0.7)

    # Signals: the right byte order gives a smooth curve, the wrong one jumps
    reverse.plot_field()
    reverse.set_field({'byte_order': 'little_endian'})
    reverse.select_bits(16, 31)
    reverse.set_field({'scale': 0.01, 'unit': 'km/h', 'name': 'VehicleSpeed_wrong_order'})
    reverse.plot_field()
    show_page('signals')
    connection.plot_view.fit_all()
    await shot('signals', 1.0)
    show_page('reverse')
    reverse.set_field({'byte_order': 'big_endian'})
    reverse.select_bits(23, 24)
    reverse.set_field({'scale': 0.01, 'unit': 'km/h', 'name': 'VehicleSpeed'})

    reverse.analysis.run(False)
    await until(lambda: not reverse.analysis.running)
    tabs.setProperty('currentIndex', TAB['analysis'])
    await shot('reverse-analysis')

    # A GPS logger's speed as the reference
    gps = Path(tempfile.mkdtemp()) / 'gps.csv'
    rows = [f'{stamp - start:.3f},{raw * 0.01:.1f}' for stamp, raw in traffic.raw['BodyStatus.VehicleSpeed'][::50]]
    gps.write_text('time_s,speed [km/h]\n' + '\n'.join(rows))
    reverse.reference.open_csv(QUrl.fromLocalFile(str(gps)))
    reverse.reference.find('csv:0', 1.0)
    await until(lambda: not reverse.reference.running)
    tabs.setProperty('currentIndex', TAB['reference'])
    await shot('reverse-reference')

    reverse.markers.find('', 0.5, 1.0)
    await until(lambda: not reverse.markers.running)
    tabs.setProperty('currentIndex', TAB['markers'])
    await shot('reverse-markers')

    for frame in cluster_echo(traffic):
        connection.session._dispatch(frame)
    connection._refresh()
    reverse.analysis.run(False)
    await until(lambda: not reverse.analysis.running)
    reverse.related.find(0.98, 0.5)
    await until(lambda: not reverse.related.running)
    tabs.setProperty('currentIndex', TAB['related'])
    await shot('reverse-related')

    # Reaction: headlights switched on. The light switch bit of 0x3B0 flips, and a lamp status message 0x6A1
    # starts; the cluster message 0x420 keeps sending unchanged
    def feed(t0, seconds, lights_on):
        frames = []
        for step in range(int(seconds / 0.02)):
            stamp = t0 + step * 0.02
            switches = bytes([0x10, 0x04 if lights_on else 0x00, 0x00, 0x22, 0, 0, 0, 0])
            frames.append(can.Message(timestamp=stamp, arbitration_id=0x3B0, is_extended_id=False, data=switches))
            if step % 5 == 0:
                frames.append(
                    can.Message(
                        timestamp=stamp + 0.004, arbitration_id=0x420, is_extended_id=False, data=bytes([0x42, 0, 0x7F, 1])
                    )
                )
            if lights_on and step % 5 == 2:
                frames.append(
                    can.Message(timestamp=stamp + 0.008, arbitration_id=0x6A1, is_extended_id=False, data=bytes([3, 0]))
                )
        for frame in frames:
            connection.session._dispatch(frame)
        return t0 + seconds

    reaction = reverse.reaction
    t0 = feed(time.time(), 0.5, False)
    reaction.start_baseline()
    t0 = feed(t0, 2.0, False)
    reaction.mark_action()
    feed(t0, 2.0, True)
    connection._refresh()
    reaction.capture()
    await until(lambda: not reaction.running)
    tabs.setProperty('currentIndex', TAB['reaction'])
    await shot('reverse-reaction')
    tabs.setProperty('currentIndex', 0)

    # Verify: the finished database decodes a second recording, the driver's door open
    connection.load_database(QUrl.fromLocalFile(str(HERE / 'found.dbc')))
    await asyncio.sleep(0.5)
    later = test_car_traffic(time.time(), 4.0, seed=11, door_toggles=(1.0,))
    for msg in later.messages:
        connection.session._dispatch(msg)
    connection._refresh()
    show_page('trace')
    await shot('trace-decoded', 0.8)
    return connection


async def bus_health_shot(connection):
    """Listen-only at 250 kbit/s on a 500 kbit/s bus: stuff errors, the receive error counter climbs to error
    passive and no frame arrives. At 6:00 the interface is brought up at 500 kbit/s: frames, no errors."""
    health = connection.health
    health.reset()
    now = time.time()
    switch = 360
    for second in range(600):
        stamp = now - 600 + second
        if second < switch:
            if second % 3 == 0:
                rec = min(135, second + 8)
                problem = 0x10 if rec >= 128 else 0x04 if rec >= 96 else 0
                data = [0, problem, 0x04, 0x08, 0, 0, 0, rec]
                health.on_message(
                    can.Message(
                        timestamp=stamp, arbitration_id=ERR_PROT | ERR_CNT | (ERR_CRTL if problem else 0),
                        is_error_frame=True, data=bytes(data),
                    )
                )
            health.sample(stamp, 0, 0, 0.0, None)
        else:
            if second == switch:
                health.on_message(
                    can.Message(
                        timestamp=stamp, arbitration_id=ERR_RESTARTED | ERR_CNT, is_error_frame=True,
                        data=bytes([0, 0x40, 0, 0, 0, 0, 0, 0]),
                    )
                )
            load = 0.34 + 0.04 * math.sin(second / 25)
            health.sample(stamp, load * 2000, 0, load, None)
    connection.bus_health.set_window(600)
    show_page('health')
    window.resize(1440, 1160)
    await shot('bus-health', 1.0)
    window.resize(1440, 900)


async def database_editor_shots():
    database = Path(tempfile.mkdtemp()) / 'found.dbc'
    shutil.copy(HERE / 'found.dbc', database)
    editor = manager.database_editor
    editor.open_path(str(database))
    editor.select_message('BodyStatus')
    editor.select_signal('VehicleSpeed')
    window.setProperty('tool', 'database-editor')
    await shot('database-editor', 0.6)
    window.resize(1440, 1400)
    await shot('database-editor-layout', 0.8)
    window.resize(1440, 900)
    window.setProperty('tool', '')


async def scenario():
    OUT.mkdir(parents=True, exist_ok=True)
    await asyncio.sleep(0.5)
    await new_connection_shot()
    connection = await reverse_scenario()
    await bus_health_shot(connection)
    await database_editor_shots()
    manager.close_all()
    app.quit()


async def main():
    failed = False

    async def guarded():
        nonlocal failed
        try:
            await scenario()
        except BaseException:
            traceback.print_exc()
            failed = True
            app.quit()

    asyncio.ensure_future(guarded())
    closed = asyncio.Event()
    app.aboutToQuit.connect(closed.set)
    await closed.wait()
    return failed


if __name__ == '__main__':
    with loop:
        sys.exit(1 if loop.run_until_complete(main()) else 0)
