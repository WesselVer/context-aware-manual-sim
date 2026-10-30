"""Context-aware tips: which tip the dashboard gives, and when.

The context-aware manual starts when a level is picked (B, E or P on the
keyboard: Beginner, Experienced, Expert) and stops on a long press of R;
until then the car drives as normal and no tip shows. While it runs,
Tips.update() is called once a frame with the dashboard state (what the car
and its assists are doing) and the driving context the driver script
measures in the world:

  speed          km/h
  yaw_rate       deg/s, the car turning
  cars_near      vehicles close by
  walkers_near   pedestrians close by
  junction_ahead m to a junction coming up on the road ahead, or None
  indicating     a blinker is on

It returns the values for the dashboard's tip panel and level selector.

Each level has a curriculum: its tips, in order. A tip comes up when
  - its turn has come: the tip before it is done, and its own `after`
    condition holds (cruise used for a while, say), or NEXT_ANYWAY_SECONDS
    have passed without it;
  - at least TIP_GAP_SECONDS after the last tip (the first may come
    straight away: picking the level is the driver settling in);
  - and the moment is safe (Tips.moment): standing still, steady on a
    straight, or a quiet road, with no junction coming up, no crowd of cars
    or people, and no manoeuvre (reversing, turning, indicating, braking
    hard) in the last MANOEUVRE_HOLD_SECONDS.
It stays until the driver does what it teaches (`learn`), then shows a
short "done" confirmation and goes. A tip the driver does before its turn
is skipped: they know it already.

A tip names the dashboard zones it refers to (highlight), which the page
outlines, and either the G25 control to use (control: button, tap or hold,
label), drawn on the shifter, or the movement to make (gesture), drawn too:
  zones     speed, throttle, brake, gear, cruise, acc, autopilot, driveMode, regen
  buttons   cc-onoff, cc-minus, cc-plus, cc-resume, autopilot, gap-minus, gap-plus
  gestures  release-throttle, press-brake
"""

import time


# Timing of the tips.
TIP_GAP_SECONDS = 20.0
DONE_SECONDS = 4.0
NEXT_ANYWAY_SECONDS = 120.0

# A good moment: stopped this long, steady (within the band, going straight)
# this long, or a quiet road (at most QUIET_CARS close by, going straight)
# this long.
STOPPED_KMH = 1.0
STOPPED_SECONDS = 4.0
STEADY_MIN_KMH = 15.0
STEADY_BAND_KMH = 4.0
STEADY_SECONDS = 6.0
STRAIGHT_DEG_S = 4.0
QUIET_CARS = 1
QUIET_SECONDS = 4.0

# A bad moment: this many cars or people close by, or a manoeuvre.
BUSY_CARS = 3
BUSY_WALKERS = 2
TURNING_DEG_S = 8.0
HARD_BRAKE = 50
MANOEUVRE_HOLD_SECONDS = 3.0


# ==============================================================================
# -- curricula -----------------------------------------------------------------
# ==============================================================================


BEGINNER = [
    dict(id='cc-on',
         title='Let cruise control hold your speed',
         body='At the speed you want (20 km/h or more, in D), press ON / OFF once. '
              'The car keeps that speed, so you can take your foot off the accelerator.',
         highlight=['cruise'],
         control=dict(button='cc-onoff', press='tap', label='ON / OFF'),
         action='Press once to set your current speed',
         learn='cruise-on',
         done_title='Cruise control is on',
         done_body='It holds your speed now. Press ON / OFF again to switch it off.'),
    dict(id='cc-brake',
         title='Tap the brake to switch cruise control off',
         body='Cruise control lets go the moment you touch the brake, so you are always in '
              'control. It remembers your speed: RES picks it up again.',
         highlight=['cruise', 'brake'],
         gesture='press-brake',
         action='Touch the brake pedal',
         learn='brake-cancel',
         after=lambda tips: tips.used('Cruise') >= 20.0 or tips.saw('cruise-off'),
         waiting='after some driving with cruise control',
         done_title='Cruise control let go',
         done_body='You are driving yourself again. Press RES to go back to your set speed.'),
    dict(id='acc-on',
         title='Adaptive cruise keeps your distance for you',
         body='From 20 km/h, hold ON / OFF for a second. The car speeds up to 30 km/h and '
              'slows down with the car in front, even to a stop. Brake to take over.',
         highlight=['acc'],
         control=dict(button='cc-onoff', press='hold', label='ON / OFF'),
         action='Hold for one second',
         learn='acc-on',
         done_title='Adaptive cruise is on',
         done_body='It follows the car ahead. The gap buttons set how far behind it stays.'),
    dict(id='autopilot',
         title='Let the car drive itself for a while',
         body='In D, press the autopilot button. The car steers and keeps its speed. '
              'Brake or turn the wheel to take over at any moment.',
         highlight=['autopilot', 'gear'],
         control=dict(button='autopilot', press='tap', label='AUTOPILOT'),
         action='Press once, in D',
         learn='autopilot-on',
         after=lambda tips: tips.used('ACC') >= 20.0 or tips.saw('acc-off'),
         waiting='after some driving with adaptive cruise',
         done_title='The car is driving itself',
         done_body='Keep watching the road. Brake or steer and it hands the car back to you.'),
]

# Experienced and Expert tips come next.
CURRICULA = {
    'Beginner': BEGINNER,
    'Experienced': [],
    'Expert': [],
}

LEVELS = ['Beginner', 'Experienced', 'Expert']

# What of a tip goes to the dashboard.
PAYLOAD = ('id', 'title', 'body', 'highlight', 'control', 'gesture', 'action')


def _family(assist):
    """'Cruise', 'ACC' or 'Autopilot' for an activeAssist, standby or not; None when off."""
    for name in ('Cruise', 'ACC', 'Autopilot'):
        if assist.startswith(name):
            return name
    return None


# ==============================================================================
# -- Tips ----------------------------------------------------------------------
# ==============================================================================


class Tips(object):
    def __init__(self, curricula=None, clock=time.time):
        self._curricula = curricula or CURRICULA
        self._clock = clock
        self.level = None
        self._last = None
        self._prev_assist = None
        # The moment, measured whether or not the manual runs.
        self._ctx = {}
        self._stopped_for = 0.0
        self._steady_for = 0.0
        self._steady_ref = None
        self._quiet_for = 0.0
        self._manoeuvre = None
        self._manoeuvre_until = 0.0
        self._reset()

    def _reset(self):
        self._last_tip = self._turn_since = 0.0
        self._queue = []
        self._total = 0
        self._seen = set()
        self._used = {}
        self._tip = None
        self._why = None
        self._since = 0.0
        self._done_until = None

    @property
    def running(self):
        return self.level is not None

    def start(self, level):
        """Start (or switch to) a level; returns a line for the HUD."""
        if level == self.level:
            return 'Context-aware manual: %s' % level
        self._reset()
        now = self._clock()
        self.level = level
        self._turn_since = now
        self._last_tip = now - TIP_GAP_SECONDS
        self._queue = list(self._curricula.get(level, []))
        self._total = len(self._queue)
        self._log('on, %s' % level)
        return 'Context-aware manual: %s' % level

    def stop(self):
        """Stop giving tips; returns a line for the HUD."""
        if self.level is not None:
            self._log('off')
        self.level = None
        self._reset()
        return 'Context-aware manual off'

    def used(self, assist):
        """Seconds this assist (Cruise, ACC, Autopilot) has been active since the start."""
        return self._used.get(assist, 0.0)

    def saw(self, event):
        return event in self._seen

    def update(self, state, context):
        """state: the dashboard state; context: see the module docstring.
        Returns dict(level, showTip, tip, contextStatus, tipProgress)."""
        now = self._clock()
        dt = 0.0 if self._last is None else min(now - self._last, 0.5)
        self._last = now
        self._watch(state, context, dt, now)
        assist = state.get('activeAssist', 'None')
        events = self._events(self._prev_assist, assist, state.get('brake', 0))
        self._prev_assist = assist
        if not self.running:
            return dict(level=None, showTip=False, tip=None, contextStatus=None, tipProgress=None)

        family = _family(assist)
        if family is not None and not assist.endswith('standby'):
            self._used[family] = self._used.get(family, 0.0) + dt
        self._seen.update(events)
        good, bad = self.moment()

        if self._tip is not None:
            self._follow_tip(events, now)
        # Skip what the driver already knows.
        while self._tip is None and self._queue and self._queue[0]['learn'] in self._seen:
            self._log('%s skipped, already known' % self._queue.pop(0)['id'])
            self._turn_since = now
        if self._tip is None and self._queue:
            nxt = self._queue[0]
            turn = (nxt.get('after') is None or nxt['after'](self) or
                    now - self._turn_since >= NEXT_ANYWAY_SECONDS)
            if turn and good and not bad and now - self._last_tip >= TIP_GAP_SECONDS:
                self._tip, self._since, self._why = nxt, now, good
                self._log('%s shown (%s)' % (nxt['id'], good))

        return dict(level=self.level,
                    showTip=self._tip is not None,
                    tip=self._payload(),
                    contextStatus=self._status(good, bad, now),
                    tipProgress=[self._total - len(self._queue), self._total])

    def _follow_tip(self, events, now):
        """The tip on screen stays until it is done, then shows done for DONE_SECONDS."""
        if self._done_until is not None:
            if now >= self._done_until:
                self._queue.remove(self._tip)
                self._tip = self._done_until = None
                self._last_tip = self._turn_since = now
        elif self._tip['learn'] in events:
            self._done_until = now + DONE_SECONDS
            self._log('%s done after %.0f s' % (self._tip['id'], now - self._since))

    def _payload(self):
        if self._tip is None:
            return None
        tip = {k: self._tip[k] for k in PAYLOAD if k in self._tip}
        tip['why'] = self._why
        if self._done_until is not None:
            tip.update(title=self._tip['done_title'], body=self._tip['done_body'], complete=True)
        return tip

    def _status(self, good, bad, now):
        """The line under an empty tip panel: what the manual is waiting for."""
        if self._tip is not None:
            return None
        if not self._total:
            return 'No %s tips yet' % self.level
        if not self._queue:
            return 'All %s tips given' % self.level
        nxt = self._queue[0]
        if (nxt.get('after') is not None and not nxt['after'](self) and
                now - self._turn_since < NEXT_ANYWAY_SECONDS):
            return 'Next tip %s' % nxt['waiting']
        if bad:
            return 'Not now: %s' % bad
        return 'Waiting for a calm moment'

    # -- the moment ------------------------------------------------------------

    def _watch(self, state, ctx, dt, now):
        """Keeps the timers moment() reads."""
        self._ctx = ctx
        speed = ctx.get('speed', 0.0)
        yaw = abs(ctx.get('yaw_rate', 0.0))
        manoeuvre = None
        if state.get('gear') == 'R':
            manoeuvre = 'reversing'
        elif speed > 3.0 and yaw > TURNING_DEG_S:
            manoeuvre = 'turning'
        elif ctx.get('indicating'):
            manoeuvre = 'indicating'
        elif state.get('brake', 0) >= HARD_BRAKE and speed > 5.0:
            manoeuvre = 'braking hard'
        if manoeuvre:
            self._manoeuvre, self._manoeuvre_until = manoeuvre, now + MANOEUVRE_HOLD_SECONDS

        self._stopped_for = self._stopped_for + dt if speed < STOPPED_KMH else 0.0
        straight = speed >= STEADY_MIN_KMH and yaw < STRAIGHT_DEG_S
        if straight and self._steady_ref is not None and abs(speed - self._steady_ref) <= STEADY_BAND_KMH:
            self._steady_for += dt
        else:
            self._steady_ref = speed if straight else None
            self._steady_for = 0.0
        quiet = straight and ctx.get('cars_near', 0) <= QUIET_CARS and not ctx.get('walkers_near', 0)
        self._quiet_for = self._quiet_for + dt if quiet else 0.0

    def moment(self, now=None):
        """(why it is a good moment, or None; why it is a bad one, or None)."""
        now = self._clock() if now is None else now
        ctx = self._ctx
        bad = None
        if now < self._manoeuvre_until:
            bad = self._manoeuvre
        elif ctx.get('junction_ahead') is not None:
            bad = 'junction ahead'
        elif ctx.get('cars_near', 0) >= BUSY_CARS:
            bad = '%d cars close by' % ctx['cars_near']
        elif ctx.get('walkers_near', 0) >= BUSY_WALKERS:
            bad = 'people close by'
        good = None
        if self._stopped_for >= STOPPED_SECONDS:
            good = 'Why now: you have been standing still for %d s' % self._stopped_for
        elif self._steady_for >= STEADY_SECONDS:
            good = 'Why now: steady speed on a straight road for %d s' % self._steady_for
        elif self._quiet_for >= QUIET_SECONDS:
            good = 'Why now: a quiet road, with few cars around you'
        return good, bad

    @staticmethod
    def _events(prev, assist, brake):
        """What the driver did with the assists between two frames."""
        if prev is None or prev == assist:
            return []
        was, now = _family(prev), _family(assist)
        events = []
        if now == 'Cruise' and was != 'Cruise':
            events.append('cruise-on')
        if now == 'ACC' and was != 'ACC':
            events.append('acc-on')
        if assist == 'Autopilot':
            events.append('autopilot-on')
        if prev in ('Cruise', 'ACC') and assist == prev + ' standby' and brake > 0:
            events.append('brake-cancel')
        if was == 'Cruise' and now is None:
            events.append('cruise-off')
        if was == 'ACC' and now != 'ACC':
            events.append('acc-off')
        return events

    def _log(self, text):
        print('[context %s] %s' % (time.strftime('%H:%M:%S'), text))
