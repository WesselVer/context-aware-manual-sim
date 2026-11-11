"""The car's own steering wheel, turned in the driver's view.

CARLA 0.9.16's cars have a steering wheel that never turns: it is part of
the body mesh, with no bone or component the Python API can move. So it is
turned in the image instead. Once per car, a depth image from the driver's
camera finds which pixels are the wheel (find_wheel); every frame, those
pixels are cut out of the camera image, turned about the hub (in the plane
of the wheel, which the camera sees as an ellipse), and drawn back over a
copy of the cabin with the wheel filled out of it (WheelImage).

The wheel's lower edge is often below the screen; the turned wheel there
takes its rim from the top, mirrored, so a turned rim has no gap.
"""

import collections

import numpy as np
import pygame

# A depth step bigger than this share of the distance is an edge: the wheel
# is the patch of smooth depth around the hub, bounded by the step to the
# dashboard behind it.
DEPTH_JUMP = 0.03
# Nothing further away than this (m) is part of the wheel.
MAX_DEPTH = 1.2
# Behind the rim, further than this (m) past the rim's own depth, is not
# the wheel (the Mini's instrument cluster sits on the column behind it).
BEHIND_RIM = 0.05
# The rim is fitted on this share of the wheel's height from its top, above
# the stalks that stick out to the sides.
RIM_FIT_SHARE = 0.35

WheelGeometry = collections.namedtuple('WheelGeometry', 'mask cx cy a b')


def depth_metres(image):
    """A carla.Image from sensor.camera.depth as an (H, W) array of metres."""
    d = np.reshape(np.frombuffer(image.raw_data, dtype=np.uint8), (image.height, image.width, 4))
    d = d.astype(np.float64)
    return 1000.0 * (d[:, :, 2] + d[:, :, 1] * 256.0 + d[:, :, 0] * 65536.0) / (256.0 ** 3 - 1)


def find_wheel(metres):
    """The steering wheel in a driver's-eye depth image: its pixels and the
    ellipse of its rim (centre cx, cy; half-axes a across, b down); None
    when there is no wheel low in the middle of the view."""
    m = metres
    h, w = m.shape
    rel = DEPTH_JUMP * m
    edge = np.zeros(m.shape, dtype=bool)
    dx = np.abs(np.diff(m, axis=1))
    dy = np.abs(np.diff(m, axis=0))
    edge[:, 1:] |= dx > rel[:, 1:]
    edge[:, :-1] |= dx > rel[:, :-1]
    edge[1:, :] |= dy > rel[1:, :]
    edge[:-1, :] |= dy > rel[:-1, :]
    smooth = ~edge & (m < MAX_DEPTH)
    # The hub: the nearest smooth point low in the middle.
    y0, y1, x0, x1 = int(h * 0.8), h - 10, w // 2 - 80, w // 2 + 80
    window = np.where(smooth[y0:y1, x0:x1], m[y0:y1, x0:x1], np.inf)
    if not np.isfinite(window).any():
        return None
    sy, sx = np.unravel_index(np.argmin(window), window.shape)
    surface = pygame.Surface((w, h), pygame.SRCALPHA)
    alpha = pygame.surfarray.pixels_alpha(surface)
    alpha[:] = (smooth.T * 255).astype(np.uint8)
    del alpha
    component = pygame.mask.from_surface(surface).connected_component((x0 + sx, y0 + sy))
    mask = pygame.surfarray.array_red(
        component.to_surface(setcolor=(255, 255, 255, 255), unsetcolor=(0, 0, 0, 255))).T > 0
    rows = np.nonzero(mask.any(axis=1))[0]
    if len(rows) < 20:
        return None
    top = rows[0]
    rim_depth = np.median(m[top:top + 8][mask[top:top + 8]])
    mask &= m <= rim_depth + BEHIND_RIM
    # The rim's outer edge on its upper part (above the stalks), in 3D: the
    # camera has a 90-degree field of view, so f is half the image width.
    f = w / 2.0
    us, vs = [], []
    for y in range(top, top + int(RIM_FIT_SHARE * (h - top))):
        xs = np.nonzero(mask[y])[0]
        if len(xs) > 1:
            for x in (xs[0], xs[-1]):
                us.append(x)
                vs.append(y)
    if len(us) < 20:
        return None
    us, vs = np.array(us, dtype=np.float64), np.array(vs, dtype=np.float64)
    zs = m[vs.astype(int), us.astype(int)]
    pts = np.stack([(us - w / 2.0) * zs / f, (vs - h / 2.0) * zs / f, zs], axis=1)
    # The rim's plane, and the circle in it.
    mean = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - mean)
    normal = vt[2]
    ex = np.array([1.0, 0.0, 0.0]) - normal[0] * normal
    ex /= np.linalg.norm(ex)
    ey = np.cross(normal, ex)
    pu, pv = (pts - mean) @ ex, (pts - mean) @ ey
    # Kasa fit: u^2 + v^2 + D u + E v + F = 0.
    sol, *_ = np.linalg.lstsq(np.stack([pu, pv, np.ones_like(pu)], axis=1), -(pu ** 2 + pv ** 2), rcond=None)
    uc, vc = -sol[0] / 2.0, -sol[1] / 2.0
    radius = np.sqrt(max(uc * uc + vc * vc - sol[2], 1e-6))
    centre = mean + uc * ex + vc * ey

    def project(p):
        return np.array([w / 2.0 + f * p[0] / p[2], h / 2.0 + f * p[1] / p[2]])

    # The rim's centre in the image is the column's; its outline there is
    # near enough an ellipse about it, fitted to the same edge points.
    c = project(centre)
    if not np.isfinite(c).all():
        return None
    sol, *_ = np.linalg.lstsq(np.stack([(us - c[0]) ** 2, (vs - c[1]) ** 2], axis=1),
                              np.ones_like(us), rcond=None)
    if sol[0] <= 0 or sol[1] <= 0:
        return None
    a, b = 1.0 / np.sqrt(sol[0]), 1.0 / np.sqrt(sol[1])
    if a < 20 or b < 20:
        return None
    # Only what lies within the rim: the stalks beside it stay where they are.
    yy, xx = np.mgrid[0:h, 0:w]
    mask &= ((xx - c[0]) / (a * 1.04)) ** 2 + ((yy - c[1]) / (b * 1.04)) ** 2 <= 1.0
    return WheelGeometry(mask, float(c[0]), float(c[1]), float(a), float(b))


def _fill(image, unknown):
    """Fills the unknown pixels of an (H, W, 3) float image from around them
    (push-pull: averaged down until there are no holes, then back up)."""
    if not unknown.any():
        return image
    h, w = unknown.shape
    if min(h, w) < 4:
        image[unknown] = image[~unknown].mean(axis=0) if (~unknown).any() else 0.0
        return image
    h2, w2 = h // 2 * 2, w // 2 * 2
    known = (~unknown[:h2, :w2]).astype(np.float64)
    weight = known.reshape(h2 // 2, 2, w2 // 2, 2).sum(axis=(1, 3))
    total = (image[:h2, :w2] * known[:, :, None]).reshape(h2 // 2, 2, w2 // 2, 2, 3).sum(axis=(1, 3))
    small_unknown = weight == 0
    small = total / np.maximum(weight, 1.0)[:, :, None]
    small = _fill(small, small_unknown)
    up = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)
    if up.shape[0] < h or up.shape[1] < w:
        up = np.pad(up, ((0, h - up.shape[0]), (0, w - up.shape[1]), (0, 0)), mode='edge')
    # Smoothed, so the coarse levels do not show as blocks.
    p = np.pad(up, ((1, 1), (1, 1), (0, 0)), mode='edge')
    up = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] + 2 * p[1:-1, 1:-1]) / 6.0
    image[unknown] = up[unknown]
    return image


class WheelImage(object):
    """Turns the wheel of a WheelGeometry in the driver's camera image."""

    def __init__(self, geometry):
        self.geometry = g = geometry
        h, w = g.mask.shape
        # The sprite: a box round the rim, each pixel taken from the image,
        # or (below the screen) from the mirror image above the centre.
        pad = 6
        self._x0 = int(np.floor(g.cx - g.a)) - pad
        self._y0 = int(np.floor(g.cy - g.b)) - pad
        sw, sh = int(np.ceil(2 * g.a)) + 2 * pad, int(np.ceil(2 * g.b)) + 2 * pad
        vy, vx = np.mgrid[0:sh, 0:sw]
        ix, iy = vx + self._x0, vy + self._y0
        below = iy >= h
        iy = np.where(below, np.round(2 * g.cy - iy).astype(int), iy)
        inside = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        self._ix, self._iy = np.clip(ix, 0, w - 1), np.clip(iy, 0, h - 1)
        self._alpha = np.where(inside & g.mask[self._iy, self._ix], 255, 0).astype(np.uint8)
        self._size = (sw, sh)
        self._sprite = None
        self._sprite_of = None
        # Where the cabin without the wheel is drawn: the wheel's pixels on screen.
        rows, cols = np.nonzero(g.mask)
        self._plate_rect = pygame.Rect(int(cols.min()) - 2, int(rows.min()) - 2,
                                       int(cols.max() - cols.min()) + 5, int(rows.max() - rows.min()) + 5)
        self._plate_rect.clamp_ip(pygame.Rect(0, 0, w, h))
        self._plate_rect = self._plate_rect.clip(pygame.Rect(0, 0, w, h))
        self._plate = None

    def set_background(self, rgb):
        """The cabin without the wheel, filled in from an (H, W, 3) camera image."""
        r = self._plate_rect
        region = rgb[r.top:r.bottom, r.left:r.right].astype(np.float64)
        hole = self.geometry.mask[r.top:r.bottom, r.left:r.right]
        # A pixel's grow: the wheel's antialiased edge goes too.
        grown = hole.copy()
        grown[1:] |= hole[:-1]
        grown[:-1] |= hole[1:]
        grown[:, 1:] |= hole[:, :-1]
        grown[:, :-1] |= hole[:, 1:]
        filled = _fill(region.copy(), grown)
        plate = pygame.Surface(r.size, pygame.SRCALPHA)
        pygame.surfarray.pixels3d(plate)[:] = np.clip(filled, 0, 255).astype(np.uint8).swapaxes(0, 1)
        pygame.surfarray.pixels_alpha(plate)[:] = (grown.T * 255).astype(np.uint8)
        self._plate = plate

    def draw(self, surface, rgb, degrees):
        """Draws the wheel turned degrees clockwise (the camera image rgb is
        already on surface); straight ahead it is left as it is."""
        if self._plate is None or abs(degrees) < 0.5:
            return
        g = self.geometry
        if self._sprite_of is not rgb:
            sprite = pygame.Surface(self._size, pygame.SRCALPHA)
            pygame.surfarray.pixels3d(sprite)[:] = rgb[self._iy, self._ix].swapaxes(0, 1)
            pygame.surfarray.pixels_alpha(sprite)[:] = self._alpha.T
            # Squashed to a circle, so turning it is turning it in the wheel's plane.
            sw, sh = self._size
            self._sprite = pygame.transform.smoothscale(sprite, (sw, max(1, int(round(sh * g.a / g.b)))))
            self._sprite_of = rgb
        surface.blit(self._plate, self._plate_rect)
        turned = pygame.transform.rotate(self._sprite, -degrees)
        tw, th = turned.get_size()
        turned = pygame.transform.smoothscale(turned, (tw, max(1, int(round(th * g.b / g.a)))))
        cx = g.cx - self._x0 - self._size[0] / 2.0
        cy = g.cy - self._y0 - self._size[1] / 2.0
        # The sprite's box is centred on the rim's centre up to rounding.
        rect = turned.get_rect(center=(int(round(g.cx - cx)), int(round(g.cy - cy))))
        surface.blit(turned, rect)


class CabinWheel(object):
    """The turning steering wheel for one car: a depth camera at the
    driver's eye takes one image when the car spawns, and from the first
    frame after it the wheel turns with the car's steering.

    The steering wheel's angle is the front wheels' (CARLA's steer times
    the car's maximum steer angle) times the steering ratio, so it matches
    the G25, which turns the same way; up to half the wheel's range.
    """

    def __init__(self, vehicle, eye, size, steer_ratio, wheel_range=900.0):
        import carla
        self._vehicle = vehicle
        self._steer_ratio = steer_ratio
        self._half_range = wheel_range / 2.0
        try:
            angles = [w.max_steer_angle for w in vehicle.get_physics_control().wheels]
            self._max_steer = max(angles) or 70.0
        except RuntimeError:
            self._max_steer = 70.0
        self._depth = None
        self._image = None
        self._wheel = None
        self._failed = False
        world = vehicle.get_world()
        bp = world.get_blueprint_library().find('sensor.camera.depth')
        bp.set_attribute('image_size_x', str(size[0]))
        bp.set_attribute('image_size_y', str(size[1]))
        self._sensor = world.spawn_actor(bp, carla.Transform(eye), attach_to=vehicle,
                                         attachment_type=carla.AttachmentType.Rigid)
        self._sensor.listen(self._on_depth)

    def _on_depth(self, image):
        if self._depth is None:
            self._depth = image

    def degrees(self):
        steer = self._vehicle.get_control().steer
        angle = steer * self._max_steer * self._steer_ratio
        return max(-self._half_range, min(self._half_range, angle))

    def draw(self, display, rgb):
        """Draws the turned wheel over the driver's view (already on display)."""
        if self._failed or rgb is None:
            return
        if self._wheel is None:
            if self._depth is None:
                return
            # Once: find the wheel, then the depth camera is done.
            self._stop_sensor()
            geometry = find_wheel(depth_metres(self._depth))
            if geometry is None or geometry.mask.shape != rgb.shape[:2]:
                print('Steering wheel not found in the driver\'s view; it will not turn.')
                self._failed = True
                return
            self._wheel = WheelImage(geometry)
            self._wheel.set_background(rgb)
        self._wheel.draw(display, rgb, self.degrees())

    def _stop_sensor(self):
        if self._sensor is not None:
            self._sensor.stop()
            self._sensor.destroy()
            self._sensor = None

    def destroy(self):
        self._stop_sensor()
