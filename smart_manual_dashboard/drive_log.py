"""Logs a participant's drive and scores how they drive.

manual_control_driver.py --participant P07 starts a DriveLog when the
manual is first started (B), and from then on calls DriveLog.frame() once a
frame with the dashboard state and what it measured in the world (the
probe):

  speed        km/h
  limit        the speed limit where the car is, km/h (Vehicle.get_speed_limit)
  lateral      m from the centre of the lane, + right (from the map's waypoint)
  lane         the lane the car is in, 'road:section:lane'
  junction     the car is in a junction
  lead         (gap in m, speed in km/h) of the car ahead in the lane, or None
  a_long       m/s2 along the car, - braking (Vehicle.get_acceleration)
  steer        -1..1, the steering applied
  indicating   a blinker is on
  brake_pedal  0..1, the driver's own brake, before cruise control and regen
  x, y         m

and DriveLog.event() with each collision and lane crossing its sensors
report. The manual runs as usual: that is the guided phase. F starts the
free drive: the manual pauses for FREE_DRIVE_SECONDS, or until F again,
while the participant drives as they like, and the drive is then scored and
the score shown in
the tip panel as a final tip. The log goes on until the driver script ends.

The participant's folder (study_data/P07/) gets
  drive-<time>.csv    the probe and the state, SAMPLE_HZ times a second
  events-<time>.csv   phases, tips shown and done, features used, assists
                      handed back, collisions and lane crossings
  drive.json          the metrics of each phase and the score, with what
                      each means; report.py reads it

The metrics are the ones the driving-simulator literature uses, from what
CARLA gives: collisions (the collision sensor), close calls (time to
collision with the car ahead under TTC_CRITICAL_S), tailgating (time
headway under THW_SHORT_S), lane keeping (the standard deviation of lateral
position, SDLP, and lane crossings without a blinker from the lane-invasion
sensor), speeding (over the map's limit) and harsh braking. Feature use is
which of the features the tips prompted the participant to try they use
again, and how long the assists were on. score() turns them into five parts of 0-100,
each a plain rule, and the total is their mean.
"""

import csv
import json
import math
import os
import shutil
import time

from smart_manual_dashboard.tips import TIPS, Tips, _family

# The tip ids, to the feature each teaches.
TIP_FEATURES = {tip['id']: tip['learn'] for tip in TIPS}

# Each participant's drive logs and report go in a folder of their own under
# here: study_data/ beside the driver script, or SMART_MANUAL_DATA.
DATA_DIR = os.environ.get('SMART_MANUAL_DATA') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'study_data')

SAMPLE_HZ = 10.0
FREE_DRIVE_SECONDS = 120.0

MOVING_KMH = 5.0
SPEEDING_MARGIN_KMH = 5.0
TTC_CRITICAL_S = 1.5
THW_SHORT_S = 1.0
# A car ahead closer than this in time is being followed.
THW_FOLLOWING_S = 3.0
# Harsh braking is the driver's: on the brake pedal, not maximum regen on
# lift-off (one-pedal driving, about 3.9 m/s2, which the manual teaches).
HARSH_BRAKE_MS2 = 3.5
HARSH_BRAKE_END_MS2 = 2.5
BRAKE_PEDAL_ON = 0.05
# One contact fires the collision sensor many times; and one lane change the
# lane-invasion sensor more than once.
EVENT_MERGE_SECONDS = 1.0
# SDLP over stretches of one lane, outside junctions, driver steering.
SDLP_MIN_KMH = 10.0
SDLP_MIN_STRETCH_S = 3.0
# A lane change is not wandering: SDLP leaves out this long either side of
# each lane crossing. On the first real drive this took a driver's SDLP from
# 43 cm to 18 cm (the autopilot's is 10 cm).
SDLP_LANE_CHANGE_S = 3.0
SDLP_GOOD_M = 0.20
SDLP_POOR_M = 0.50

FEATURE_NAMES = {
    'cruise-on': 'Cruise control',
    'brake-cancel': 'Braking to cancel cruise',
    'acc-on': 'Adaptive cruise',
    'autopilot-on': 'Autopilot',
    'speed-adjust': 'Set speed + / -',
    'acc-speed-adjust': 'ACC set speed + / -',
    'resume': 'Resume (RES)',
    'mode-dynamic': 'Dynamic mode',
    'ap-speed': 'Autopilot speed',
    'gap-change': 'Following gap',
    'regen-max': 'One-pedal driving',
    'ap-override': 'Overriding the autopilot',
    'cruise-off': 'Cruise control off',
    'acc-off': 'Adaptive cruise off',
    # The feature groups the score counts (FEATURE_GROUPS).
    'leave-cruise': 'Leaving cruise (brake or button)',
    'set-speed': 'Set speed + / -',
    'mode-regen': 'Drive mode or regen',
}

# The free drive's feature score counts each of these once: one use of any
# member counts for the group, so 100 does not need, say, the speed changed
# under cruise, ACC and the autopilot each, or cruise left both ways. A
# feature in no group counts on its own.
FEATURE_GROUPS = {
    'leave-cruise': ('brake-cancel', 'cruise-off', 'acc-off'),
    'set-speed': ('speed-adjust', 'acc-speed-adjust', 'ap-speed'),
    'mode-regen': ('mode-dynamic', 'regen-max'),
}
FEATURE_GROUP_OF = {f: g for g, members in FEATURE_GROUPS.items() for f in members}


def feature_group(feature):
    return FEATURE_GROUP_OF.get(feature, feature)

COLUMNS = ['t', 'phase', 'x', 'y', 'speed', 'limit', 'assist', 'gear', 'drive_mode', 'regen',
           'pedal', 'throttle', 'brake', 'brake_pedal', 'steer', 'a_long', 'lateral', 'lane', 'junction',
           'lead_gap', 'lead_speed', 'thw', 'ttc', 'indicating', 'tip']

# What each metric is, for drive.json and the report.
METRICS = {
    'duration_s': 'How long the phase lasted.',
    'distance_km': 'How far the car went.',
    'collisions': 'Contacts with anything, from CARLA\'s collision sensor (one per %.0f s).' % EVENT_MERGE_SECONDS,
    'close_calls': 'Times the time to collision with the car ahead (gap / closing speed) fell under %.1f s.'
                   % TTC_CRITICAL_S,
    'min_ttc_s': 'The shortest time to collision with the car ahead.',
    'following_s': 'Time spent following a car (time headway under %.0f s).' % THW_FOLLOWING_S,
    'short_headway_share': 'Share of that following time at a time headway (gap / own speed) under %.1f s.'
                           % THW_SHORT_S,
    'sdlp_m': 'Standard deviation of lateral position: how much the car wanders in its lane, over stretches '
              'of at least %.0f s in one lane, outside junctions, above %.0f km/h, while the driver steers, '
              'leaving out %.0f s either side of each lane crossing.'
              % (SDLP_MIN_STRETCH_S, SDLP_MIN_KMH, SDLP_LANE_CHANGE_S),
    'unsignalled_crossings': 'Lane markings crossed without a blinker while the driver steers, from CARLA\'s '
                             'lane-invasion sensor.',
    'speeding_share': 'Share of the time moving (over %.0f km/h) above the speed limit + %.0f km/h; the limit '
                      'is the map\'s signs, or the driver script\'s --speed-limit.' % (MOVING_KMH, SPEEDING_MARGIN_KMH),
    'mean_speed_kmh': 'Average speed while moving.',
    'harsh_brakes': 'Braking harder than %.1f m/s2 on the brake pedal while the driver controls the speed '
                    '(regen on lift-off does not count).' % HARSH_BRAKE_MS2,
    'assist_share': 'Share of the time each assist was on.',
    'features_used': 'Features the driver used in the phase.',
    'prompted_features_used': 'Of the features the tips prompted the driver to try, those used in the phase.',
    'takeovers': 'Times the driver took the car back from the autopilot.',
}


def _now_stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def _clamp(v):
    return max(0.0, min(100.0, v))


# ==============================================================================
# -- metrics -------------------------------------------------------------------
# ==============================================================================


def metrics(rows, events, prompted=()):
    """The metrics of a phase from its rows (the CSV's) and its events;
    prompted is the features the tips showed (prompted_features)."""
    m = dict(duration_s=0.0, distance_km=0.0)
    if not rows:
        return m
    duration = moving = over = following = short = 0.0
    speed_sum = 0.0
    min_ttc = None
    close_calls = harsh = 0
    in_close = in_harsh = False
    assist_time = {}
    crossings = [e['t'] for e in events if e['kind'] == 'lane-crossing']
    stretches, stretch, stretch_lane = [], [], None
    for prev, row in zip([None] + rows[:-1], rows):
        dt = 1.0 / SAMPLE_HZ if prev is None else min(0.5, max(0.0, row['t'] - prev['t']))
        duration += dt
        speed = row['speed']
        m['distance_km'] += speed / 3.6 * dt / 1000.0
        family = _family(row['assist'])
        active = family if family and not row['assist'].endswith('standby') else 'None'
        assist_time[active] = assist_time.get(active, 0.0) + dt
        driver_speed = active == 'None'
        driver_steers = active != 'Autopilot'

        if speed > MOVING_KMH:
            moving += dt
            speed_sum += speed * dt
            if row['limit'] and speed > row['limit'] + SPEEDING_MARGIN_KMH:
                over += dt

        thw, ttc = row['thw'], row['ttc']
        if thw is not None and thw < THW_FOLLOWING_S:
            following += dt
            if thw < THW_SHORT_S:
                short += dt
        if ttc is not None:
            min_ttc = ttc if min_ttc is None else min(min_ttc, ttc)
        critical = ttc is not None and ttc < TTC_CRITICAL_S
        close_calls += critical and not in_close
        in_close = critical

        braking = (row['a_long'] is not None and driver_speed and speed > MOVING_KMH and
                   (row.get('brake_pedal') or 0.0) > BRAKE_PEDAL_ON)
        if braking and row['a_long'] < -HARSH_BRAKE_MS2 and not in_harsh:
            harsh += 1
            in_harsh = True
        elif not braking or row['a_long'] > -HARSH_BRAKE_END_MS2:
            in_harsh = False

        steady = (driver_steers and not row['junction'] and speed >= SDLP_MIN_KMH and
                  row['lateral'] is not None and
                  not any(abs(row['t'] - c) < SDLP_LANE_CHANGE_S for c in crossings))
        if steady and row['lane'] == stretch_lane:
            stretch.append((dt, row['lateral']))
        else:
            stretches.append(stretch)
            stretch, stretch_lane = ([(dt, row['lateral'])], row['lane']) if steady else ([], None)
    stretches.append(stretch)

    # Pooled over the stretches: each one's spread around its own mean, so a
    # car that sits a little off-centre is not counted as wandering.
    sq = n = 0.0
    for s in stretches:
        if sum(dt for dt, _ in s) < SDLP_MIN_STRETCH_S or len(s) < 3:
            continue
        mean = sum(x for _, x in s) / len(s)
        sq += sum((x - mean) ** 2 for _, x in s)
        n += len(s) - 1
    m.update(
        duration_s=round(duration, 1),
        distance_km=round(m['distance_km'], 2),
        collisions=sum(1 for e in events if e['kind'] == 'collision'),
        close_calls=close_calls,
        min_ttc_s=None if min_ttc is None else round(min_ttc, 2),
        following_s=round(following, 1),
        short_headway_share=round(short / following, 3) if following else 0.0,
        sdlp_m=round(math.sqrt(sq / n), 3) if n else None,
        unsignalled_crossings=sum(1 for e in events if e['kind'] == 'lane-crossing' and
                                  e['detail'].startswith('unsignalled')),
        speeding_share=round(over / moving, 3) if moving else 0.0,
        mean_speed_kmh=round(speed_sum / moving, 1) if moving else 0.0,
        harsh_brakes=harsh,
        assist_share={k: round(v / duration, 3) for k, v in sorted(assist_time.items())},
        takeovers=sum(1 for e in events if e['kind'] == 'takeover'))
    used = sorted(set(e['detail'] for e in events if e['kind'] == 'feature'))
    m['features_used'] = used
    used_groups = set(feature_group(f) for f in used)
    m['prompted_features'] = list(prompted)
    m['prompted_features_used'] = [f for f in prompted if feature_group(f) in used_groups]
    return m


def prompted_features(events):
    """The feature groups (FEATURE_GROUPS) the manual's tips prompted the
    driver to try, each once, in order."""
    out = []
    for e in events:
        learn = TIP_FEATURES.get(e['detail']) if e['kind'] == 'tip-shown' else None
        if learn and feature_group(learn) not in out:
            out.append(feature_group(learn))
    return out


def score(m):
    """The five parts of the driving score, 0-100 each, and their mean.

    Each part is a plain rule, so a participant (and the report) can see
    where every point went. Counts are per minute, so a long and a short
    phase compare.
      Safety      100, less 40 a collision, 15 a close call, and up to 50
                  for the share of following time spent tailgating
      Lane        100 at an SDLP of SDLP_GOOD_M or less, 0 at SDLP_POOR_M,
                  less 15 an unsignalled lane crossing; 100 when the
                  autopilot did all the steering
      Speed       the share of the moving time within the limit
      Smoothness  100, less 25 a harsh brake
      Features    the share of the features the tips prompted that the
                  driver used again: do they remember how they work? Each
                  of FEATURE_GROUPS counts once. Left out of the total
                  when no tip was shown.
    """
    minutes = max(m.get('duration_s', 0.0), 1.0) / 60.0
    per_min = lambda n: n / minutes
    parts = []

    safety = 100 - 40 * per_min(m['collisions']) - 15 * per_min(m['close_calls']) \
        - 50 * m['short_headway_share']
    notes = []
    if m['collisions']:
        notes.append('%d collision%s' % (m['collisions'], 's' if m['collisions'] > 1 else ''))
    if m['close_calls']:
        notes.append('%d close call%s with the car ahead' % (m['close_calls'], 's' if m['close_calls'] > 1 else ''))
    if m['short_headway_share'] >= 0.05:
        notes.append('%d%% of following time under %.0f s behind' % (100 * m['short_headway_share'], THW_SHORT_S))
    parts.append(dict(key='safety', name='Safety', score=_clamp(safety),
                      note=', '.join(notes) or 'no collisions or close calls'))

    if m['sdlp_m'] is None:
        lane, note = 100.0, ('the autopilot did the steering' if m['assist_share'].get('Autopilot', 0) > 0.5
                             else 'not enough steady driving in one lane to measure')
    else:
        lane = 100 * (SDLP_POOR_M - m['sdlp_m']) / (SDLP_POOR_M - SDLP_GOOD_M)
        note = 'wandered %.0f cm in the lane' % (100 * m['sdlp_m'])
    lane -= 15 * per_min(m['unsignalled_crossings'])
    if m['unsignalled_crossings']:
        note += ', %d lane crossing%s without a blinker' % (
            m['unsignalled_crossings'], 's' if m['unsignalled_crossings'] > 1 else '')
    parts.append(dict(key='lane', name='Lane keeping', score=_clamp(lane), note=note))

    parts.append(dict(key='speed', name='Speed', score=_clamp(100 * (1 - m['speeding_share'])),
                      note=('%d%% of the time over the limit' % round(100 * m['speeding_share'])
                            if m['speeding_share'] >= 0.005 else 'kept to the speed limit')))

    parts.append(dict(key='smooth', name='Smoothness', score=_clamp(100 - 25 * per_min(m['harsh_brakes'])),
                      note=('%d harsh brake%s' % (m['harsh_brakes'], 's' if m['harsh_brakes'] > 1 else '')
                            if m['harsh_brakes'] else 'no harsh braking')))

    prompted, used = m.get('prompted_features', []), m.get('prompted_features_used', [])
    if prompted:
        missed = [f for f in prompted if f not in used]
        shown = 'the feature the tip showed' if len(prompted) == 1 else 'the %d features the tips showed' % len(prompted)
        if not missed:
            note = 'used %s' % (shown if len(prompted) == 1 else 'all ' + shown)
        elif used:
            note = 'used %d of %s, not %s' % (
                len(used), shown, ', '.join(FEATURE_NAMES.get(f, f) for f in missed))
        else:
            note = 'did not use %s' % (shown if len(prompted) == 1 else 'any of ' + shown)
        parts.append(dict(key='features', name='Features', score=100.0 * len(used) / len(prompted), note=note))

    for p in parts:
        p['score'] = int(round(p['score']))
    return dict(total=int(round(sum(p['score'] for p in parts) / len(parts))), parts=parts)


def final_tip(result, seconds=FREE_DRIVE_SECONDS):
    """The score as a tip for the dashboard's tip panel."""
    parts = result['parts']
    best = max(parts, key=lambda p: p['score'])
    worst = min(parts, key=lambda p: p['score'])
    if worst['score'] >= 90:
        body = 'A strong drive all round. Best: %s, %s.' % (best['name'].lower(), best['note'])
    else:
        body = 'Best: %s, %s. To work on: %s, %s.' % (best['name'].lower(), best['note'],
                                                      worst['name'].lower(), worst['note'])
    return dict(id='final-score', title='Your drive: %d / 100' % result['total'], body=body,
                why='Final tip · scored on the last %d seconds you drove without tips' % seconds,
                score=result)


# ==============================================================================
# -- DriveLog ------------------------------------------------------------------
# ==============================================================================


class DriveLog(object):
    def __init__(self, folder, clock=time.time, free_seconds=FREE_DRIVE_SECONDS):
        self.folder = folder
        self._clock = clock
        self._free_seconds = free_seconds
        self.phase = None
        self._csv = self._events_csv = None
        self._reset()

    def _reset(self):
        self._t0 = None
        self._next_sample = 0.0
        self._rows = []
        self._events = []
        self._prev_state = None
        self._tip = None
        self._free_since = None
        self._result = None
        self._free_duration = 0.0
        self._last_event = {}

    @property
    def running(self):
        return self.phase is not None

    @property
    def free_driving(self):
        return self.phase == 'free'

    def start(self):
        """Starts a new log (closing any running one) in the guided phase."""
        self.close()
        self._reset()
        os.makedirs(self.folder, exist_ok=True)
        stamp = _now_stamp()
        self.stamp = stamp
        self._t0 = self._clock()
        self._csv_file = open(os.path.join(self.folder, 'drive-%s.csv' % stamp), 'w', newline='', encoding='utf-8')
        self._csv = csv.DictWriter(self._csv_file, COLUMNS)
        self._csv.writeheader()
        self._events_file = open(os.path.join(self.folder, 'events-%s.csv' % stamp), 'w', newline='',
                                 encoding='utf-8')
        self._events_csv = csv.DictWriter(self._events_file, ['t', 'phase', 'kind', 'detail'])
        self._events_csv.writeheader()
        self._set_phase('guided')
        return 'Test started, logging to %s' % os.path.basename(self.folder)

    def next_participant(self):
        """Closes this test; the next one logs to a new session-<time> folder. Returns its name."""
        self.close()
        self._reset()
        name = time.strftime('session-%Y%m%d-%H%M%S')
        self.folder = os.path.join(os.path.dirname(self.folder), name)
        return name

    def toggle_free_drive(self):
        """F: starts the free drive, or ends it early and scores it."""
        if not self.running:
            return 'Press B to start the test first'
        if self.phase == 'free':
            self._finish_free_drive()
            if self._result is None:
                return 'Free drive too short to score'
            return 'Free drive ended: score %d' % self._result['total']
        if self._result is not None:
            return 'The free drive is done: restart the script for a new test'
        self._set_phase('free')
        self._free_since = self._clock()
        return 'Free drive: up to %d s, F again to end it' % self._free_seconds

    def _set_phase(self, phase):
        self.phase = phase
        self.event('phase', phase)

    def _t(self):
        return round(self._clock() - self._t0, 2)

    def event(self, kind, detail=''):
        """A thing that happened: collision, lane-crossing (signalled/unsignalled), ..."""
        if not self.running:
            return
        if kind in ('collision', 'lane-crossing'):
            now = self._clock()
            if now - self._last_event.get(kind, -1e9) < EVENT_MERGE_SECONDS:
                return
            self._last_event[kind] = now
        e = dict(t=self._t(), phase=self.phase, kind=kind, detail=detail)
        self._events.append(e)
        self._events_csv.writerow(e)

    def frame(self, state, probe):
        """Once a frame. Returns what to lay over the dashboard state."""
        if not self.running:
            return dict(freeDrive=False)
        prev = self._prev_state
        for feature in Tips._events(prev, state):
            if feature in FEATURE_NAMES:
                self.event('feature', feature)
        if prev is not None and prev.get('activeAssist') == 'Autopilot' != state.get('activeAssist'):
            self.event('takeover', state.get('activeAssist', 'None'))
        self._prev_state = dict(state)
        tip = state.get('tip') if state.get('showTip') else None
        key = None if tip is None else (tip.get('id'), bool(tip.get('complete')))
        if key != self._tip and tip is not None and tip.get('id') != 'final-score':
            self.event('tip-done' if key[1] else 'tip-shown', key[0])
        self._tip = key

        now = self._clock()
        if now >= self._next_sample:
            self._next_sample = now + 1.0 / SAMPLE_HZ
            self._sample(state, probe, tip)

        if self.phase == 'free':
            left = int(math.ceil(self._free_seconds - (now - self._free_since)))
            if left <= 0:
                self._finish_free_drive()
            else:
                return dict(freeDrive=True, showTip=False, tip=None, tipProgress=None,
                            contextStatus='%d:%02d left' % (left // 60, left % 60))
        if self._result is not None:
            return dict(freeDrive=False, showTip=True,
                        tip=final_tip(self._result, self._free_duration))
        return dict(freeDrive=False)

    def _sample(self, state, probe, tip):
        speed = probe['speed']
        lead = probe.get('lead')
        thw = ttc = None
        if lead is not None:
            gap, lead_kmh = lead
            v, v_lead = speed / 3.6, lead_kmh / 3.6
            if v > MOVING_KMH / 3.6:
                thw = round(gap / v, 2)
            if v - v_lead > 0.5:
                ttc = round(gap / (v - v_lead), 2)
        r = lambda v, n=2: None if v is None else round(v, n)
        row = dict(t=self._t(), phase=self.phase, x=r(probe.get('x'), 1), y=r(probe.get('y'), 1),
                   speed=r(speed, 1), limit=r(probe.get('limit'), 0), assist=state.get('activeAssist', 'None'),
                   gear=state.get('gear'), drive_mode=state.get('driveMode'), regen=state.get('regenLevel'),
                   pedal=state.get('driverPedal'), throttle=state.get('appliedThrottle'),
                   brake=state.get('brake'), brake_pedal=r(probe.get('brake_pedal')), steer=r(probe.get('steer'), 3), a_long=r(probe.get('a_long')),
                   lateral=r(probe.get('lateral'), 3), lane=probe.get('lane'),
                   junction=int(bool(probe.get('junction'))),
                   lead_gap=r(lead[0], 1) if lead else None, lead_speed=r(lead[1], 1) if lead else None,
                   thw=thw, ttc=ttc, indicating=int(bool(probe.get('indicating'))),
                   tip=tip.get('id') if tip else '')
        self._csv.writerow(row)
        self._rows.append(row)
        if len(self._rows) % int(5 * SAMPLE_HZ) == 0:
            self._csv_file.flush()
            self._events_file.flush()

    def _finish_free_drive(self):
        self._set_phase('after')
        self._free_since = None
        free = self.write_summary()['phases'].get('free')
        if free is None or free['score'] is None:
            return
        self._result = free['score']
        self._free_duration = free['metrics']['duration_s']
        self.event('score', str(self._result['total']))

    def phase_metrics(self, phase):
        rows = [row for row in self._rows if row['phase'] == phase]
        events = [e for e in self._events if e['phase'] == phase]
        m = metrics(rows, events, prompted_features(e for e in self._events if e['phase'] == 'guided'))
        return dict(metrics=m, score=score(m) if rows else None)

    def write_summary(self):
        """Writes drive.json (keeping an earlier one under a timestamp); returns it."""
        summary = dict(started=self.stamp, sample_hz=SAMPLE_HZ,
                       free_drive_seconds=self._free_seconds,
                       phases={p: self.phase_metrics(p) for p in ('guided', 'free')
                               if any(row['phase'] == p for row in self._rows)},
                       tips=[dict(t=e['t'], kind=e['kind'], tip=e['detail'])
                             for e in self._events if e['kind'] in ('tip-shown', 'tip-done')],
                       definitions=METRICS,
                       files=dict(samples='drive-%s.csv' % self.stamp, events='events-%s.csv' % self.stamp))
        path = os.path.join(self.folder, 'drive.json')
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                earlier = json.load(f).get('started')
            if earlier and earlier != self.stamp:
                shutil.move(path, os.path.join(self.folder, 'drive-%s.json' % earlier))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
        return summary

    def close(self):
        """Writes the summary and closes the files."""
        if not self.running:
            return
        self.write_summary()
        self._csv_file.close()
        self._events_file.close()
        self._csv = self._events_csv = None
        self.phase = None
