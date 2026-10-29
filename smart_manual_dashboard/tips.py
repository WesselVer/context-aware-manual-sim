"""Context-aware tips for the dashboard's tip panel.

Each tip names the dashboard zones it refers to (highlight), which the page
outlines, and the gesture the driver has to make, which the page draws:
  zones     speed, throttle, brake, gear, cruise, driveMode, regen
  gestures  release-throttle

Tips.update() is called once a frame with what the car is doing and returns
the showTip and tip values for Dashboard.update().
"""

import time


SPEED_OVER_100 = dict(
    id='speed-over-100',
    title='You are driving faster than 100 km/h',
    body='Ease off the accelerator and let the car slow down. Above 100 km/h '
         'you need more room to stop, and many roads have a lower limit.',
    why='Why now: your speed passed 100 km/h',
    highlight=['speed', 'throttle'],
    gesture='release-throttle',
    action='Lift your foot off the accelerator')

# A tip stays up at least this long, so it does not flicker around its trigger.
MIN_SHOW_SECONDS = 4.0


class Tips(object):
    def __init__(self):
        self._tip = None
        self._since = 0.0

    def update(self, speed):
        """speed in km/h; returns dict(showTip, tip)."""
        now = time.time()
        if self._tip is None:
            if speed > 100:
                self._tip, self._since = SPEED_OVER_100, now
        elif speed < 97 and now - self._since > MIN_SHOW_SECONDS:
            self._tip = None
        return dict(showTip=self._tip is not None, tip=self._tip)
