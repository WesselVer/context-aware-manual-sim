# Context-aware manual: driving simulator

A user study of an AI vehicle manual that teaches a car's features while you
drive, at calm moments, the way a game tutorial does. It runs on the
[CARLA](https://carla.org) simulator: you drive from the driver's seat with a
Logitech G25 wheel, and a second screen shows the car's dashboard, where the
manual's tips appear.

- `manual_control_driver.py`: the drive. The driver's-seat view, the G25,
  drive modes and regen, cruise control, adaptive cruise and an autopilot,
  and the user-test controls.
- `steering_wheel.py`: the G25 (axes, buttons, H-shifter as P/R/N/D) and its
  force feedback.
- `smart_manual_dashboard/`: the second-screen dashboard, the context-aware
  tips (`tips.py`), the drive log and driving score (`drive_log.py`) and the
  participant report (`report.py`).

## Running it

CARLA 0.9.16 (Windows), with its Python client (the release ships a wheel
for Python 3.12 only) in an environment beside `pygame` and `numpy`:

```bash
CarlaUE4.exe -windowed -ResX=1280 -ResY=720
```

If the drive judders while the HUD reads 60 fps, the camera images are
arriving late: the HUD counts the server's ticks, not the images. Close
anything else using the GPU; `-quality-level=Low` is the fallback.

```bash
python manual_control_driver.py --tm-port 8010
```

It loads Town04 (a small town inside a highway loop) unless `--map` names
another. `--participant P07` logs a user test to `study_data/P07/`, and
`python -m smart_manual_dashboard.report P07` writes the report. The script's
`--help` lists every key and option.

## The G25

| Control | Does |
| --- | --- |
| Shifter, 1st / 3rd / 4th | P / D / R; anywhere else N |
| Red buttons, left to right | cruise control, adaptive cruise, autopilot |
| Silver panel, top / bottom | set speed +5 / −5 km/h |
| D-pad up / down | following gap shorter / longer |
| Paddles | drive mode down / up (Eco, Normal, Dynamic) |
| Wheel's top buttons | regen down / up |

An assist's red button switches it on, resumes it after braking, and switches
it off. On the keyboard, B starts the manual, P skips a tip (for testing) and
holding R stops it.
