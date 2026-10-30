# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""Builds a self-contained replay page of the dashboard and its tips.

A scripted Beginner drive (DRIVE below: speed, assists, traffic, junctions)
runs through the real Tips class, so when each tip shows, waits or is done
is decided by tips.py exactly as in the simulator. The dashboard's frames,
10 a second, play in the real dashboard.html, wrapped in a player with
chapters and captions. Fonts are inlined, so the page is one file.

    python -m smart_manual_dashboard.make_showcase showcase.html
"""

import base64
import json
import os
import re
import sys

from smart_manual_dashboard.tips import Tips

HERE = os.path.dirname(os.path.abspath(__file__))
DT = 0.1
DURATION = 150.0

# Speed (km/h) at these times, straight lines between.
SPEED = [(0, 0), (6, 32), (12, 30), (15, 18), (18, 22), (22, 35), (64, 35), (67, 27),
         (72, 20), (76, 0), (92, 0), (98, 26), (104, 30), (150, 30)]

# What the car and the world do, as (from, to, values) spans; later spans win.
DRIVE = [
    (0, 151, dict(gear='D', activeAssist='None', setSpeed=None, accLead=None, brake=0,
                  driverPedal=15, appliedThrottle=15, cars_near=1, junction_ahead=None, yaw_rate=0.0)),
    (0, 6, dict(driverPedal=45, appliedThrottle=45)),
    (5, 15, dict(junction_ahead='approach')),
    (15, 18, dict(junction_ahead=0.0, yaw_rate=18.0, driverPedal=10, appliedThrottle=10)),
    (18, 22, dict(driverPedal=35, appliedThrottle=35)),
    (30, 64, dict(activeAssist='Cruise', setSpeed=35, driverPedal=0, appliedThrottle=20)),
    (44, 57, dict(cars_near=4)),
    (64, 98, dict(activeAssist='Cruise standby', setSpeed=35, driverPedal=0, appliedThrottle=0)),
    (64, 67, dict(brake=25)),
    (72, 76, dict(brake=30)),
    (74, 92, dict(cars_near=2)),
    (92, 98, dict(driverPedal=40, appliedThrottle=40)),
    (98, 130, dict(activeAssist='ACC', setSpeed=30, accLead=24, driverPedal=0, appliedThrottle=18)),
    (130, 150, dict(activeAssist='Autopilot', setSpeed=None, driverPedal=0, appliedThrottle=15)),
]
LEVEL_ON, LEVEL_OFF = 8.0, 144.0

CHAPTERS = [
    (0, 'Driving as normal',
     'The simulator starts like any car. The context-aware manual is off, and the tip panel says how to start it.'),
    (8, 'Beginner profile picked',
     'The researcher presses B. Beginner lights up in the header. A junction is coming up, so the manual holds back.'),
    (15, 'Turning at the junction',
     'Turning is a manoeuvre: no tip during it, or for 3 seconds after.'),
    (22, 'Tip 1: cruise control',
     'A quiet road with few cars around: the first tip appears. The drawing shows which button on the G25 shifter to tap.'),
    (30, 'Cruise control on',
     'The driver taps ON / OFF. Cruise control holds 35 km/h and the tip confirms it is done.'),
    (50, 'Holding back in traffic',
     'After 20 seconds on cruise the next tip is due, but four cars are close by. The panel says why it waits.'),
    (57, 'Tip 2: brake to switch off',
     'Steady on a straight road again, the tip explains that touching the brake hands control back.'),
    (64, 'Brake: cruise lets go',
     'The driver brakes, cruise control goes to standby and the tip is done.'),
    (76, 'Waiting in a queue',
     'Stopped behind traffic, away from a junction: standing still is a good moment for the next tip.'),
    (88, 'Tip 3: adaptive cruise',
     'The tip asks for a long press. It stays up as the car pulls away, until the driver has done it.'),
    (98, 'Adaptive cruise on',
     'Held for a second, ACC speeds up to 30 km/h and follows the car ahead at the set gap.'),
    (122, 'Tip 4: autopilot',
     'After 20 seconds with ACC, the last Beginner tip shows the autopilot button on the red row.'),
    (130, 'The car drives itself',
     'Autopilot is on: all four Beginner tips are given.'),
    (144, 'Manual off',
     'Holding R switches the context-aware manual off again.'),
]


def _speed(t):
    for (t0, v0), (t1, v1) in zip(SPEED, SPEED[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / float(t1 - t0)
    return SPEED[-1][1]


def frames():
    """The dashboard state every DT seconds, as changes from the frame before."""
    clock = [0.0]
    tips = Tips(clock=lambda: clock[0])
    out, last = [], {}
    for i in range(int(DURATION / DT) + 1):
        t = clock[0] = i * DT
        values = {}
        for t0, t1, span in DRIVE:
            if t0 <= t < t1:
                values.update(span)
        if values['junction_ahead'] == 'approach':
            values['junction_ahead'] = 25.0 * (15 - t) / 10.0
        speed = _speed(t)
        state = {k: values[k] for k in ('gear', 'activeAssist', 'setSpeed', 'accLead', 'brake',
                                        'driverPedal', 'appliedThrottle')}
        context = dict(speed=speed, yaw_rate=values['yaw_rate'], cars_near=values['cars_near'],
                       walkers_near=0, junction_ahead=values['junction_ahead'], indicating=False)
        if abs(t - LEVEL_ON) < DT / 2:
            tips.start('Beginner')
        if abs(t - LEVEL_OFF) < DT / 2:
            tips.stop()
        state.update(tips.update(state, context))
        state.update(speed=int(round(speed)), apTarget=30, apDistance=17, driveMode='Normal',
                     regenLevel=1, throttleOverride=False)
        out.append({k: v for k, v in state.items() if last.get(k, object()) != v})
        last = state
    return out


def _font_faces():
    faces = [("Barlow", 400, 'Barlow-Regular'), ("Barlow", 600, 'Barlow-SemiBold'),
             ("Barlow", 700, 'Barlow-Bold'),
             ("Barlow Semi Condensed", 600, 'BarlowSemiCondensed-SemiBold'),
             ("Barlow Semi Condensed", 700, 'BarlowSemiCondensed-Bold')]
    css = ''
    for family, weight, name in faces:
        with open(os.path.join(HERE, 'fonts', name + '.woff2'), 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        css += ("@font-face { font-family: '%s'; font-weight: %d; "
                "src: url(data:font/woff2;base64,%s) format('woff2'); }\n" % (family, weight, data))
    return css


def build():
    with open(os.path.join(HERE, 'dashboard.html'), encoding='utf-8') as f:
        page = f.read()
    style = re.search(r'<style>(.*?)</style>', page, re.S).group(1)
    script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
    # The page's own fonts and full-window rules give way to the player's.
    style = '\n'.join(line for line in style.split('\n')
                      if not line.startswith(('@font-face', 'html, body', '#offline')))
    # Fed by the player instead of the driver script's event stream.
    script = script[:script.index("var events = new EventSource")]
    script = script.replace(
        "var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);",
        "var s = stage.parentNode.clientWidth / 1920;")
    script = script.replace("  var c = CHIMES[name];\n",
                            "  if (!window.soundOn) return;\n  var c = CHIMES[name];\n", 1)
    script = script.replace("function updateHum() {\n",
                            "function updateHum() {\n  if (!window.soundOn) { if (hum) hum.out.gain.value = 0; return; }\n", 1)
    with open(os.path.join(HERE, 'showcase_template.html'), encoding='utf-8') as f:
        template = f.read()
    data = dict(dt=DT, duration=DURATION, frames=frames(),
                chapters=[dict(t=t, title=title, text=text) for t, title, text in CHAPTERS])
    return (template.replace('/*FONTS*/', _font_faces())
            .replace('/*DASHBOARD_STYLE*/', style)
            .replace('/*DASHBOARD_SCRIPT*/', script)
            .replace('/*DATA*/', json.dumps(data, separators=(',', ':'))))


def main():
    if len(sys.argv) != 2:
        sys.exit('usage: python -m smart_manual_dashboard.make_showcase OUTPUT.html')
    with open(sys.argv[1], 'w', encoding='utf-8') as f:
        f.write(build())
    print('Wrote %s' % sys.argv[1])


if __name__ == '__main__':
    main()
