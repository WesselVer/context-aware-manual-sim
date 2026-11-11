"""Context-aware tips: which tip the dashboard gives, and when.

The context-aware manual starts on B and stops on a long press of R;
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

It returns the values for the dashboard's tip panel and manual indicator.

Every driver gets the same tips (TIPS), in order, from the basics up; there
are no experience levels, as the car is new to everyone. A tip comes up when
  - its turn has come: the tip before it is done, and its own `after`
    condition holds (cruise used for a while, say), or NEXT_ANYWAY_SECONDS
    have passed without it. A `strict` tip waits for its condition however
    long it takes (resume only means something with cruise on standby), and an
    `anytime` tip may come before the tips ahead of it once its condition
    holds, so it is shown when its moment arises;
  - at least TIP_GAP_SECONDS after the last tip (the first may come
    straight away: starting the manual is the driver settling in);
  - and the moment is safe (Tips.moment): standing still, steady on a
    straight, or a quiet road, with no junction coming up, no crowd of cars
    or people, and no manoeuvre (reversing, turning, indicating, braking
    hard, though not the autopilot's braking) in the last
    MANOEUVRE_HOLD_SECONDS.
It stays until the driver does what it teaches (`learn`), then shows a
short "done" confirmation and goes. A tip the driver does before its turn
is skipped: they know it already; so is one whose `known` condition holds
when its turn comes (switching on an assist that is already on).

Tips.pause() holds the tips back until the next start, for the free drive
at the end of a user test (drive_log.py).

Beside contextStatus (one line, for the full dashboard's tip panel) it sends
manualStatus, the minimal dashboard's grey lines: `moment`, why now is or is
not a moment for a tip (or why the tip on screen was given), and `next`, what
the driver has to do before the next tip can come (a tip's `need`, falling
back to its `waiting`), or the pause between tips.

A tip that waits for the driver to use a mode first names that mode's zones
(expect); while it waits, the page marks them with a lighter outline,
beside the "Next tip when ..." line in the tip panel.

A tip names the dashboard zones it refers to (highlight), which the page
outlines, and either the G25 control to use (control: device, button or
buttons, tap or hold, label; hint and where to override the words under the
label), drawn on the shifter or the wheel, or the movement to make
(gesture), drawn too:
  zones     speed, throttle, brake, gear, cruise, acc, autopilot, driveMode, regen
  shifter   speed-up, speed-down (the silver panel's top and bottom), cc, acc, autopilot
            (the red row), dpad-up (gap -), dpad-down (gap +)
  wheel     paddle-left, paddle-right, regen-down, regen-up
  gestures  release-throttle, press-brake, press-throttle
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


def _left(seconds):
    """Whole seconds still to go, never below 1 (the condition holds at 0)."""
    return max(1, int(seconds + 0.999))


def _clause(sentence):
    """'Press CC once.' as 'press CC once', to go inside a status line."""
    return sentence[:1].lower() + sentence[1:].rstrip('.')


def _clock_text(seconds):
    """'1:40' for 100 s."""
    seconds = _left(seconds)
    return '%d:%02d' % (seconds // 60, seconds % 60)


# ==============================================================================
# -- curricula -----------------------------------------------------------------
# ==============================================================================


# The one curriculum every participant gets, basics first: cruise control
# on, off and back on, then adaptive cruise and its speed and gap, the
# autopilot and overriding it, and last what to do when driving yourself,
# the drive modes and one-pedal driving. All tips are in for now, to see in
# the user tests which ones are worth keeping.
TIPS = [
    dict(id='cc-on',
         title='Let cruise control hold your speed',
         body='At the speed you want (20 km/h or more, in D), press the CC button once. '
              'The car keeps that speed, so you can take your foot off the accelerator.',
         highlight=['cruise'],
         control=dict(button='cc', press='tap', label='CC'),
         action='Press once to set your current speed.',
         learn='cruise-on',
         known=lambda tips: tips.assist == 'Cruise',
         done_title='Cruise control is on',
         done_body='It holds your speed now. Press CC again to switch it off.'),
    dict(id='cc-brake',
         title='Tap the brake to switch cruise control off',
         body='Cruise control lets go the moment you touch the brake, so you are always in '
              'control. It remembers your speed: press CC again to resume.',
         highlight=['cruise', 'brake'],
         gesture='press-brake',
         action='Touch the brake pedal.',
         learn='brake-cancel',
         after=lambda tips: tips.used('Cruise') >= 20.0 or tips.saw('cruise-off'),
         waiting='after some driving with cruise control',
         need=lambda tips: 'drive %d s more with cruise control on (or switch it off)' % _left(20.0 - tips.used('Cruise')),
         expect=['cruise'],
         done_title='Cruise control let go',
         done_body='You are driving yourself again. Press CC again to resume your set speed.'),
    dict(id='cc-resume',
         title='Pick up your speed again after braking',
         body='Braking put cruise control on standby. Press CC again to resume the speed '
              'you had set.',
         highlight=['cruise'],
         control=dict(button='cc', press='tap', label='CC'),
         action='Press CC once.',
         learn='resume',
         after=lambda tips: tips.assist == 'Cruise standby',
         strict=True, anytime=True,
         waiting='after you brake with cruise control on',
         need='brake while cruise control is on',
         expect=['cruise'],
         done_title='Back to your set speed',
         done_body='Cruise control holds it again.'),
    dict(id='cc-adjust',
         title='Change your cruise speed in 5 km/h steps',
         body='While cruise control is on, press the top button to go 5 km/h faster, or the '
              'bottom one to go 5 km/h slower.',
         highlight=['cruise'],
         control=dict(button=['speed-up', 'speed-down'], press='tap', label='SPEED + / −'),
         action='Press the top or bottom button once.',
         learn='speed-adjust',
         after=lambda tips: tips.assist == 'Cruise',
         strict=True,
         waiting='when cruise control is on',
         need='switch cruise control on',
         expect=['cruise'],
         done_title='Set speed changed',
         done_body='The car eases to the new speed. Each press is 5 km/h.'),
    dict(id='acc-on',
         title='Adaptive cruise keeps your distance for you',
         body='From 20 km/h, press the ACC button. The car speeds up to 30 km/h and '
              'slows down for the car ahead, even to a stop. Brake to take over.',
         highlight=['acc'],
         control=dict(button='acc', press='tap', label='ACC'),
         action='Press ACC once.',
         learn='acc-on',
         known=lambda tips: tips.assist == 'ACC',
         done_title='Adaptive cruise is on',
         done_body='It follows the car ahead, down to a stop. Brake to take over.'),
    dict(id='acc-speed',
         title='Tune the speed adaptive cruise aims for',
         body='The top and bottom buttons move its set speed in 5 km/h steps. It still slows '
              'down for the car ahead.',
         highlight=['acc'],
         control=dict(button=['speed-up', 'speed-down'], press='tap', label='SPEED + / −'),
         action='Press the top or bottom button once.',
         learn='acc-speed-adjust',
         after=lambda tips: tips.assist == 'ACC',
         strict=True, anytime=True,
         waiting='when adaptive cruise is on',
         need='switch adaptive cruise on',
         expect=['acc'],
         done_title='Adaptive cruise speed changed',
         done_body='It aims for the new speed whenever the road ahead is clear.'),
    dict(id='acc-gap',
         title='Choose how close adaptive cruise follows',
         body='The d-pad sets the time gap to the car ahead: up for a shorter gap (1 s at the least), '
              'down for a longer one (2.5 s at the most).',
         highlight=['acc'],
         control=dict(button=['dpad-up', 'dpad-down'], press='tap', label='GAP', hint='UP: CLOSER',
                      where='down: more room'),
         action='Press the d-pad up or down.',
         learn='gap-change',
         after=lambda tips: tips.assist == 'ACC',
         strict=True, anytime=True,
         waiting='when adaptive cruise is on',
         need='switch adaptive cruise on',
         expect=['acc'],
         done_title='Following gap changed',
         done_body='The bars on the adaptive cruise card show the gap you picked.'),
    dict(id='autopilot',
         title='Let the car drive itself for a while',
         body='In D, press the autopilot button. The car steers and keeps its speed. '
              'Brake or turn the wheel to take over at any moment.',
         highlight=['autopilot', 'gear'],
         control=dict(button='autopilot', press='tap', label='AUTOPILOT'),
         action='Press once, in D.',
         learn='autopilot-on',
         known=lambda tips: tips.assist == 'Autopilot',
         after=lambda tips: tips.used('ACC') >= 20.0 or tips.saw('acc-off'),
         waiting='after some driving with adaptive cruise',
         need=lambda tips: 'drive %d s more with adaptive cruise on (or switch it off)' % _left(20.0 - tips.used('ACC')),
         expect=['acc'],
         done_title='The car is driving itself',
         done_body='Keep watching the road. Brake or steer and it hands the car back to you.'),
    dict(id='ap-speed',
         title='Set how fast the autopilot drives',
         body='With the autopilot on, the top and bottom buttons change its speed in 5 km/h '
              'steps. It starts at 60 km/h.',
         highlight=['autopilot'],
         control=dict(button=['speed-up', 'speed-down'], press='tap', label='SPEED + / −'),
         action='Press the top or bottom button once.',
         learn='ap-speed',
         after=lambda tips: tips.assist == 'Autopilot',
         strict=True, anytime=True,
         waiting='when the autopilot is driving',
         need='switch the autopilot on',
         expect=['autopilot'],
         done_title='Autopilot speed changed',
         done_body='It drives at the new speed. Brake or steer to take over at any moment.'),
    dict(id='ap-override',
         title='Speed past the autopilot with the accelerator',
         body='With the autopilot on, press the accelerator to go faster for a moment, for example '
              'to merge. It keeps steering and drops back to its own speed when you let go.',
         highlight=['autopilot', 'throttle'],
         gesture='press-throttle',
         action='Press the accelerator.',
         learn='ap-override',
         after=lambda tips: tips.assist == 'Autopilot',
         strict=True, anytime=True,
         waiting='when the autopilot is driving',
         need='switch the autopilot on',
         expect=['autopilot'],
         done_title='You overrode the autopilot',
         done_body='Let go of the pedal and it returns to its set speed.'),
    dict(id='drive-mode',
         title='Wake the car up with Dynamic mode',
         body='Pull the right paddle behind the wheel to step up to Dynamic, for a sharper '
              'throttle. The left paddle steps back down, to Normal and Eco.',
         highlight=['driveMode'],
         control=dict(device='wheel', button='paddle-right', press='tap', label='PADDLE +',
                      hint='PULL ONCE', where='behind the wheel'),
         action='Pull the right paddle.',
         learn='mode-dynamic',
         after=lambda tips: tips.driving_yourself,
         strict=True,
         waiting='when you drive yourself, with no assist on',
         need='drive yourself, with no assist on',
         done_title='Dynamic mode is on',
         done_body='The throttle answers sooner. Pull the left paddle to go back to Normal.'),
    dict(id='regen-max',
         title='Drive with one pedal',
         body='Press the right button on the wheel until regen is at Maximum. Lift off the '
              'accelerator and the car slows firmly, almost to a stop, without the brake.',
         highlight=['regen'],
         control=dict(device='wheel', button='regen-up', press='tap', label='REGEN +',
                      hint='PRESS TO MAX', where='right, on the wheel'),
         action='Press until regen shows Maximum.',
         learn='regen-max',
         after=lambda tips: tips.driving_yourself,
         strict=True,
         waiting='when you drive yourself, with no assist on',
         need='drive yourself, with no assist on',
         done_title='One-pedal driving',
         done_body='Ease off early to slow down. The left button turns regen back down.'),
]

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
    def __init__(self, tips=None, clock=time.time):
        self._tips = tips or TIPS
        self._clock = clock
        self._on = False
        self._last = None
        self._prev = None
        # The activeAssist this frame, for the tips' `after` conditions.
        self.assist = 'None'
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
        # The free drive at the end of a test (drive_log.py): no tips.
        self.paused = False

    @property
    def running(self):
        return self._on

    def start(self):
        """Start the manual from its first tip; returns a line for the HUD."""
        if self._on:
            return 'Context-aware manual is on'
        self._reset()
        now = self._clock()
        self._on = True
        self._turn_since = now
        self._last_tip = now - TIP_GAP_SECONDS
        self._queue = list(self._tips)
        self._total = len(self._queue)
        self._log('on')
        return 'Context-aware manual on'

    def stop(self):
        """Stop giving tips; returns a line for the HUD."""
        if self._on:
            self._log('off')
        self._on = False
        self._reset()
        return 'Context-aware manual off'

    def pause(self):
        """Holds the tips back (the tip on screen goes) until the next start."""
        if self.running and not self.paused:
            if self._tip is not None and self._done_until is not None:
                self._queue.remove(self._tip)
            self._tip = self._done_until = None
            self.paused = True
            self._log('paused')

    def skip(self):
        """For testing: drops the tip on screen, or the one the manual waits
        for, and lets the next come at its moment; returns its id or None."""
        if not self.running or self.paused:
            return None
        tip = self._tip or (self._queue[0] if self._queue else None)
        if tip is None:
            return None
        now = self._clock()
        self._queue.remove(tip)
        self._tip = self._done_until = None
        self._last_tip = now - TIP_GAP_SECONDS
        self._turn_since = now
        self._log('%s skipped by the tester' % tip['id'])
        return tip['id']

    @property
    def driving_yourself(self):
        """No assist holds the speed, so the driver feels the throttle and regen."""
        return _family(self.assist) is None or self.assist.endswith('standby')

    def used(self, assist):
        """Seconds this assist (Cruise, ACC, Autopilot) has been active since the start."""
        return self._used.get(assist, 0.0)

    def saw(self, event):
        return event in self._seen

    def update(self, state, context):
        """state: the dashboard state; context: see the module docstring.
        Returns dict(manual, showTip, tip, contextStatus, manualStatus, tipProgress, expect)."""
        now = self._clock()
        dt = 0.0 if self._last is None else min(now - self._last, 0.5)
        self._last = now
        self._watch(state, context, dt, now)
        assist = self.assist = state.get('activeAssist', 'None')
        events = self._events(self._prev, state)
        self._prev = dict(state)
        if not self.running:
            return dict(manual=False, showTip=False, tip=None, contextStatus=None, manualStatus=None,
                        tipProgress=None, expect=None)

        family = _family(assist)
        if family is not None and not assist.endswith('standby'):
            self._used[family] = self._used.get(family, 0.0) + dt
        self._seen.update(events)
        if self.paused:
            return dict(manual=True, showTip=False, tip=None, contextStatus=None, manualStatus=None,
                        tipProgress=None, expect=None)
        good, bad = self.moment()

        if self._tip is not None:
            self._follow_tip(events, now)
        # Skip what the driver already knows.
        if self._tip is None:
            for tip in [t for t in self._queue
                        if t['learn'] in self._seen or (t.get('known') and t['known'](self))]:
                self._log('%s skipped, already known' % tip['id'])
                self._queue.remove(tip)
                self._turn_since = now
        if self._tip is None and self._queue and good and not bad and now - self._last_tip >= TIP_GAP_SECONDS:
            # The next tip, or an anytime tip further on whose moment has come.
            for nxt in self._queue[:1] + [t for t in self._queue[1:] if t.get('anytime')]:
                if self._turn(nxt, now):
                    self._tip, self._since, self._why = nxt, now, good
                    self._log('%s shown (%s)' % (nxt['id'], good))
                    break

        return dict(manual=True,
                    showTip=self._tip is not None,
                    tip=self._payload(),
                    contextStatus=self._status(good, bad, now),
                    manualStatus=self._manual_status(good, bad, now),
                    tipProgress=[self._total - len(self._queue), self._total],
                    expect=self._expect(now))

    def _expect(self, now):
        """The zones of the mode the next tip waits for the driver to use, or None."""
        if self._tip is not None or not self._queue or self._turn(self._queue[0], now):
            return None
        return self._queue[0].get('expect')

    def _turn(self, tip, now):
        """Whether the tip's turn has come: its condition, or waiting long enough."""
        if tip.get('after') is None or tip['after'](self):
            return True
        return not tip.get('strict') and now - self._turn_since >= NEXT_ANYWAY_SECONDS

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
            return 'No tips yet'
        if not self._queue:
            return 'All tips given'
        nxt = self._queue[0]
        if not self._turn(nxt, now):
            return 'Next tip %s' % nxt['waiting']
        if bad:
            return 'Not now: %s' % bad
        return 'Waiting for a calm moment'

    def _manual_status(self, good, bad, now):
        """The minimal dashboard's lines: dict(moment, next), either None."""
        if self._tip is not None:
            if self._done_until is not None:
                return dict(moment=None, next='Next tip at least %d s after this one' % TIP_GAP_SECONDS)
            why = (self._why or '').replace('Why now: ', '')
            return dict(moment='Picked this moment: %s' % why if why else None,
                        next='This tip stays until you do it: %s' % _clause(self._tip.get('action', '')))
        if not self._total:
            return dict(moment=None, next='No tips yet')
        if not self._queue:
            return dict(moment=None, next='All tips given, drive as you like')

        nxt = self._queue[0]
        ready = any(self._turn(t, now) for t in self._queue[:1] + [t for t in self._queue[1:] if t.get('anytime')])
        if ready:
            need = None
        else:
            need = nxt.get('need') or nxt.get('waiting', '')
            if callable(need):
                need = need(self)
            need = 'Next tip when you %s' % need if nxt.get('need') else 'Next tip %s' % need
            if not nxt.get('strict'):
                need += ', or in %s anyway' % _clock_text(NEXT_ANYWAY_SECONDS - (now - self._turn_since))
        pause = TIP_GAP_SECONDS - (now - self._last_tip)
        if need is None and pause > 0:
            need = 'Short pause after the last tip: %d s' % _left(pause)

        if bad:
            moment = 'Not a good moment for a tip: %s' % bad
        elif good:
            moment = 'Good moment for a tip: %s' % good.replace('Why now: ', '')
        else:
            moment = 'Waiting for a calm moment: %s' % self._moment_hint()
        return dict(moment=moment, next=need)

    def _moment_hint(self):
        """How far the driver is from a good moment, for _manual_status."""
        if self._ctx.get('speed', 0.0) < STOPPED_KMH:
            return 'standing still, %d of %d s' % (self._stopped_for, STOPPED_SECONDS)
        if self._steady_for > 0.0:
            return 'steady speed on a straight road, %d of %d s' % (self._steady_for, STEADY_SECONDS)
        if self._quiet_for > 0.0:
            return 'a quiet straight road, %d of %d s' % (self._quiet_for, QUIET_SECONDS)
        return 'keep a steady speed on a straight road, or stop'

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
        elif (state.get('brake', 0) >= HARD_BRAKE and speed > 5.0 and
              state.get('activeAssist') != 'Autopilot'):
            # The autopilot brakes hard as a matter of course; that is no
            # manoeuvre of the driver's.
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
    def _events(prev_state, state):
        """What the driver did with the assists and drive modes between two frames."""
        if prev_state is None:
            return []
        prev = prev_state.get('activeAssist', 'None')
        assist = state.get('activeAssist', 'None')
        events = []
        # The same assist, holding a speed: its speed changed with + or -.
        if assist == prev and assist in ('Cruise', 'ACC') and \
                state.get('setSpeed') != prev_state.get('setSpeed'):
            events.append('speed-adjust')
            if assist == 'ACC':
                events.append('acc-speed-adjust')
        if assist == prev == 'Autopilot' and state.get('apTarget') != prev_state.get('apTarget'):
            events.append('ap-speed')
        if assist == prev and assist in ('ACC', 'Autopilot') and state.get('accGap') != prev_state.get('accGap'):
            events.append('gap-change')
        if assist == prev == 'Autopilot' and state.get('throttleOverride') and \
                not prev_state.get('throttleOverride'):
            events.append('ap-override')
        # Regen up to Maximum with its buttons; a drive mode sets it too.
        if (state.get('regenLevel') == 3 != prev_state.get('regenLevel') and
                state.get('driveMode') == prev_state.get('driveMode')):
            events.append('regen-max')
        # The autopilot changes the mode when it comes on and goes off.
        if (state.get('driveMode') == 'Dynamic' != prev_state.get('driveMode') and
                'Autopilot' not in (prev, assist)):
            events.append('mode-dynamic')
        if prev == assist:
            return events
        was, now = _family(prev), _family(assist)
        if prev.endswith('standby') and assist == was:
            events.append('resume')
        # On is holding the speed, however it got there: switched on, or
        # resumed from standby (an assist left on standby before the tip came
        # up could otherwise never be switched on for it).
        if assist == 'Cruise' and prev != 'Cruise':
            events.append('cruise-on')
        if assist == 'ACC' and prev != 'ACC':
            events.append('acc-on')
        if assist == 'Autopilot':
            events.append('autopilot-on')
        if prev in ('Cruise', 'ACC') and assist == prev + ' standby' and state.get('brake', 0) > 0:
            events.append('brake-cancel')
        if was == 'Cruise' and now is None:
            events.append('cruise-off')
        if was == 'ACC' and now != 'ACC':
            events.append('acc-off')
        return events

    def _log(self, text):
        print('[context %s] %s' % (time.strftime('%H:%M:%S'), text))
