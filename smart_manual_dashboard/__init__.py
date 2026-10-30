# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""Second-screen dashboard for the context-aware manual study.

The dashboard is dashboard.html in this directory, served from a local HTTP
server and shown in a browser app window (Edge or Chrome, no tabs or address
bar), full screen on the second monitor when there is one. The driving
script pushes values with Dashboard.update(); the page receives them as
server-sent events, so nothing beyond the standard library is needed.

    dashboard = Dashboard()
    dashboard.start()
    dashboard.update(speed=87)
    ...
    dashboard.close()

Run this package on its own (python -m smart_manual_dashboard) for a demo
that sweeps the speed without CARLA.
"""

import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
import webbrowser

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:
    raise RuntimeError('the dashboard needs Python 3.7 or newer')


HERE = os.path.dirname(os.path.abspath(__file__))

# Only these files are served; anything else is a 404.
FILES = {
    '/': ('dashboard.html', 'text/html; charset=utf-8'),
    '/fonts/Barlow-Regular.woff2': ('fonts/Barlow-Regular.woff2', 'font/woff2'),
    '/fonts/Barlow-SemiBold.woff2': ('fonts/Barlow-SemiBold.woff2', 'font/woff2'),
    '/fonts/Barlow-Bold.woff2': ('fonts/Barlow-Bold.woff2', 'font/woff2'),
    '/fonts/BarlowSemiCondensed-SemiBold.woff2': ('fonts/BarlowSemiCondensed-SemiBold.woff2', 'font/woff2'),
    '/fonts/BarlowSemiCondensed-Bold.woff2': ('fonts/BarlowSemiCondensed-Bold.woff2', 'font/woff2'),
}

PROFILE_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.cache'),
    'smart-manual-dashboard', 'browser-profile')

# The page redraws at most this often, however fast update() is called.
MAX_RATE_HZ = 30.0


# ==============================================================================
# -- monitors ------------------------------------------------------------------
# ==============================================================================


def get_monitors():
    """Return the monitors as (left, top, width, height), primary first.

    Only Windows is detected; elsewhere this returns an empty list and the
    dashboard opens as an ordinary window.
    """
    if os.name != 'nt':
        return []
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    monitors = []
    user32 = ctypes.windll.user32

    def callback(hmonitor, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            primary = bool(info.dwFlags & 1)  # MONITORINFOF_PRIMARY
            monitors.append((not primary, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return 1

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.EnumDisplayMonitors(None, None, proc(callback), 0)
    return [rect for _, rect in sorted(monitors, key=lambda m: m[0])]


def find_browser():
    """Return the path of a Chromium browser (Edge or Chrome), or None."""
    names = ['msedge', 'chrome', 'google-chrome', 'chromium', 'chromium-browser']
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    if os.name == 'nt':
        for root in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'),
                     os.environ.get('LOCALAPPDATA')):
            if not root:
                continue
            for rel in (r'Microsoft\Edge\Application\msedge.exe',
                        r'Google\Chrome\Application\chrome.exe'):
                path = os.path.join(root, rel)
                if os.path.isfile(path):
                    return path
    return None


# ==============================================================================
# -- Dashboard -----------------------------------------------------------------
# ==============================================================================


class Dashboard(object):
    """Serves dashboard.html and streams state to it."""

    def __init__(self, port=0):
        self._state = {}
        self._version = 0
        self._closed = False
        self._cond = threading.Condition()
        self._profile = None
        self.connected = threading.Event()
        self._server = ThreadingHTTPServer(('127.0.0.1', port), self._make_handler())
        self._server.daemon_threads = True
        self.url = 'http://127.0.0.1:%d/' % self._server.server_address[1]

    def start(self, monitor=None, open_window=True):
        """Start serving and open the dashboard window.

        monitor is an index into get_monitors(); None picks the second
        monitor, or the primary one as a window when there is only one.
        """
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        if open_window:
            self._open_window(monitor)
        return self

    def update(self, **values):
        """Set dashboard values (speed=..., gear=..., see DEFAULTS in dashboard.html)."""
        with self._cond:
            changed = {k: v for k, v in values.items() if self._state.get(k, object()) != v}
            if changed:
                self._state.update(changed)
                self._version += 1
                self._cond.notify_all()

    def close(self):
        """Close the dashboard window and stop serving."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        if self._profile:
            # The page closes its own window on the 'close' event; the browser
            # deletes the lockfile in its profile once it has exited.
            deadline = time.time() + 3.0
            while self._browser_running() and time.time() < deadline:
                time.sleep(0.2)
            if self._browser_running():
                self._kill_browser()
        self._server.shutdown()
        self._server.server_close()

    def _browser_running(self):
        return os.path.exists(os.path.join(self._profile, 'lockfile'))

    def _kill_browser(self):
        # The process started by Popen is often not the browser any more:
        # Edge relaunches itself, so find the processes by their profile.
        if os.name == 'nt':
            script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%s*' } "
                      "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                      "-ErrorAction SilentlyContinue }" % self._profile)
            subprocess.call(['powershell', '-NoProfile', '-Command', script],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.call(['pkill', '-f', self._profile],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _open_window(self, monitor):
        monitors = get_monitors()
        if monitor is None:
            monitor = 1 if len(monitors) > 1 else None
        elif not 0 <= monitor < len(monitors):
            print('Dashboard: there is no monitor %d, opening a window instead' % monitor)
            monitor = None

        browser = find_browser()
        if browser is None:
            print('Dashboard: no Edge or Chrome found, opening %s in the default browser' % self.url)
            webbrowser.open(self.url)
            return

        # A profile of its own makes this a separate browser instance, so the
        # window flags apply even when the browser is already open, and
        # closing it does not touch the user's own browser windows. It is kept
        # between runs so the browser's welcome screen only shows once.
        self._profile = PROFILE_DIR
        if self._browser_running():
            self._kill_browser()  # left open by a run that did not exit cleanly
            time.sleep(0.5)
        args = [browser, '--app=' + self.url, '--user-data-dir=' + self._profile,
                '--no-first-run', '--no-default-browser-check',
                '--disable-features=Translate',
                # The tip chime plays without a click on the page.
                '--autoplay-policy=no-user-gesture-required']
        if monitor is not None:
            left, top, _, _ = monitors[monitor]
            args += ['--window-position=%d,%d' % (left + 100, top + 100),
                     '--window-size=960,540', '--start-fullscreen']
            print('Dashboard: %s full screen on monitor %d' % (self.url, monitor))
        else:
            args += ['--window-size=960,540']
            print('Dashboard: %s (one monitor found, opened as a window)' % self.url)
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _make_handler(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split('?', 1)[0]
                if path == '/events':
                    self._events()
                elif path in FILES:
                    name, mime = FILES[path]
                    with open(os.path.join(HERE, name), 'rb') as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def _events(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                dashboard.connected.set()
                seen = -1
                try:
                    while True:
                        with dashboard._cond:
                            dashboard._cond.wait_for(
                                lambda: dashboard._version != seen or dashboard._closed, timeout=5.0)
                            if dashboard._closed:
                                self.wfile.write(b'event: close\ndata: {}\n\n')
                                self.wfile.flush()
                                return
                            fresh = dashboard._version != seen
                            seen = dashboard._version
                            state = dict(dashboard._state)
                        if fresh:
                            self.wfile.write(('data: %s\n\n' % json.dumps(state)).encode('utf-8'))
                        else:
                            self.wfile.write(b': keep-alive\n\n')
                        self.wfile.flush()
                        time.sleep(1.0 / MAX_RATE_HZ)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

            def log_message(self, *args):
                pass

        return Handler


def focus_window(hwnd):
    """Bring a window back to the front once the dashboard has opened.

    The browser window takes the focus when it appears; the driving window
    needs it for the keyboard. Windows only lets the foreground process hand
    over the focus, and pressing and releasing Alt lifts that lock.
    """
    if os.name != 'nt' or not hwnd:
        return
    user32 = ctypes.windll.user32
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)


# ==============================================================================
# -- demo ----------------------------------------------------------------------
# ==============================================================================


def _demo():
    import argparse
    import math
    from smart_manual_dashboard.tips import Tips
    argparser = argparse.ArgumentParser(description='Smart manual dashboard demo (no CARLA)')
    argparser.add_argument('--port', default=0, type=int, help='HTTP port (default: any free port)')
    argparser.add_argument('--monitor', default=None, type=int,
                           help='monitor index (default: the second monitor if there is one)')
    argparser.add_argument('--no-window', action='store_true', help='only serve, print the URL')
    args = argparser.parse_args()

    dashboard = Dashboard(args.port).start(args.monitor, open_window=not args.no_window)
    print('Serving %s, Ctrl+C to stop' % dashboard.url)
    try:
        # The Beginner tips: steady at about 50 km/h, with a junction coming
        # up for 8 s of every 40 that holds the first one back.
        t0 = time.time()
        tips = Tips()
        tips.start('Beginner')
        while True:
            t = time.time() - t0
            speed = 50 + 1.5 * math.sin(t / 3.0)
            state = dict(activeAssist='None', gear='D', brake=0)
            context = dict(speed=speed, yaw_rate=0.0, cars_near=0, walkers_near=0,
                           junction_ahead=20.0 if t % 40 > 32 else None, indicating=False)
            state.update(tips.update(state, context))
            dashboard.update(speed=int(round(speed)), **state)
            time.sleep(1.0 / 60)
    except KeyboardInterrupt:
        pass
    finally:
        dashboard.close()

