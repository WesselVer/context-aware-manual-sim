#!/usr/bin/env python

# Copyright (c) 2019 Computer Vision Center (CVC) at the Universitat Autonoma de
# Barcelona (UAB).
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""Allows controlling a vehicle with a keyboard, seen from the driver's seat.

manual_control.py from the CARLA 0.10.0 release, changed to start in a
driver's-eye camera, full screen on the primary monitor (--windowed for a
window of --res). Backspace cycles the cars in DRIVER_EYES (Lincoln MKZ,
Dodge Charger, Nissan Patrol, Ford taxi, Mini Cooper), each with a tuned eye
position; Tab cycles the camera views. Pass --filter for the original
random-car behaviour.

A second window opens with the smart-manual dashboard (smart_manual_dashboard/),
full screen on the second monitor, showing the car's speed. --no-dashboard
turns it off.

A Logitech G25 wheel, when plugged in, drives the car (steering, throttle,
brake, clutch) with force feedback (steering_wheel.py); the keyboard keeps
working beside it. --wheel picks another wheel by name, --no-wheel ignores it.
CARLA has no clutch, so pressing it disengages the throttle. The H-shifter is
an automatic's selector: 1st P, 3rd D, 4th R, anywhere else N.

Drive modes Eco, Normal (the default) and Sport shape the throttle; the left
paddle steps down, the right one up. Eco also makes the wheel's force
feedback 40% lighter. Each mode sets the regenerative braking
(Eco Maximum, Normal Low, Sport Off), which the wheel's top buttons then
adjust: left down, right up. CARLA has no regen, so it is brake applied on
lift-off. The dashboard shows the mode, regen, throttle and brake.

Cruise control, the plain kind that holds a speed with the throttle and never
brakes, is on the wheel's cruise buttons: on/off switches it on at the current
speed (20 km/h or more, in D), + and - change the set speed by 5 km/h, and
resume returns to the last set speed. Braking, the clutch, the hand brake or
leaving D cancels it; the throttle overrides it while pressed.

Adaptive cruise control (ACC) is a long press of on/off. It starts at the
road's speed limit (30 km/h on a map without speed signs, such as Town10HD),
follows the car ahead in your lane at the time gap set with gap - and +
(1.0, 1.5, 2.0 or 2.5 s; 1.5 s to start), and brakes, down to a stop. After
more than 3 s stopped it waits for resume or the throttle. The same +, -,
resume and cancels apply.

Autopilot (the wheel's autopilot button, or P) drives the car around the map
on CARLA's Traffic Manager, slowly (30 km/h to start) and in Eco; the wheel
turns with the car. + and - change its speed, gap - and + its following
distance. Braking, leaving D, or turning the wheel against it (it resists
at first) hands the car back, in the drive mode it had before.
"""

"""
Welcome to CARLA manual control.

Use ARROWS or WASD keys for control.

    W            : throttle
    S            : brake
    A/D          : steer left/right
    Q            : toggle reverse
    Space        : hand-brake
    P            : toggle autopilot
    M            : toggle manual transmission
    ,/.          : gear up/down
    CTRL + W     : toggle constant velocity mode at 60 km/h

    L            : toggle next light type
    SHIFT + L    : toggle high beam
    Z/X          : toggle right/left blinker
    I            : toggle interior light

    TAB          : change sensor position
    ` or N       : next sensor
    [1-9]        : change to sensor [1-9]
    G            : toggle radar visualization
    C            : change weather (Shift+C reverse)
    Backspace    : change vehicle

    O            : open/close all doors of vehicle
    T            : toggle vehicle's telemetry

    V            : Select next map layer (Shift+V reverse)
    B            : Load current selected map layer (Shift+B to unload)

    R            : toggle recording images to disk

    CTRL + R     : toggle recording of simulation (replacing any previous)
    CTRL + P     : start replaying last recorded simulation
    CTRL + +     : increments the start time of the replay by 1 second (+SHIFT = 10 seconds)
    CTRL + -     : decrements the start time of the replay by 1 second (+SHIFT = 10 seconds)

    F1           : toggle HUD
    H/?          : toggle help
    ESC          : quit
"""

# ==============================================================================
# -- imports -------------------------------------------------------------------
# ==============================================================================

import carla

from carla import ColorConverter as cc

import argparse
import collections
import datetime
import logging
import math
import random
import time
import re
import os
import weakref

try:
    import pygame
    from pygame.locals import KMOD_CTRL
    from pygame.locals import KMOD_SHIFT
    from pygame.locals import K_0
    from pygame.locals import K_9
    from pygame.locals import K_BACKQUOTE
    from pygame.locals import K_BACKSPACE
    from pygame.locals import K_COMMA
    from pygame.locals import K_DOWN
    from pygame.locals import K_ESCAPE
    from pygame.locals import K_F1
    from pygame.locals import K_LEFT
    from pygame.locals import K_PERIOD
    from pygame.locals import K_RIGHT
    from pygame.locals import K_SLASH
    from pygame.locals import K_SPACE
    from pygame.locals import K_TAB
    from pygame.locals import K_UP
    from pygame.locals import K_a
    from pygame.locals import K_b
    from pygame.locals import K_c
    from pygame.locals import K_d
    from pygame.locals import K_f
    from pygame.locals import K_g
    from pygame.locals import K_h
    from pygame.locals import K_i
    from pygame.locals import K_l
    from pygame.locals import K_m
    from pygame.locals import K_n
    from pygame.locals import K_o
    from pygame.locals import K_p
    from pygame.locals import K_q
    from pygame.locals import K_r
    from pygame.locals import K_s
    from pygame.locals import K_t
    from pygame.locals import K_v
    from pygame.locals import K_w
    from pygame.locals import K_x
    from pygame.locals import K_z
    from pygame.locals import K_MINUS
    from pygame.locals import K_EQUALS
except ImportError:
    raise RuntimeError('cannot import pygame, make sure pygame package is installed')

try:
    import numpy as np
except ImportError:
    raise RuntimeError('cannot import numpy, make sure numpy package is installed')

from smart_manual_dashboard import Dashboard, focus_window
from smart_manual_dashboard.tips import Tips
from steering_wheel import (SteeringWheel, PADDLE_LEFT, PADDLE_RIGHT,
                            WHEEL_BUTTON_LEFT, WHEEL_BUTTON_RIGHT,
                            CRUISE_ON_OFF, CRUISE_DOWN, CRUISE_RESUME, CRUISE_UP,
                            GAP_DOWN, GAP_UP, AUTOPILOT_BUTTON)

OBJECT_TO_COLOR = [
    (255, 255, 255),
    (128, 64, 128),
    (244, 35, 232),
    (70, 70, 70),
    (102, 102, 156),
    (190, 153, 153),
    (153, 153, 153),
    (250, 170, 30),
    (220, 220, 0),
    (107, 142,  35),
    (152, 251, 152),
    (70, 130, 180),
    (220, 20, 60),
    (255, 0, 0),
    (0, 0, 142),
    (0, 0, 70),
    (0,  60, 100),
    (0,  80, 100),
    (0, 0, 230),
    (119, 11, 32),
    (110, 190, 160),
    (170, 120, 50),
    (55, 90, 80),
    (45, 60, 150),
    (157, 234, 50),
    (81, 0, 81),
    (150, 100, 100),
    (230, 150, 140),
    (180, 165, 180),
]

# ==============================================================================
# -- Global functions ----------------------------------------------------------
# ==============================================================================


def find_weather_presets():
    rgx = re.compile('.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$)')
    name = lambda x: ' '.join(m.group(0) for m in rgx.finditer(x))
    presets = [x for x in dir(carla.WeatherParameters) if re.match('[A-Z].+', x)]
    return [(getattr(carla.WeatherParameters, x), name(x)) for x in presets]


def get_actor_display_name(actor, truncate=250):
    name = ' '.join(actor.type_id.replace('_', '.').title().split('.')[1:])
    return (name[:truncate - 1] + u'\u2026') if len(name) > truncate else name

def get_actor_blueprints(world, filter, generation):
    bps = world.get_blueprint_library().filter(filter)

    if generation.lower() == "all":
        return bps

    # If the filter returns only one bp, we assume that this one needed
    # and therefore, we ignore the generation
    if len(bps) == 1:
        return bps

    try:
        int_generation = int(generation)
        # Check if generation is in available generations
        if int_generation in [1, 2, 3, 4]:
            bps = [x for x in bps if int(x.get_attribute('generation')) == int_generation]
            return bps
        else:
            print("   Warning! Actor Generation is not valid. No actor will be spawned.")
            return []
    except:
        print("   Warning! Actor Generation is not valid. No actor will be spawned.")
        return []


# Cars Backspace cycles through, in order, each with a driver's-eye camera
# position (metres, vehicle frame: x forward, y right, z up) tuned by eye so
# the windshield, steering wheel and dashboard are all in view.
DRIVER_EYES = [
    ('vehicle.lincoln.mkz', carla.Location(x=-0.25, y=-0.41, z=1.18)),
    ('vehicle.dodge.charger', carla.Location(x=-0.40, y=-0.42, z=1.24)),
    ('vehicle.nissan.patrol', carla.Location(x=-0.35, y=-0.48, z=1.68)),
    ('vehicle.taxi.ford', carla.Location(x=0.00, y=-0.40, z=1.08)),
    ('vehicle.mini.cooper', carla.Location(x=-0.35, y=-0.45, z=1.30)),
]


# Drive modes the paddles step through: the throttle response, the regen
# level the mode starts with, and the force-feedback strength. Eco caps the
# throttle at 60% with a soft start and lightens the wheel by 40%, so the
# switch is felt; Sport answers early in the pedal's travel.
DriveMode = collections.namedtuple('DriveMode', 'name throttle regen ffb')
DRIVE_MODES = [
    DriveMode('Eco', lambda pedal: 0.6 * pedal ** 1.6, 3, 0.6),
    DriveMode('Normal', lambda pedal: pedal, 1, 1.0),
    DriveMode('Sport', lambda pedal: pedal ** 0.7, 0, 1.0),
]
DEFAULT_DRIVE_MODE = 1

# Regenerative braking, which CARLA lacks, as brake applied when the
# throttle is released: Off, Low, Medium, Maximum (about 0.5 g).
REGEN_BRAKE = [0.0, 0.12, 0.25, 0.5]
REGEN_NAMES = ['Off', 'Low', 'Medium', 'Maximum']

# Cruise control: the lowest speed it holds, the step of + and -, and the
# PI gains from the speed error (km/h) to throttle.
CRUISE_MIN_KMH = 20
CRUISE_MAX_KMH = 180
CRUISE_STEP_KMH = 5
CRUISE_KP = 0.08
CRUISE_KI = 0.03
# Held this long, on/off turns on adaptive cruise instead.
LONG_PRESS_SECONDS = 0.6

# Adaptive cruise: time gaps for gap 1-4, the distance kept at a standstill,
# and how far ahead it looks.
ACC_GAPS = [1.0, 1.5, 2.0, 2.5]
ACC_DEFAULT_GAP = 2
ACC_STANDSTILL_M = 5.0
ACC_RANGE_M = 100.0
# How hard it closes a gap error (m/s of speed per m), the brake per km/h
# over the target, and the deceleration a full brake gives.
ACC_GAP_GAIN = 0.3
ACC_BRAKE_PER_KMH = 0.04
ACC_FULL_BRAKE_MS2 = 9.0
ACC_MAX_BRAKE = 0.8
# Stopped longer than this, it waits for resume or the throttle to go again.
ACC_AUTO_GO_SECONDS = 3.0

# Autopilot: its starting speed and range (km/h), the drive mode it drives
# in, and the takeover: the brake pedal past AUTOPILOT_BRAKE, or the wheel
# held AUTOPILOT_TAKEOVER_DEG away from the car's steering for
# AUTOPILOT_TAKEOVER_SECONDS without coming back.
AUTOPILOT_KMH = 30
AUTOPILOT_MIN_KMH = 10
AUTOPILOT_MAX_KMH = 90
AUTOPILOT_DRIVE_MODE = 0
AUTOPILOT_BRAKE = 0.1
AUTOPILOT_TAKEOVER_DEG = 40.0
AUTOPILOT_TAKEOVER_SECONDS = 0.25


class CruiseControl(object):
    """Cruise control, plain or adaptive.

    Plain cruise holds a set speed with the throttle alone and never brakes.
    Adaptive cruise (ACC) also follows the car ahead at the time gap, braking
    when it has to, down to a stop.

    Off, or on: holding the speed (active) or waiting to (standby, after a
    cancel). The set speed is kept through a cancel, for resume.
    """

    def __init__(self):
        self.on = False
        self.active = False
        self.adaptive = False
        self.set_speed = None
        self.gap = ACC_DEFAULT_GAP
        # (gap in m, speed in km/h) of the car ahead, or None; set by the caller.
        self.lead = None
        # Stopped behind a car for longer than ACC_AUTO_GO_SECONDS.
        self.waiting = False
        self._stopped_for = 0.0
        self._integral = 0.0

    @property
    def name(self):
        return 'ACC' if self.adaptive else 'Cruise'

    def toggle(self, kmh, throttle):
        """A short press of on/off: plain cruise on, or either one off."""
        if self.on:
            name = self.name
            self.on = self.active = self.adaptive = self.waiting = False
            self.set_speed = None
            return '%s off' % name
        self.on = True
        return self._engage(kmh, int(round(kmh)), throttle)

    def adaptive_on(self, kmh, throttle, default_speed):
        """A long press of on/off: adaptive cruise at default_speed."""
        self.on = self.adaptive = True
        self.waiting = False
        return self._engage(kmh, default_speed, throttle)

    def step_gap(self, step):
        self.gap = max(1, min(len(ACC_GAPS), self.gap + step))
        return 'Gap %d (%.1f s)' % (self.gap, ACC_GAPS[self.gap - 1])

    def change(self, step, kmh, throttle):
        """+ or -: steps the set speed; on standby, sets the current speed."""
        if not self.on:
            return None
        if not self.active:
            return self._engage(kmh, int(round(kmh)), throttle)
        # To the next multiple of the step, so 47 goes to 50 or 45.
        if step > 0:
            target = (self.set_speed // step + 1) * step
        else:
            target = -(-self.set_speed // -step) * -step + step
        self.set_speed = max(CRUISE_MIN_KMH, min(CRUISE_MAX_KMH, target))
        return '%s %d km/h' % (self.name, self.set_speed)

    def resume(self, kmh, throttle):
        if self.active and self.waiting:
            self.waiting = False
            return 'ACC go'
        if not self.on or self.active:
            return None
        if self.set_speed is None:
            return self._engage(kmh, int(round(kmh)), throttle)
        return self._engage(kmh, self.set_speed, throttle)

    def cancel(self):
        if self.active:
            self.active = self.waiting = False
            return '%s cancelled' % self.name
        return None

    def _engage(self, kmh, target, throttle):
        # ACC also starts behind a car, slow or stopped; plain cruise needs speed.
        if kmh < CRUISE_MIN_KMH and not (self.adaptive and self.lead is not None):
            self.active = False
            if self.adaptive:
                self.set_speed = max(CRUISE_MIN_KMH, min(CRUISE_MAX_KMH, target))
            return '%s standby: needs %d km/h' % (self.name, CRUISE_MIN_KMH)
        self.active = True
        self.set_speed = max(CRUISE_MIN_KMH, min(CRUISE_MAX_KMH, target))
        # Starts from the throttle in use, so engaging does not lurch.
        self._integral = throttle
        self._stopped_for = 0.0
        return '%s %d km/h' % (self.name, self.set_speed)

    def control(self, kmh, dt, hold=False):
        """(throttle, brake) for this frame; hold is a driver override on the throttle."""
        target = self.set_speed
        brake = 0.0
        if self.adaptive:
            target, brake = self._follow(kmh, dt, hold)
            if self.waiting:
                return 0.0, 0.4
        if brake > 0.0:
            # Braking: the throttle's integral bleeds off rather than winding up.
            self._integral = max(0.0, self._integral - 0.6 * dt)
            return 0.0, brake
        error = target - kmh
        if not hold:
            self._integral = max(0.0, min(1.0, self._integral + CRUISE_KI * error * dt))
        return max(0.0, min(1.0, self._integral + CRUISE_KP * error)), 0.0

    def _follow(self, kmh, dt, hold):
        """Adaptive cruise: (target km/h, brake) from the set speed and the car ahead."""
        target = float(self.set_speed)
        brake = 0.0
        if self.lead is not None:
            gap, lead_kmh = self.lead
            v, v_lead = kmh / 3.6, lead_kmh / 3.6
            wanted = ACC_STANDSTILL_M + ACC_GAPS[self.gap - 1] * v
            # The speed that closes the gap error over a few seconds.
            target = min(target, 3.6 * max(0.0, v_lead + ACC_GAP_GAIN * (gap - wanted)))
            # Closing in: the deceleration that matches its speed before the gap is gone.
            closing = v - v_lead
            if closing > 0.0:
                needed = closing ** 2 / (2.0 * max(1.0, gap - 0.5 * ACC_STANDSTILL_M))
                brake = needed / ACC_FULL_BRAKE_MS2
            # Behind a stopped car: stop ACC_STANDSTILL_M short of it.
            if v_lead < 1.0 and v > 0.0:
                needed = v ** 2 / (2.0 * max(0.5, gap - ACC_STANDSTILL_M))
                brake = max(brake, 1.5 * needed / ACC_FULL_BRAKE_MS2)
        # Above the target (a slower car, a lower set speed, downhill); a
        # light brake is left to the engine.
        over = ACC_BRAKE_PER_KMH * max(0.0, kmh - target - 3.0)
        brake = max(brake, over if over >= 0.05 else 0.0)
        if kmh < 1.0 and target < 3.0:
            brake = max(brake, 0.4)  # hold the car at a standstill
        if kmh < 1.0:
            self._stopped_for += dt
            if self._stopped_for > ACC_AUTO_GO_SECONDS and not hold:
                self.waiting = True
        else:
            self._stopped_for = 0.0
        if hold:
            self.waiting = False
        return target, min(ACC_MAX_BRAKE, brake)

    @property
    def state(self):
        """The dashboard's activeAssist: Cruise, ACC, either with standby, or None."""
        if not self.on:
            return 'None'
        return self.name if self.active else self.name + ' standby'


class LeadFinder(object):
    """The car ahead in the ego car's lane, for adaptive cruise control.

    Follows the ego's lane forward along the map's waypoints (at a junction,
    the straightest way on) and takes the nearest vehicle on that path.
    """

    STEP = 2.5

    def __init__(self, world):
        self._world = world
        self._map = world.get_map()
        self._next = 0.0
        self._lead = None

    def update(self, ego):
        """(gap in m, speed along the lane in km/h) of the car ahead, or None; 20 Hz."""
        now = time.time()
        if now >= self._next:
            self._next = now + 0.05
            self._lead = self._find(ego)
        return self._lead

    def _find(self, ego):
        here = ego.get_location()
        wp = self._map.get_waypoint(here)
        if wp is None:
            return None
        path = [wp]
        for _ in range(int(ACC_RANGE_M / self.STEP)):
            ahead = path[-1].next(self.STEP)
            if not ahead:
                break
            yaw = path[-1].transform.rotation.yaw
            path.append(min(ahead, key=lambda w: abs((w.transform.rotation.yaw - yaw + 180.0) % 360.0 - 180.0)))
        points = []
        for i, w in enumerate(path):
            f = w.transform.get_forward_vector()
            points.append((w.transform.location.x, w.transform.location.y, f.x, f.y,
                           i * self.STEP, math.hypot(self.STEP / 2.0, 0.4 * w.lane_width)))
        best = None
        reach = (ACC_RANGE_M + 10.0) ** 2
        for other in self._world.get_actors().filter('vehicle.*'):
            if other.id == ego.id:
                continue
            at = other.get_location()
            if (at.x - here.x) ** 2 + (at.y - here.y) ** 2 > reach:
                continue
            x, y, fx, fy, along, radius = min(points, key=lambda p: (at.x - p[0]) ** 2 + (at.y - p[1]) ** 2)
            if math.hypot(at.x - x, at.y - y) > radius:
                continue
            along += (at.x - x) * fx + (at.y - y) * fy
            gap = along - ego.bounding_box.extent.x - other.bounding_box.extent.x
            if along <= 0.0 or (best is not None and gap >= best[0]):
                continue
            v = other.get_velocity()
            best = (max(0.0, gap), 3.6 * (v.x * fx + v.y * fy))
        return best


def _speed_kmh(vehicle):
    v = vehicle.get_velocity()
    return 3.6 * math.sqrt(v.x**2 + v.y**2 + v.z**2)


def driver_eye(vehicle):
    """Tuned eye position for the cars above, an estimate from the bounding box otherwise."""
    for type_id, eye in DRIVER_EYES:
        if vehicle.type_id == type_id:
            return eye
    # Left seat (left-hand drive), a little behind the box centre, at about
    # 3/4 of the body height.
    bb = vehicle.bounding_box
    return carla.Location(x=bb.location.x - 0.10 * bb.extent.x,
                          y=-0.45 * bb.extent.y,
                          z=bb.location.z + 0.55 * bb.extent.z)


# ==============================================================================
# -- World ---------------------------------------------------------------------
# ==============================================================================


class World(object):
    def __init__(self, carla_world, hud, traffic_manager, args):
        self.world = carla_world
        self.sync = args.sync
        self.traffic_manager = traffic_manager
        self.actor_role_name = args.rolename
        try:
            self.map = self.world.get_map()
        except RuntimeError as error:
            print('RuntimeError: {}'.format(error))
            print('  The server could not send the OpenDRIVE (.xodr) file:')
            print('  Make sure it exists, has the same name of your town, and is correct.')
            sys.exit(1)
        self.hud = hud
        self.player = None
        self.collision_sensor = None
        self.lane_invasion_sensor = None
        self.gnss_sensor = None
        self.imu_sensor = None
        self.radar_sensor = None
        self.camera_manager = None
        self._weather_presets = find_weather_presets()
        self._weather_index = 0
        self._actor_filter = args.filter
        self._actor_generation = args.generation
        self._driver_car_index = -1
        self.remove_leftover_players()
        self._gamma = args.gamma
        self.restart()
        self.world.on_tick(hud.on_world_tick)
        self.recording_enabled = False
        self.recording_start = 0
        self.constant_velocity_enabled = False
        self.show_vehicle_telemetry = False
        self.doors_are_open = False
        self.current_map_layer = 0
        self.map_layer_names = [
            carla.MapLayer.NONE,
            carla.MapLayer.Buildings,
            carla.MapLayer.Decals,
            carla.MapLayer.Foliage,
            carla.MapLayer.Ground,
            carla.MapLayer.ParkedVehicles,
            carla.MapLayer.Particles,
            carla.MapLayer.Props,
            carla.MapLayer.StreetLights,
            carla.MapLayer.Walls,
            carla.MapLayer.All
        ]

    def remove_leftover_players(self):
        """Destroy cars with our role name left by a client that did not exit cleanly.

        Their sensors go first: a sensor left attached to a destroyed car
        crashes the 0.10.0 server (EXCEPTION_ACCESS_VIOLATION).
        """
        actors = self.world.get_actors()
        for car in actors.filter('vehicle.*'):
            if car.attributes.get('role_name') != self.actor_role_name:
                continue
            for sensor in actors.filter('sensor.*'):
                if sensor.parent is not None and sensor.parent.id == car.id:
                    if hasattr(sensor, 'stop'):
                        sensor.stop()
                    sensor.destroy()
            car.destroy()

    def restart(self):
        self.player_max_speed = 1.589
        self.player_max_speed_fast = 3.713
        # Keep same camera config if the camera manager exists.
        cam_index = self.camera_manager.index if self.camera_manager is not None else 0
        cam_pos_index = self.camera_manager.transform_index if self.camera_manager is not None else 0
        if self._actor_filter is None:
            # Step to the next car in DRIVER_EYES.
            self._driver_car_index = (self._driver_car_index + 1) % len(DRIVER_EYES)
            blueprint = self.world.get_blueprint_library().find(DRIVER_EYES[self._driver_car_index][0])
        else:
            # Get a random blueprint.
            blueprint_list = get_actor_blueprints(self.world, self._actor_filter, self._actor_generation)
            if not blueprint_list:
                raise ValueError("Couldn't find any blueprints with the specified filters")
            blueprint = random.choice(blueprint_list)
        blueprint.set_attribute('role_name', self.actor_role_name)
        if blueprint.has_attribute('terramechanics'):
            blueprint.set_attribute('terramechanics', 'true')
        if blueprint.has_attribute('color'):
            color = random.choice(blueprint.get_attribute('color').recommended_values)
            blueprint.set_attribute('color', color)
        if blueprint.has_attribute('driver_id'):
            driver_id = random.choice(blueprint.get_attribute('driver_id').recommended_values)
            blueprint.set_attribute('driver_id', driver_id)
        if blueprint.has_attribute('is_invincible'):
            blueprint.set_attribute('is_invincible', 'true')
        # set the max speed
        if blueprint.has_attribute('speed'):
            self.player_max_speed = float(blueprint.get_attribute('speed').recommended_values[1])
            self.player_max_speed_fast = float(blueprint.get_attribute('speed').recommended_values[2])

        # Spawn the player.
        if self.player is not None:
            spawn_point = self.player.get_transform()
            spawn_point.location.z += 2.0
            spawn_point.rotation.roll = 0.0
            spawn_point.rotation.pitch = 0.0
            self.destroy()
            self.player = self.world.try_spawn_actor(blueprint, spawn_point)
            self.show_vehicle_telemetry = False
            self.modify_vehicle_physics(self.player)
        while self.player is None:
            if not self.map.get_spawn_points():
                print('There are no spawn points available in your map/town.')
                print('Please add some Vehicle Spawn Point to your UE5 scene.')
                sys.exit(1)
            spawn_points = self.map.get_spawn_points()
            spawn_point = random.choice(spawn_points) if spawn_points else carla.Transform()
            self.player = self.world.try_spawn_actor(blueprint, spawn_point)
            self.show_vehicle_telemetry = False
            self.modify_vehicle_physics(self.player)
        # Set up the sensors.
        self.collision_sensor = CollisionSensor(self.player, self.hud)
        self.lane_invasion_sensor = LaneInvasionSensor(self.player, self.hud)
        self.gnss_sensor = GnssSensor(self.player)
        self.imu_sensor = IMUSensor(self.player)
        self.camera_manager = CameraManager(self.player, self.hud, self._gamma)
        self.camera_manager.transform_index = cam_pos_index
        self.camera_manager.set_sensor(cam_index, notify=False)
        actor_type = get_actor_display_name(self.player)
        self.hud.notification(actor_type)
        self.traffic_manager.update_vehicle_lights(self.player, True)

        if self.sync:
            self.world.tick()
        else:
            self.world.wait_for_tick()

    def next_weather(self, reverse=False):
        self._weather_index += -1 if reverse else 1
        self._weather_index %= len(self._weather_presets)
        preset = self._weather_presets[self._weather_index]
        self.hud.notification('Weather: %s' % preset[1])
        self.player.get_world().set_weather(preset[0])

    def next_map_layer(self, reverse=False):
        self.current_map_layer += -1 if reverse else 1
        self.current_map_layer %= len(self.map_layer_names)
        selected = self.map_layer_names[self.current_map_layer]
        self.hud.notification('LayerMap selected: %s' % selected)

    def load_map_layer(self, unload=False):
        selected = self.map_layer_names[self.current_map_layer]
        if unload:
            self.hud.notification('Unloading map layer: %s' % selected)
            self.world.unload_map_layer(selected)
        else:
            self.hud.notification('Loading map layer: %s' % selected)
            self.world.load_map_layer(selected)

    def toggle_radar(self):
        if self.radar_sensor is None:
            self.radar_sensor = RadarSensor(self.player)
        elif self.radar_sensor.sensor is not None:
            self.radar_sensor.sensor.destroy()
            self.radar_sensor = None

    def modify_vehicle_physics(self, actor):
        #If actor is not a vehicle, we cannot use the physics control
        try:
            physics_control = actor.get_physics_control()
            physics_control.use_sweep_wheel_collision = True
            actor.apply_physics_control(physics_control)
        except Exception:
            pass

    def tick(self, clock):
        self.hud.tick(self, clock)

    def render(self, display):
        self.camera_manager.render(display)
        self.hud.render(display)

    def destroy_sensors(self):
        self.camera_manager.sensor.destroy()
        self.camera_manager.sensor = None
        self.camera_manager.index = None

    def destroy(self):
        if self.radar_sensor is not None:
            self.toggle_radar()
        sensors = [
            self.camera_manager.sensor,
            self.collision_sensor.sensor,
            self.lane_invasion_sensor.sensor,
            self.gnss_sensor.sensor,
            self.imu_sensor.sensor]
        for sensor in sensors:
            if sensor is not None:
                sensor.stop()
                sensor.destroy()
        if self.player is not None:
            self.player.destroy()


# ==============================================================================
# -- KeyboardControl -----------------------------------------------------------
# ==============================================================================


class KeyboardControl(object):
    """Class that handles keyboard input."""
    def __init__(self, world, start_in_autopilot, wheel=None):
        self._autopilot_enabled = start_in_autopilot
        self._wheel = wheel
        # --ffb-gain, which each drive mode scales.
        self._ffb_gain = wheel.ffb_gain if wheel is not None else 1.0
        self._drive_mode = DEFAULT_DRIVE_MODE
        self._regen = DRIVE_MODES[self._drive_mode].regen
        self._key_throttle = 0.0
        self._clutch = 0.0
        self._cruise = CruiseControl()
        self._cruise_override = False
        self._leads = None
        self._ap_speed = AUTOPILOT_KMH
        # The drive mode to go back to when the driver takes over.
        self._mode_before_autopilot = None
        # Since when the wheel has been held against the autopilot, and how far.
        self._ap_pull_since = None
        self._ap_pull_deg = 0.0
        # The control the Traffic Manager last applied, for the dashboard.
        self._ap_control = None
        # When on/off went down, and whether its long press has acted yet.
        self._on_off_down = None
        self._on_off_long = False
        self._applied_throttle = 0.0
        self._cruise_throttle = 0.0
        # The throttle asked for, before the drive mode shapes it.
        self.pedal = 0.0
        self._ackermann_enabled = False
        self._ackermann_reverse = 1
        if isinstance(world.player, carla.Vehicle):
            self._control = carla.VehicleControl()
            self._ackermann_control = carla.VehicleAckermannControl()
            self._lights = carla.VehicleLightState.NONE
            world.player.set_autopilot(self._autopilot_enabled, world.traffic_manager.get_port())
            if self._autopilot_enabled:
                self._configure_autopilot(world)
            world.player.set_light_state(self._lights)
        elif isinstance(world.player, carla.Walker):
            self._control = carla.WalkerControl()
            self._autopilot_enabled = False
            self._rotation = world.player.get_transform().rotation
        else:
            raise NotImplementedError("Actor type not supported")
        self._steer_cache = 0.0
        world.hud.notification("Press 'H' or '?' for help.", seconds=4.0)

    def parse_events(self, client, world, clock, sync_mode):
        if isinstance(self._control, carla.VehicleControl):
            current_lights = self._lights
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
            elif event.type == pygame.KEYUP:
                if self._is_quit_shortcut(event.key):
                    return True
                elif event.key == K_BACKSPACE:
                    if self._autopilot_enabled:
                        world.player.set_autopilot(False, world.traffic_manager.get_port())
                        world.restart()
                        world.player.set_autopilot(True, world.traffic_manager.get_port())
                        self._configure_autopilot(world)
                    else:
                        world.restart()
                elif event.key == K_F1:
                    world.hud.toggle_info()
                elif event.key == K_v and pygame.key.get_mods() & KMOD_SHIFT:
                    world.next_map_layer(reverse=True)
                elif event.key == K_v:
                    world.next_map_layer()
                elif event.key == K_b and pygame.key.get_mods() & KMOD_SHIFT:
                    world.load_map_layer(unload=True)
                elif event.key == K_b:
                    world.load_map_layer()
                elif event.key == K_h or (event.key == K_SLASH and pygame.key.get_mods() & KMOD_SHIFT):
                    world.hud.help.toggle()
                elif event.key == K_TAB:
                    world.camera_manager.toggle_camera()
                elif event.key == K_c and pygame.key.get_mods() & KMOD_SHIFT:
                    world.next_weather(reverse=True)
                elif event.key == K_c:
                    world.next_weather()
                elif event.key == K_g:
                    world.toggle_radar()
                elif event.key == K_BACKQUOTE:
                    world.camera_manager.next_sensor()
                elif event.key == K_n:
                    world.camera_manager.next_sensor()
                elif event.key == K_w and (pygame.key.get_mods() & KMOD_CTRL):
                    if world.constant_velocity_enabled:
                        world.player.disable_constant_velocity()
                        world.constant_velocity_enabled = False
                        world.hud.notification("Disabled Constant Velocity Mode")
                    else:
                        world.player.enable_constant_velocity(carla.Vector3D(17, 0, 0))
                        world.constant_velocity_enabled = True
                        world.hud.notification("Enabled Constant Velocity Mode at 60 km/h")
                elif event.key == K_o:
                    try:
                        if world.doors_are_open:
                            world.hud.notification("Closing Doors")
                            world.doors_are_open = False
                            world.player.close_door(carla.VehicleDoor.All)
                        else:
                            world.hud.notification("Opening doors")
                            world.doors_are_open = True
                            world.player.open_door(carla.VehicleDoor.All)
                    except Exception:
                        pass
                elif event.key == K_t:
                    if world.show_vehicle_telemetry:
                        world.player.show_debug_telemetry(False)
                        world.show_vehicle_telemetry = False
                        world.hud.notification("Disabled Vehicle Telemetry")
                    else:
                        try:
                            world.player.show_debug_telemetry(True)
                            world.show_vehicle_telemetry = True
                            world.hud.notification("Enabled Vehicle Telemetry")
                        except Exception:
                            pass
                elif event.key > K_0 and event.key <= K_9:
                    index_ctrl = 0
                    if pygame.key.get_mods() & KMOD_CTRL:
                        index_ctrl = 9
                    world.camera_manager.set_sensor(event.key - 1 - K_0 + index_ctrl)
                elif event.key == K_r and not (pygame.key.get_mods() & KMOD_CTRL):
                    world.camera_manager.toggle_recording()
                elif event.key == K_r and (pygame.key.get_mods() & KMOD_CTRL):
                    if (world.recording_enabled):
                        client.stop_recorder()
                        world.recording_enabled = False
                        world.hud.notification("Recorder is OFF")
                    else:
                        client.start_recorder("manual_recording.rec")
                        world.recording_enabled = True
                        world.hud.notification("Recorder is ON")
                elif event.key == K_p and (pygame.key.get_mods() & KMOD_CTRL):
                    # stop recorder
                    client.stop_recorder()
                    world.recording_enabled = False
                    # work around to fix camera at start of replaying
                    current_index = world.camera_manager.index
                    world.destroy_sensors()
                    # disable autopilot
                    self._autopilot_enabled = False
                    world.player.set_autopilot(self._autopilot_enabled, world.traffic_manager.get_port())
                    world.hud.notification("Replaying file 'manual_recording.rec'")
                    # replayer
                    client.replay_file("manual_recording.rec", world.recording_start, 0, 0)
                    world.camera_manager.set_sensor(current_index)
                elif event.key == K_MINUS and (pygame.key.get_mods() & KMOD_CTRL):
                    if pygame.key.get_mods() & KMOD_SHIFT:
                        world.recording_start -= 10
                    else:
                        world.recording_start -= 1
                    world.hud.notification("Recording start time is %d" % (world.recording_start))
                elif event.key == K_EQUALS and (pygame.key.get_mods() & KMOD_CTRL):
                    if pygame.key.get_mods() & KMOD_SHIFT:
                        world.recording_start += 10
                    else:
                        world.recording_start += 1
                    world.hud.notification("Recording start time is %d" % (world.recording_start))
                if isinstance(self._control, carla.VehicleControl):
                    if event.key == K_f:
                        # Toggle ackermann controller
                        self._ackermann_enabled = not self._ackermann_enabled
                        world.hud.show_ackermann_info(self._ackermann_enabled)
                        world.hud.notification("Ackermann Controller %s" %
                                               ("Enabled" if self._ackermann_enabled else "Disabled"))
                    if event.key == K_q:
                        if not self._ackermann_enabled:
                            self._control.gear = 1 if self._control.reverse else -1
                        else:
                            self._ackermann_reverse *= -1
                            # Reset ackermann control
                            self._ackermann_control = carla.VehicleAckermannControl()
                    elif event.key == K_m:
                        self._control.manual_gear_shift = not self._control.manual_gear_shift
                        self._control.gear = world.player.get_control().gear
                        world.hud.notification('%s Transmission' %
                                               ('Manual' if self._control.manual_gear_shift else 'Automatic'))
                    elif self._control.manual_gear_shift and event.key == K_COMMA:
                        self._control.gear = max(-1, self._control.gear - 1)
                    elif self._control.manual_gear_shift and event.key == K_PERIOD:
                        self._control.gear = self._control.gear + 1
                    elif event.key == K_p and not pygame.key.get_mods() & KMOD_CTRL:
                        self._set_autopilot(world, not self._autopilot_enabled)
                    elif event.key == K_l and pygame.key.get_mods() & KMOD_CTRL:
                        current_lights ^= carla.VehicleLightState.Special1
                    elif event.key == K_l and pygame.key.get_mods() & KMOD_SHIFT:
                        current_lights ^= carla.VehicleLightState.HighBeam
                    elif event.key == K_l:
                        # Use 'L' key to switch between lights:
                        # closed -> position -> low beam -> fog
                        if not self._lights & carla.VehicleLightState.Position:
                            world.hud.notification("Position lights")
                            current_lights |= carla.VehicleLightState.Position
                        else:
                            world.hud.notification("Low beam lights")
                            current_lights |= carla.VehicleLightState.LowBeam
                        if self._lights & carla.VehicleLightState.LowBeam:
                            world.hud.notification("Fog lights")
                            current_lights |= carla.VehicleLightState.Fog
                        if self._lights & carla.VehicleLightState.Fog:
                            world.hud.notification("Lights off")
                            current_lights ^= carla.VehicleLightState.Position
                            current_lights ^= carla.VehicleLightState.LowBeam
                            current_lights ^= carla.VehicleLightState.Fog
                    elif event.key == K_i:
                        current_lights ^= carla.VehicleLightState.Interior
                    elif event.key == K_z:
                        current_lights ^= carla.VehicleLightState.LeftBlinker
                    elif event.key == K_x:
                        current_lights ^= carla.VehicleLightState.RightBlinker

        if self._autopilot_enabled and isinstance(self._control, carla.VehicleControl):
            self._parse_autopilot(world, pygame.key.get_pressed(), clock.get_time() / 1000.0)

        if not self._autopilot_enabled:
            if isinstance(self._control, carla.VehicleControl):
                self._parse_vehicle_keys(pygame.key.get_pressed(), clock.get_time())
                if self._wheel is not None and not self._ackermann_enabled:
                    self._parse_wheel(world)
                if not self._ackermann_enabled:
                    self._apply_drive_mode(world, clock.get_time() / 1000.0)
                self._control.reverse = self._control.gear < 0
                # Set automatic control-related vehicle lights
                if self._control.brake:
                    current_lights |= carla.VehicleLightState.Brake
                else: # Remove the Brake flag
                    current_lights &= ~carla.VehicleLightState.Brake
                if self._control.reverse:
                    current_lights |= carla.VehicleLightState.Reverse
                else: # Remove the Reverse flag
                    current_lights &= ~carla.VehicleLightState.Reverse
                if current_lights != self._lights: # Change the light state only if necessary
                    world.player.set_light_state(carla.VehicleLightState(current_lights))
                # Apply control
                if not self._ackermann_enabled:
                    world.player.apply_control(self._control)
                else:
                    world.player.apply_ackermann_control(self._ackermann_control)
                    # Update control to the last one applied by the ackermann controller.
                    self._control = world.player.get_control()
                    # Update hud with the newest ackermann control
                    world.hud.update_ackermann_control(self._ackermann_control)

            elif isinstance(self._control, carla.WalkerControl):
                self._parse_walker_keys(pygame.key.get_pressed(), clock.get_time(), world)
                world.player.apply_control(self._control)

        self._lights = current_lights

    def _parse_vehicle_keys(self, keys, milliseconds):
        if keys[K_UP] or keys[K_w]:
            if not self._ackermann_enabled:
                # Kept apart, so the drive mode's shaping does not feed back.
                self._key_throttle = min(self._key_throttle + 0.1, 1.00)
                self._control.throttle = self._key_throttle
            else:
                self._ackermann_control.speed += round(milliseconds * 0.005, 2) * self._ackermann_reverse
        else:
            if not self._ackermann_enabled:
                self._key_throttle = 0.0
                self._control.throttle = 0.0

        if keys[K_DOWN] or keys[K_s]:
            if not self._ackermann_enabled:
                self._control.brake = min(self._control.brake + 0.2, 1)
            else:
                self._ackermann_control.speed -= min(abs(self._ackermann_control.speed), round(milliseconds * 0.005, 2)) * self._ackermann_reverse
                self._ackermann_control.speed = max(0, abs(self._ackermann_control.speed)) * self._ackermann_reverse
        else:
            if not self._ackermann_enabled:
                self._control.brake = 0

        steer_increment = 5e-4 * milliseconds
        if keys[K_LEFT] or keys[K_a]:
            if self._steer_cache > 0:
                self._steer_cache = 0
            else:
                self._steer_cache -= steer_increment
        elif keys[K_RIGHT] or keys[K_d]:
            if self._steer_cache < 0:
                self._steer_cache = 0
            else:
                self._steer_cache += steer_increment
        else:
            self._steer_cache = 0.0
        self._steer_cache = min(0.7, max(-0.7, self._steer_cache))
        if not self._ackermann_enabled:
            self._control.steer = round(self._steer_cache, 1)
            self._control.hand_brake = keys[K_SPACE]
        else:
            self._ackermann_control.steer = round(self._steer_cache, 1)

    def _parse_wheel(self, world):
        """Wheel and pedals on top of the keys: steering keys win, pedals add."""
        for button in self._wheel.pressed():
            if button in (PADDLE_LEFT, PADDLE_RIGHT):
                step = 1 if button == PADDLE_RIGHT else -1
                mode = max(0, min(len(DRIVE_MODES) - 1, self._drive_mode + step))
                if mode != self._drive_mode:
                    self._set_drive_mode(mode)
                    world.hud.notification('%s mode' % DRIVE_MODES[mode].name)
            elif button in (WHEEL_BUTTON_LEFT, WHEEL_BUTTON_RIGHT):
                step = 1 if button == WHEEL_BUTTON_RIGHT else -1
                self._regen = max(0, min(len(REGEN_BRAKE) - 1, self._regen + step))
                world.hud.notification('Regen %s' % REGEN_NAMES[self._regen])
            elif button == CRUISE_ON_OFF:
                # Acts on release (short) or once held long enough (long).
                self._on_off_down, self._on_off_long = time.time(), False
            elif button in (CRUISE_DOWN, CRUISE_RESUME, CRUISE_UP):
                self._cruise_button(button, world)
            elif button in (GAP_DOWN, GAP_UP):
                world.hud.notification(self._cruise.step_gap(1 if button == GAP_UP else -1))
            elif button == AUTOPILOT_BUTTON:
                self._set_autopilot(world, True)
                return
        if self._on_off_down is not None:
            if self._wheel.is_down(CRUISE_ON_OFF):
                if not self._on_off_long and time.time() - self._on_off_down >= LONG_PRESS_SECONDS:
                    self._on_off_long = True
                    self._cruise_button(CRUISE_ON_OFF, world, long_press=True)
            else:
                if not self._on_off_long:
                    self._cruise_button(CRUISE_ON_OFF, world)
                self._on_off_down = None
        steer, throttle, brake, clutch = self._wheel.read(world.player)
        if self._steer_cache == 0.0:
            self._control.steer = steer
        # No clutch in CARLA: pressing it takes the engine off the wheels.
        self._clutch = clutch
        self._control.throttle = max(self._control.throttle, throttle) * (1.0 - clutch)
        self._control.brake = max(self._control.brake, brake)
        selected = self._wheel.gear()
        if selected is None:
            return
        # D and R drive CARLA's automatic gearbox; N is the manual gearbox's
        # neutral; P, which CARLA lacks, is neutral with the hand brake on.
        self._control.manual_gear_shift = selected in ('P', 'N')
        self._control.gear = {'D': 1, 'R': -1}.get(selected, 0)
        if selected == 'P':
            self._control.throttle = 0.0
            self._control.hand_brake = True

    def _in_drive(self):
        return not self._control.manual_gear_shift and self._control.gear >= 0

    def _set_autopilot(self, world, enabled, reason=None):
        """Turns the autopilot on (in D only) or off; reason says why it handed over."""
        if enabled == self._autopilot_enabled:
            return
        if enabled and isinstance(self._control, carla.VehicleControl) and not self._in_drive():
            world.hud.notification('Autopilot needs D')
            return
        self._autopilot_enabled = enabled
        # Cruise control has no part in it, either way.
        self._cruise.on = self._cruise.active = self._cruise.adaptive = self._cruise.waiting = False
        self._ap_pull_since = None
        self._cruise_override = False
        tm_port = world.traffic_manager.get_port()
        if enabled:
            world.player.set_autopilot(True, tm_port)
            self._configure_autopilot(world)
            self._mode_before_autopilot = self._drive_mode
            self._set_drive_mode(AUTOPILOT_DRIVE_MODE)
            world.hud.notification('Autopilot on: %d km/h' % self._ap_speed)
        else:
            world.player.set_autopilot(False, tm_port)
            if self._mode_before_autopilot is not None:
                self._set_drive_mode(self._mode_before_autopilot)
                self._mode_before_autopilot = None
            self._ap_control = None
            world.hud.notification('Autopilot off' + (': ' + reason if reason else ''))

    def _configure_autopilot(self, world):
        """The Traffic Manager's speed and following distance for the ego car."""
        tm = world.traffic_manager
        tm.set_desired_speed(world.player, float(self._ap_speed))
        tm.distance_to_leading_vehicle(world.player, self._ap_distance)
        tm.auto_lane_change(world.player, True)

    @property
    def _ap_distance(self):
        """Following distance (m): the ACC gap's time at the autopilot's speed."""
        return ACC_STANDSTILL_M + ACC_GAPS[self._cruise.gap - 1] * self._ap_speed / 3.6

    def _set_drive_mode(self, mode):
        self._drive_mode = mode
        self._regen = DRIVE_MODES[mode].regen
        if self._wheel is not None:
            self._wheel.ffb_gain = self._ffb_gain * DRIVE_MODES[mode].ffb

    def _parse_autopilot(self, world, keys, dt):
        """While the car drives itself: its buttons, and the driver taking over."""
        self._ap_control = world.player.get_control()
        brake = 1.0 if keys[K_DOWN] or keys[K_s] else 0.0
        if self._wheel is None:
            if brake:
                self._set_autopilot(world, False, 'brake')
            return
        for button in self._wheel.pressed():
            if button == AUTOPILOT_BUTTON:
                self._set_autopilot(world, False)
                return
            if button in (CRUISE_UP, CRUISE_DOWN):
                step = CRUISE_STEP_KMH if button == CRUISE_UP else -CRUISE_STEP_KMH
                self._ap_speed = max(AUTOPILOT_MIN_KMH, min(AUTOPILOT_MAX_KMH, self._ap_speed + step))
                self._configure_autopilot(world)
                world.hud.notification('Autopilot %d km/h' % self._ap_speed)
            elif button in (GAP_DOWN, GAP_UP):
                message = self._cruise.step_gap(1 if button == GAP_UP else -1)
                self._configure_autopilot(world)
                world.hud.notification('%s, %d m' % (message, self._ap_distance))
            elif button in (PADDLE_LEFT, PADDLE_RIGHT):
                step = 1 if button == PADDLE_RIGHT else -1
                mode = max(0, min(len(DRIVE_MODES) - 1, self._drive_mode + step))
                if mode != self._drive_mode:
                    self._set_drive_mode(mode)
                    world.hud.notification('%s mode' % DRIVE_MODES[mode].name)
        _, throttle, pedal_brake, _ = self._wheel.read(world.player)
        self.pedal = throttle
        if max(brake, pedal_brake) > AUTOPILOT_BRAKE:
            self._set_autopilot(world, False, 'brake')
            return
        selected = self._wheel.gear()
        if selected is not None and selected != 'D':
            self._set_autopilot(world, False, 'shifted to %s' % selected)
            return
        # Turned against it: the wheel lagging the car's steering comes back
        # towards it, a hand holding it does not.
        pull = abs(self._wheel.deviation(world.player, self._ap_control.steer))
        if pull < AUTOPILOT_TAKEOVER_DEG:
            self._ap_pull_since = None
        elif self._ap_pull_since is None:
            self._ap_pull_since, self._ap_pull_deg = 0.0, pull
        elif pull < self._ap_pull_deg - 10.0:
            self._ap_pull_since = None  # coming back to the car's steering: lag
        else:
            self._ap_pull_since += dt
            self._ap_pull_deg = max(self._ap_pull_deg, pull)
            if self._ap_pull_since >= AUTOPILOT_TAKEOVER_SECONDS:
                self._set_autopilot(world, False, 'steering')

    def _lead_finder(self, world):
        if self._leads is None:
            self._leads = LeadFinder(world.world)
        return self._leads

    def _cruise_button(self, button, world, long_press=False):
        kmh = _speed_kmh(world.player)
        throttle = self._applied_throttle
        on = self._cruise.on
        if button != CRUISE_ON_OFF and not on:
            return
        if not self._in_drive() and (button != CRUISE_ON_OFF or not on or long_press):
            world.hud.notification('%s needs D' % ('ACC' if long_press else self._cruise.name))
            return
        if long_press:
            self._cruise.lead = self._lead_finder(world).update(world.player)
            # The limit of the road, from the last speed sign; 30 km/h on a
            # map without signs, such as Town10HD.
            limit = int(round(world.player.get_speed_limit()))
            message = self._cruise.adaptive_on(kmh, throttle, limit)
        elif button == CRUISE_ON_OFF:
            message = self._cruise.toggle(kmh, throttle)
        elif button == CRUISE_RESUME:
            message = self._cruise.resume(kmh, throttle)
        else:
            step = CRUISE_STEP_KMH if button == CRUISE_UP else -CRUISE_STEP_KMH
            message = self._cruise.change(step, kmh, throttle)
        if message:
            world.hud.notification(message)

    def _apply_drive_mode(self, world, dt):
        """Shapes the throttle for the drive mode, adds cruise control, and
        brakes for regen on lift-off."""
        self.pedal = self._control.throttle
        self._control.throttle = DRIVE_MODES[self._drive_mode].throttle(self.pedal)
        kmh = _speed_kmh(world.player)
        self._cruise_override = False
        if self._cruise.adaptive:
            self._cruise.lead = self._lead_finder(world).update(world.player)
        else:
            self._cruise.lead = None
        if self._cruise.active:
            # Braking, or taking the drive away, cancels it; the set speed stays for resume.
            if (self._control.brake > 0.02 or self._clutch > 0.5 or
                    self._control.hand_brake or not self._in_drive()):
                world.hud.notification(self._cruise.cancel())
            else:
                hold = self.pedal > 0.02 and self._control.throttle > self._cruise_throttle
                self._cruise_throttle, cruise_brake = self._cruise.control(kmh, dt, hold)
                self._cruise_override = self._control.throttle > self._cruise_throttle
                if self._cruise_override:
                    cruise_brake = 0.0  # the driver's foot wins over ACC braking
                self._control.throttle = max(self._control.throttle, self._cruise_throttle)
                self._control.brake = cruise_brake
        self._applied_throttle = self._control.throttle
        neutral = self._control.manual_gear_shift and self._control.gear == 0
        if (self.pedal > 0.02 or neutral or self._clutch > 0.5 or
                self._control.hand_brake or self._cruise.active):
            return
        # Eases off below 13 km/h, so the car rolls to a stop rather than lurching.
        regen = REGEN_BRAKE[self._regen] * min(1.0, max(0.0, (kmh - 3.0) / 10.0))
        self._control.brake = max(self._control.brake, regen)

    def dashboard_state(self):
        """The drivetrain values the dashboard shows."""
        # On autopilot the Traffic Manager's control is the one applied.
        control = self._ap_control if self._autopilot_enabled and self._ap_control else self._control
        return dict(gear=self.selected_gear,
                    driveMode=DRIVE_MODES[self._drive_mode].name,
                    regenLevel=self._regen,
                    driverPedal=int(round(100 * self.pedal)),
                    appliedThrottle=int(round(100 * control.throttle)),
                    brake=int(round(100 * control.brake)),
                    activeAssist='Autopilot' if self._autopilot_enabled else self._cruise.state,
                    apTarget=self._ap_speed,
                    apDistance=int(round(self._ap_distance)),
                    setSpeed=self._cruise.set_speed,
                    accGap=self._cruise.gap,
                    accLead=(int(round(self._cruise.lead[0]))
                             if self._cruise.adaptive and self._cruise.lead else None),
                    accWaiting=self._cruise.waiting,
                    throttleOverride=self._cruise_override)

    @property
    def selected_gear(self):
        """'P', 'R', 'N' or 'D' as the dashboard shows it."""
        if self._wheel is not None and self._wheel.gear() is not None:
            return self._wheel.gear()
        if self._control.reverse:
            return 'R'
        if self._control.manual_gear_shift and self._control.gear == 0:
            return 'N'
        return 'D'

    @property
    def autopilot_enabled(self):
        return self._autopilot_enabled

    def _parse_walker_keys(self, keys, milliseconds, world):
        self._control.speed = 0.0
        if keys[K_DOWN] or keys[K_s]:
            self._control.speed = 0.0
        if keys[K_LEFT] or keys[K_a]:
            self._control.speed = .01
            self._rotation.yaw -= 0.08 * milliseconds
        if keys[K_RIGHT] or keys[K_d]:
            self._control.speed = .01
            self._rotation.yaw += 0.08 * milliseconds
        if keys[K_UP] or keys[K_w]:
            self._control.speed = world.player_max_speed_fast if pygame.key.get_mods() & KMOD_SHIFT else world.player_max_speed
        self._control.jump = keys[K_SPACE]
        self._rotation.yaw = round(self._rotation.yaw, 1)
        self._control.direction = self._rotation.get_forward_vector()

    @staticmethod
    def _is_quit_shortcut(key):
        return (key == K_ESCAPE) or (key == K_q and pygame.key.get_mods() & KMOD_CTRL)


# ==============================================================================
# -- HUD -----------------------------------------------------------------------
# ==============================================================================


class HUD(object):
    def __init__(self, width, height):
        self.dim = (width, height)
        font = pygame.font.Font(pygame.font.get_default_font(), 20)
        font_name = 'courier' if os.name == 'nt' else 'mono'
        fonts = [x for x in pygame.font.get_fonts() if font_name in x]
        default_font = 'ubuntumono'
        mono = default_font if default_font in fonts else fonts[0]
        mono = pygame.font.match_font(mono)
        self._font_mono = pygame.font.Font(mono, 12 if os.name == 'nt' else 14)
        self._notifications = FadingText(font, (width, 40), (0, height - 40))
        self.help = HelpText(pygame.font.Font(mono, 16), width, height)
        self.server_fps = 0
        self.frame = 0
        self.simulation_time = 0
        self._show_info = True
        self._info_text = []
        self._server_clock = pygame.time.Clock()

        self._show_ackermann_info = False
        self._ackermann_control = carla.VehicleAckermannControl()

    def on_world_tick(self, timestamp):
        self._server_clock.tick()
        self.server_fps = self._server_clock.get_fps()
        self.frame = timestamp.frame
        self.simulation_time = timestamp.elapsed_seconds

    def tick(self, world, clock):
        self._notifications.tick(world, clock)
        if not self._show_info:
            return
        t = world.player.get_transform()
        v = world.player.get_velocity()
        c = world.player.get_control()
        compass = world.imu_sensor.compass
        heading = 'N' if compass > 270.5 or compass < 89.5 else ''
        heading += 'S' if 90.5 < compass < 269.5 else ''
        heading += 'E' if 0.5 < compass < 179.5 else ''
        heading += 'W' if 180.5 < compass < 359.5 else ''
        colhist = world.collision_sensor.get_collision_history()
        collision = [colhist[x + self.frame - 200] for x in range(0, 200)]
        max_col = max(1.0, max(collision))
        collision = [x / max_col for x in collision]
        vehicles = world.world.get_actors().filter('vehicle.*')
        self._info_text = [
            'Server:  % 16.0f FPS' % self.server_fps,
            'Client:  % 16.0f FPS' % clock.get_fps(),
            '',
            'Vehicle: % 20s' % get_actor_display_name(world.player, truncate=20),
            'Map:     % 20s' % world.map.name.split('/')[-1],
            'Simulation time: % 12s' % datetime.timedelta(seconds=int(self.simulation_time)),
            '',
            'Speed:   % 15.0f km/h' % (3.6 * math.sqrt(v.x**2 + v.y**2 + v.z**2)),
            u'Compass:% 17.0f\N{DEGREE SIGN} % 2s' % (compass, heading),
            'Accelero: (%5.1f,%5.1f,%5.1f)' % (world.imu_sensor.accelerometer),
            'Gyroscop: (%5.1f,%5.1f,%5.1f)' % (world.imu_sensor.gyroscope),
            'Location:% 20s' % ('(% 5.1f, % 5.1f)' % (t.location.x, t.location.y)),
            'GNSS:% 24s' % ('(% 2.6f, % 3.6f)' % (world.gnss_sensor.lat, world.gnss_sensor.lon)),
            'Height:  % 18.0f m' % t.location.z,
            '']
        if isinstance(c, carla.VehicleControl):
            self._info_text += [
                ('Throttle:', c.throttle, 0.0, 1.0),
                ('Steer:', c.steer, -1.0, 1.0),
                ('Brake:', c.brake, 0.0, 1.0),
                ('Reverse:', c.reverse),
                ('Hand brake:', c.hand_brake),
                ('Manual:', c.manual_gear_shift),
                'Gear:        %s' % {-1: 'R', 0: 'N'}.get(c.gear, c.gear)]
            if self._show_ackermann_info:
                self._info_text += [
                    '',
                    'Ackermann Controller:',
                    '  Target speed: % 8.0f km/h' % (3.6*self._ackermann_control.speed),
                ]
        elif isinstance(c, carla.WalkerControl):
            self._info_text += [
                ('Speed:', c.speed, 0.0, 5.556),
                ('Jump:', c.jump)]
        self._info_text += [
            '',
            'Collision:',
            collision,
            '',
            'Number of vehicles: % 8d' % len(vehicles)]
        if len(vehicles) > 1:
            self._info_text += ['Nearby vehicles:']
            distance = lambda l: math.sqrt((l.x - t.location.x)**2 + (l.y - t.location.y)**2 + (l.z - t.location.z)**2)
            vehicles = [(distance(x.get_location()), x) for x in vehicles if x.id != world.player.id]
            for d, vehicle in sorted(vehicles, key=lambda vehicles: vehicles[0]):
                if d > 200.0:
                    break
                vehicle_type = get_actor_display_name(vehicle, truncate=22)
                self._info_text.append('% 4dm %s' % (d, vehicle_type))

    def show_ackermann_info(self, enabled):
        self._show_ackermann_info = enabled

    def update_ackermann_control(self, ackermann_control):
        self._ackermann_control = ackermann_control

    def toggle_info(self):
        self._show_info = not self._show_info

    def notification(self, text, seconds=2.0):
        self._notifications.set_text(text, seconds=seconds)

    def error(self, text):
        self._notifications.set_text('Error: %s' % text, (255, 0, 0))

    def render(self, display):
        if self._show_info:
            info_surface = pygame.Surface((220, self.dim[1]))
            info_surface.set_alpha(100)
            display.blit(info_surface, (0, 0))
            v_offset = 4
            bar_h_offset = 100
            bar_width = 106
            for item in self._info_text:
                if v_offset + 18 > self.dim[1]:
                    break
                if isinstance(item, list):
                    if len(item) > 1:
                        points = [(x + 8, v_offset + 8 + (1.0 - y) * 30) for x, y in enumerate(item)]
                        pygame.draw.lines(display, (255, 136, 0), False, points, 2)
                    item = None
                    v_offset += 18
                elif isinstance(item, tuple):
                    if isinstance(item[1], bool):
                        rect = pygame.Rect((bar_h_offset, v_offset + 8), (6, 6))
                        pygame.draw.rect(display, (255, 255, 255), rect, 0 if item[1] else 1)
                    else:
                        rect_border = pygame.Rect((bar_h_offset, v_offset + 8), (bar_width, 6))
                        pygame.draw.rect(display, (255, 255, 255), rect_border, 1)
                        f = (item[1] - item[2]) / (item[3] - item[2])
                        if item[2] < 0.0:
                            rect = pygame.Rect((bar_h_offset + f * (bar_width - 6), v_offset + 8), (6, 6))
                        else:
                            rect = pygame.Rect((bar_h_offset, v_offset + 8), (f * bar_width, 6))
                        pygame.draw.rect(display, (255, 255, 255), rect)
                    item = item[0]
                if item:  # At this point has to be a str.
                    surface = self._font_mono.render(item, True, (255, 255, 255))
                    display.blit(surface, (8, v_offset))
                v_offset += 18
        self._notifications.render(display)
        self.help.render(display)


# ==============================================================================
# -- FadingText ----------------------------------------------------------------
# ==============================================================================


class FadingText(object):
    def __init__(self, font, dim, pos):
        self.font = font
        self.dim = dim
        self.pos = pos
        self.seconds_left = 0
        self.surface = pygame.Surface(self.dim)

    def set_text(self, text, color=(255, 255, 255), seconds=2.0):
        text_texture = self.font.render(text, True, color)
        self.surface = pygame.Surface(self.dim)
        self.seconds_left = seconds
        self.surface.fill((0, 0, 0, 0))
        self.surface.blit(text_texture, (10, 11))

    def tick(self, _, clock):
        delta_seconds = 1e-3 * clock.get_time()
        self.seconds_left = max(0.0, self.seconds_left - delta_seconds)
        self.surface.set_alpha(500.0 * self.seconds_left)

    def render(self, display):
        display.blit(self.surface, self.pos)


# ==============================================================================
# -- HelpText ------------------------------------------------------------------
# ==============================================================================


class HelpText(object):
    """Helper class to handle text output using pygame"""
    def __init__(self, font, width, height):
        lines = __doc__.split('\n')
        self.font = font
        self.line_space = 18
        self.dim = (780, len(lines) * self.line_space + 12)
        self.pos = (0.5 * width - 0.5 * self.dim[0], 0.5 * height - 0.5 * self.dim[1])
        self.seconds_left = 0
        self.surface = pygame.Surface(self.dim)
        self.surface.fill((0, 0, 0, 0))
        for n, line in enumerate(lines):
            text_texture = self.font.render(line, True, (255, 255, 255))
            self.surface.blit(text_texture, (22, n * self.line_space))
            self._render = False
        self.surface.set_alpha(220)

    def toggle(self):
        self._render = not self._render

    def render(self, display):
        if self._render:
            display.blit(self.surface, self.pos)


# ==============================================================================
# -- CollisionSensor -----------------------------------------------------------
# ==============================================================================


class CollisionSensor(object):
    def __init__(self, parent_actor, hud):
        self.sensor = None
        self.history = []
        self._parent = parent_actor
        self.hud = hud
        world = self._parent.get_world()
        bp = world.get_blueprint_library().find('sensor.other.collision')
        self.sensor = world.spawn_actor(bp, carla.Transform(), attach_to=self._parent)
        # We need to pass the lambda a weak reference to self to avoid circular
        # reference.
        weak_self = weakref.ref(self)
        self.sensor.listen(lambda event: CollisionSensor._on_collision(weak_self, event))

    def get_collision_history(self):
        history = collections.defaultdict(int)
        for frame, intensity in self.history:
            history[frame] += intensity
        return history

    @staticmethod
    def _on_collision(weak_self, event):
        self = weak_self()
        if not self:
            return
        actor_type = get_actor_display_name(event.other_actor)
        self.hud.notification('Collision with %r' % actor_type)
        impulse = event.normal_impulse
        intensity = math.sqrt(impulse.x**2 + impulse.y**2 + impulse.z**2)
        self.history.append((event.frame, intensity))
        if len(self.history) > 4000:
            self.history.pop(0)


# ==============================================================================
# -- LaneInvasionSensor --------------------------------------------------------
# ==============================================================================


class LaneInvasionSensor(object):
    def __init__(self, parent_actor, hud):
        self.sensor = None

        # If the spawn object is not a vehicle, we cannot use the Lane Invasion Sensor
        if parent_actor.type_id.startswith("vehicle."):
            self._parent = parent_actor
            self.hud = hud
            world = self._parent.get_world()
            bp = world.get_blueprint_library().find('sensor.other.lane_invasion')
            self.sensor = world.spawn_actor(bp, carla.Transform(), attach_to=self._parent)
            # We need to pass the lambda a weak reference to self to avoid circular
            # reference.
            weak_self = weakref.ref(self)
            self.sensor.listen(lambda event: LaneInvasionSensor._on_invasion(weak_self, event))

    @staticmethod
    def _on_invasion(weak_self, event):
        self = weak_self()
        if not self:
            return
        lane_types = set(x.type for x in event.crossed_lane_markings)
        text = ['%r' % str(x).split()[-1] for x in lane_types]
        self.hud.notification('Crossed line %s' % ' and '.join(text))


# ==============================================================================
# -- GnssSensor ----------------------------------------------------------------
# ==============================================================================


class GnssSensor(object):
    def __init__(self, parent_actor):
        self.sensor = None
        self._parent = parent_actor
        self.lat = 0.0
        self.lon = 0.0
        world = self._parent.get_world()
        bp = world.get_blueprint_library().find('sensor.other.gnss')
        self.sensor = world.spawn_actor(bp, carla.Transform(carla.Location(x=1.0, z=2.8)), attach_to=self._parent)
        # We need to pass the lambda a weak reference to self to avoid circular
        # reference.
        weak_self = weakref.ref(self)
        self.sensor.listen(lambda event: GnssSensor._on_gnss_event(weak_self, event))

    @staticmethod
    def _on_gnss_event(weak_self, event):
        self = weak_self()
        if not self:
            return
        self.lat = event.latitude
        self.lon = event.longitude


# ==============================================================================
# -- IMUSensor -----------------------------------------------------------------
# ==============================================================================


class IMUSensor(object):
    def __init__(self, parent_actor):
        self.sensor = None
        self._parent = parent_actor
        self.accelerometer = (0.0, 0.0, 0.0)
        self.gyroscope = (0.0, 0.0, 0.0)
        self.compass = 0.0
        world = self._parent.get_world()
        bp = world.get_blueprint_library().find('sensor.other.imu')
        self.sensor = world.spawn_actor(
            bp, carla.Transform(), attach_to=self._parent)
        # We need to pass the lambda a weak reference to self to avoid circular
        # reference.
        weak_self = weakref.ref(self)
        self.sensor.listen(
            lambda sensor_data: IMUSensor._IMU_callback(weak_self, sensor_data))

    @staticmethod
    def _IMU_callback(weak_self, sensor_data):
        self = weak_self()
        if not self:
            return
        limits = (-99.9, 99.9)
        self.accelerometer = (
            max(limits[0], min(limits[1], sensor_data.accelerometer.x)),
            max(limits[0], min(limits[1], sensor_data.accelerometer.y)),
            max(limits[0], min(limits[1], sensor_data.accelerometer.z)))
        self.gyroscope = (
            max(limits[0], min(limits[1], math.degrees(sensor_data.gyroscope.x))),
            max(limits[0], min(limits[1], math.degrees(sensor_data.gyroscope.y))),
            max(limits[0], min(limits[1], math.degrees(sensor_data.gyroscope.z))))
        self.compass = math.degrees(sensor_data.compass)


# ==============================================================================
# -- RadarSensor ---------------------------------------------------------------
# ==============================================================================


class RadarSensor(object):
    def __init__(self, parent_actor):
        self.sensor = None
        self._parent = parent_actor
        bound_x = 0.5 + self._parent.bounding_box.extent.x
        bound_y = 0.5 + self._parent.bounding_box.extent.y
        bound_z = 0.5 + self._parent.bounding_box.extent.z

        self.velocity_range = 7.5 # m/s
        world = self._parent.get_world()
        self.debug = world.debug
        bp = world.get_blueprint_library().find('sensor.other.radar')
        bp.set_attribute('horizontal_fov', str(35))
        bp.set_attribute('vertical_fov', str(20))
        self.sensor = world.spawn_actor(
            bp,
            carla.Transform(
                carla.Location(x=bound_x + 0.05, z=bound_z+0.05),
                carla.Rotation(pitch=5)),
            attach_to=self._parent)
        # We need a weak reference to self to avoid circular reference.
        weak_self = weakref.ref(self)
        self.sensor.listen(
            lambda radar_data: RadarSensor._Radar_callback(weak_self, radar_data))

    @staticmethod
    def _Radar_callback(weak_self, radar_data):
        self = weak_self()
        if not self:
            return
        # To get a numpy [[vel, altitude, azimuth, depth],...[,,,]]:
        # points = np.frombuffer(radar_data.raw_data, dtype=np.dtype('f4'))
        # points = np.reshape(points, (len(radar_data), 4))

        current_rot = radar_data.transform.rotation
        for detect in radar_data:
            azi = math.degrees(detect.azimuth)
            alt = math.degrees(detect.altitude)
            # The 0.25 adjusts a bit the distance so the dots can
            # be properly seen
            fw_vec = carla.Vector3D(x=detect.depth - 0.25)
            carla.Transform(
                carla.Location(),
                carla.Rotation(
                    pitch=current_rot.pitch + alt,
                    yaw=current_rot.yaw + azi,
                    roll=current_rot.roll)).transform(fw_vec)

            def clamp(min_v, max_v, value):
                return max(min_v, min(value, max_v))

            norm_velocity = detect.velocity / self.velocity_range # range [-1, 1]
            r = int(clamp(0.0, 1.0, 1.0 - norm_velocity) * 255.0)
            g = int(clamp(0.0, 1.0, 1.0 - abs(norm_velocity)) * 255.0)
            b = int(abs(clamp(- 1.0, 0.0, - 1.0 - norm_velocity)) * 255.0)
            self.debug.draw_point(
                radar_data.transform.location + fw_vec,
                size=0.075,
                life_time=0.06,
                persistent_lines=False,
                color=carla.Color(r, g, b))

# ==============================================================================
# -- CameraManager -------------------------------------------------------------
# ==============================================================================


class CameraManager(object):
    def __init__(self, parent_actor, hud, gamma_correction):
        self.sensor = None
        self.surface = None
        self._parent = parent_actor
        self.hud = hud
        self.recording = False
        bound_x = 0.5 + self._parent.bounding_box.extent.x
        bound_y = 0.5 + self._parent.bounding_box.extent.y
        bound_z = 0.5 + self._parent.bounding_box.extent.z
        Attachment = carla.AttachmentType

        if not self._parent.type_id.startswith("walker.pedestrian"):
            self._camera_transforms = [
                (carla.Transform(driver_eye(self._parent)), Attachment.Rigid),
                (carla.Transform(carla.Location(x=-2.0*bound_x, y=+0.0*bound_y, z=2.0*bound_z), carla.Rotation(pitch=8.0)), Attachment.SpringArmGhost),
                (carla.Transform(carla.Location(x=+0.8*bound_x, y=+0.0*bound_y, z=1.3*bound_z)), Attachment.Rigid),
                (carla.Transform(carla.Location(x=+1.9*bound_x, y=+1.0*bound_y, z=1.2*bound_z)), Attachment.SpringArmGhost),
                (carla.Transform(carla.Location(x=-2.8*bound_x, y=+0.0*bound_y, z=4.6*bound_z), carla.Rotation(pitch=6.0)), Attachment.SpringArmGhost),
                (carla.Transform(carla.Location(x=-1.0, y=-1.0*bound_y, z=0.4*bound_z)), Attachment.Rigid)]
        else:
            self._camera_transforms = [
                (carla.Transform(carla.Location(x=-2.5, z=0.0), carla.Rotation(pitch=-8.0)), Attachment.SpringArmGhost),
                (carla.Transform(carla.Location(x=1.6, z=1.7)), Attachment.Rigid),
                (carla.Transform(carla.Location(x=2.5, y=0.5, z=0.0), carla.Rotation(pitch=-8.0)), Attachment.SpringArmGhost),
                (carla.Transform(carla.Location(x=-4.0, z=2.0), carla.Rotation(pitch=6.0)), Attachment.SpringArmGhost),
                (carla.Transform(carla.Location(x=0, y=-2.5, z=-0.0), carla.Rotation(yaw=90.0)), Attachment.Rigid)]

        self.transform_index = 0
        self.sensors = [
            ['sensor.camera.rgb', cc.Raw, 'Camera RGB', {}],
            ['sensor.camera.depth', cc.Raw, 'Camera Depth (Raw)', {}],
            ['sensor.camera.depth', cc.Depth, 'Camera Depth (Gray Scale)', {}],
            ['sensor.camera.depth', cc.LogarithmicDepth, 'Camera Depth (Logarithmic Gray Scale)', {}],
            ['sensor.camera.semantic_segmentation', cc.Raw, 'Camera Semantic Segmentation (Raw)', {}],
            ['sensor.camera.semantic_segmentation', cc.CityScapesPalette, 'Camera Semantic Segmentation (CityScapes Palette)', {}],
            ['sensor.camera.instance_segmentation', cc.Raw, 'Camera Instance Segmentation (Raw)', {}],
            ['sensor.lidar.ray_cast', None, 'Lidar (Ray-Cast)', {'range': '50'}],
            ['sensor.lidar.ray_cast_semantic', None, 'Semantic Lidar (Ray-Cast)', {'range': '50'}],
            ['sensor.camera.rgb', cc.Raw, 'Camera RGB Distorted',
                {'lens_circle_multiplier': '3.0',
                'lens_circle_falloff': '3.0',
                'chromatic_aberration_intensity': '0.5',
                'chromatic_aberration_offset': '0'}],
            ['sensor.camera.optical_flow', cc.Raw, 'Optical Flow', {}],
            ['sensor.camera.normals', cc.Raw, 'Camera Normals', {}],
        ]
        world = self._parent.get_world()
        bp_library = world.get_blueprint_library()
        for item in self.sensors:
            bp = bp_library.find(item[0])
            if item[0].startswith('sensor.camera'):
                bp.set_attribute('image_size_x', str(hud.dim[0]))
                bp.set_attribute('image_size_y', str(hud.dim[1]))
                if bp.has_attribute('gamma'):
                    bp.set_attribute('gamma', str(gamma_correction))
                for attr_name, attr_value in item[3].items():
                    bp.set_attribute(attr_name, attr_value)
            elif item[0].startswith('sensor.lidar'):
                self.lidar_range = 50

                for attr_name, attr_value in item[3].items():
                    bp.set_attribute(attr_name, attr_value)
                    if attr_name == 'range':
                        self.lidar_range = float(attr_value)

            item.append(bp)
        self.index = None

    def toggle_camera(self):
        self.transform_index = (self.transform_index + 1) % len(self._camera_transforms)
        self.set_sensor(self.index, notify=False, force_respawn=True)

    def set_sensor(self, index, notify=True, force_respawn=False):
        index = index % len(self.sensors)
        needs_respawn = True if self.index is None else \
            (force_respawn or (self.sensors[index][2] != self.sensors[self.index][2]))
        if needs_respawn:
            if self.sensor is not None:
                self.sensor.destroy()
                self.surface = None
            self.sensor = self._parent.get_world().spawn_actor(
                self.sensors[index][-1],
                self._camera_transforms[self.transform_index][0],
                attach_to=self._parent,
                attachment_type=self._camera_transforms[self.transform_index][1])
            # We need to pass the lambda a weak reference to self to avoid
            # circular reference.
            weak_self = weakref.ref(self)
            self.sensor.listen(lambda image: CameraManager._parse_image(weak_self, image))
        if notify:
            self.hud.notification(self.sensors[index][2])
        self.index = index

    def next_sensor(self):
        self.set_sensor(self.index + 1)

    def toggle_recording(self):
        self.recording = not self.recording
        self.hud.notification('Recording %s' % ('On' if self.recording else 'Off'))

    def render(self, display):
        if self.surface is not None:
            display.blit(self.surface, (0, 0))

    @staticmethod
    def _parse_image(weak_self, image):
        self = weak_self()
        if not self:
            return
        if self.sensors[self.index][0] == 'sensor.lidar.ray_cast':
            points = np.frombuffer(image.raw_data, dtype=np.dtype('f4'))
            points = np.reshape(points, (int(points.shape[0] / 4), 4))
            lidar_data = np.array(points[:, :2])
            lidar_data *= min(self.hud.dim) / (2.0 * self.lidar_range)
            lidar_data += (0.5 * self.hud.dim[0], 0.5 * self.hud.dim[1])
            lidar_data = np.fabs(lidar_data)  # pylint: disable=E1111
            lidar_data = lidar_data.astype(np.int32)
            lidar_data = np.reshape(lidar_data, (-1, 2))
            lidar_img_size = (self.hud.dim[0], self.hud.dim[1], 3)
            lidar_img = np.zeros((lidar_img_size), dtype=np.uint8)
            lidar_img[tuple(lidar_data.T)] = (255, 255, 255)
            self.surface = pygame.surfarray.make_surface(lidar_img)
        elif self.sensors[self.index][0] == 'sensor.lidar.ray_cast_semantic':
            points = np.frombuffer(image.raw_data, dtype=np.dtype('f4'))
            points = np.reshape(points, (int(points.shape[0] / 6), 6))
            lidar_data = np.array(points[:, :2])
            lidar_data *= min(self.hud.dim) / (2.0 * self.lidar_range)
            lidar_data += (0.5 * self.hud.dim[0], 0.5 * self.hud.dim[1])
            lidar_data = lidar_data.astype(np.int32)
            lidar_data = np.reshape(lidar_data, (-1, 2))
            lidar_img_size = (self.hud.dim[0], self.hud.dim[1], 3)
            lidar_img = np.zeros((lidar_img_size), dtype=np.uint8)
            for i in range(len(image)):
                point = lidar_data[i]
                lidar_tag = image[i].object_tag
                lidar_img[tuple(point.T)] = OBJECT_TO_COLOR[int(lidar_tag)]
            self.surface = pygame.surfarray.make_surface(lidar_img)
        elif self.sensors[self.index][0].startswith('sensor.camera.optical_flow'):
            image = image.get_color_coded_flow()
            array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
            array = np.reshape(array, (image.height, image.width, 4))
            array = array[:, :, :3]
            array = array[:, :, ::-1]
            self.surface = pygame.surfarray.make_surface(array.swapaxes(0, 1))
        else:
            image.convert(self.sensors[self.index][1])
            array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
            array = np.reshape(array, (image.height, image.width, 4))
            array = array[:, :, :3]
            array = array[:, :, ::-1]
            self.surface = pygame.surfarray.make_surface(array.swapaxes(0, 1))
        if self.recording:
            image.save_to_disk('_out/%08d' % image.frame)


# ==============================================================================
# -- game_loop() ---------------------------------------------------------------
# ==============================================================================


def game_loop(args):
    pygame.init()
    pygame.font.init()
    world = None
    original_settings = None
    dashboard = None
    tips = Tips()
    wheel = None

    try:
        # Opened first so the browser is up, and has taken and given back the
        # focus, by the time the drive window needs the keyboard.
        if args.dashboard:
            dashboard = Dashboard(args.dashboard_port).start(args.dashboard_monitor)

        client = carla.Client(args.host, args.port)
        client.set_timeout(2000.0)

        sim_world = client.get_world()
        traffic_manager = client.get_trafficmanager(args.tm_port)
        if args.sync:
            original_settings = sim_world.get_settings()
            settings = sim_world.get_settings()
            if not settings.synchronous_mode:
                settings.synchronous_mode = True
                settings.fixed_delta_seconds = 0.05
            sim_world.apply_settings(settings)

            traffic_manager.set_synchronous_mode(True)

        if args.autopilot and not sim_world.get_settings().synchronous_mode:
            print("WARNING: You are currently in asynchronous mode and could "
                  "experience some issues with the traffic simulation")

        flags = pygame.HWSURFACE | pygame.DOUBLEBUF
        if not args.windowed:
            # Borderless over the whole primary monitor rather than exclusive
            # full screen, which would minimise itself whenever the dashboard
            # on the other monitor is clicked.
            os.environ['SDL_VIDEO_WINDOW_POS'] = '0,0'
            args.width, args.height = pygame.display.get_desktop_sizes()[0]
            flags |= pygame.NOFRAME
        display = pygame.display.set_mode((args.width, args.height), flags)
        display.fill((0,0,0))
        pygame.display.flip()

        if args.wheel:
            wheel = SteeringWheel.find(args.wheel, steer_ratio=args.steer_ratio,
                                       ffb_gain=args.ffb_gain)
            if wheel is None:
                print('No steering wheel matching %r; keyboard only.' % args.wheel)
            else:
                print('Steering wheel: %s, force feedback %s' % (
                    wheel.name, 'on' if wheel.has_ffb else 'off'))

        hud = HUD(args.width, args.height)
        world = World(sim_world, hud, traffic_manager, args)
        controller = KeyboardControl(world, args.autopilot, wheel)

        if args.sync:
            sim_world.tick()
        else:
            sim_world.wait_for_tick()

        clock = pygame.time.Clock()
        refocus = dashboard is not None
        last_collision = None
        while True:
            if args.sync:
                sim_world.tick()
            clock.tick_busy_loop(60)
            if controller.parse_events(client, world, clock, args.sync):
                return
            world.tick(clock)
            world.render(display)
            pygame.display.flip()
            v = world.player.get_velocity()
            speed = 3.6 * math.sqrt(v.x**2 + v.y**2 + v.z**2)
            if wheel is not None:
                history = world.collision_sensor.history
                collision = history[-1] if history else None
                if collision is not None and collision is not last_collision:
                    wheel.kick(collision[1])
                last_collision = collision
                wheel.update_ffb(world.player, world.imu_sensor,
                                 controller.autopilot_enabled, clock.get_time() / 1000.0)
            if dashboard is not None:
                state = controller.dashboard_state()
                state.update(tips.update(speed))
                dashboard.update(speed=int(round(speed)), **state)
                if refocus and dashboard.connected.is_set():
                    focus_window(pygame.display.get_wm_info().get('window'))
                    refocus = False

    finally:

        if wheel is not None:
            wheel.close()

        if dashboard is not None:
            dashboard.close()

        if original_settings:
            sim_world.apply_settings(original_settings)

        if (world and world.recording_enabled):
            client.stop_recorder()

        if world is not None:
            world.destroy()

        pygame.quit()


# ==============================================================================
# -- main() --------------------------------------------------------------------
# ==============================================================================


def main():
    argparser = argparse.ArgumentParser(description='CARLA Manual Control Client')
    argparser.add_argument(
        '-v', '--verbose', action='store_true', dest='debug',
        help='print debug information')
    argparser.add_argument(
        '--host', metavar='H', default='127.0.0.1',
        help='IP of the host server (default: 127.0.0.1)')
    argparser.add_argument(
        '-p', '--port', metavar='P', default=2000, type=int,
        help='TCP port to listen to (default: 2000)')
    argparser.add_argument(
        '-a', '--autopilot', action='store_true',
        help='enable autopilot')
    argparser.add_argument(
        '--res', metavar='WIDTHxHEIGHT', default='1280x720',
        help='window resolution with --windowed (default: 1280x720)')
    argparser.add_argument(
        '--windowed', action='store_true',
        help='a window instead of full screen on the primary monitor')
    argparser.add_argument(
        '--filter', metavar='PATTERN', default=None,
        help='actor filter; random car per Backspace (default: cycle the DRIVER_EYES cars)')
    argparser.add_argument(
        '--generation', metavar='G', default='All',
        help='restrict to certain actor generation (values: "2","3","All" - default: "All")')
    argparser.add_argument(
        '--rolename', metavar='NAME', default='hero',
        help='actor role name (default: "hero")')
    argparser.add_argument(
        '--gamma', default=1.0, type=float,
        help='Gamma correction of the camera (default: 1.0)')
    argparser.add_argument(
        '--sync', action='store_true',
        help='Activate synchronous mode execution')
    argparser.add_argument(
        '--tm-port', metavar='P', default=8000, type=int,
        help='port of the Traffic Manager (default: 8000)')
    argparser.add_argument(
        '--no-dashboard', action='store_false', dest='dashboard',
        help='do not open the dashboard window')
    argparser.add_argument(
        '--dashboard-monitor', metavar='N', default=None, type=int,
        help='monitor to show the dashboard on, 0 = primary (default: the second monitor)')
    argparser.add_argument(
        '--dashboard-port', metavar='P', default=0, type=int,
        help='HTTP port of the dashboard page (default: any free port)')
    argparser.add_argument(
        '--wheel', metavar='NAME', default='G25',
        help='steering wheel to use, part of its name (default: G25)')
    argparser.add_argument(
        '--no-wheel', action='store_const', const=None, dest='wheel',
        help='ignore any steering wheel')
    argparser.add_argument(
        '--steer-ratio', metavar='R', default=10.0, type=float,
        help='degrees of wheel per degree of front-wheel angle (default: 10)')
    argparser.add_argument(
        '--ffb-gain', metavar='G', default=1.0, type=float,
        help='force-feedback strength, 1 = full (default: 1)')
    args = argparser.parse_args()

    args.width, args.height = [int(x) for x in args.res.split('x')]

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(format='%(levelname)s: %(message)s', level=log_level)

    logging.info('listening to server %s:%s', args.host, args.port)

    print(__doc__)

    try:

        game_loop(args)

    except KeyboardInterrupt:
        print('\nCancelled by user. Bye!')


if __name__ == '__main__':

    main()
