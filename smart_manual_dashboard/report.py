"""A participant's driving report, and one table of every participant.

    python -m smart_manual_dashboard.report P07     study_data/P07/report.html
    python -m smart_manual_dashboard.report --all   a report for everyone

The report reads drive.json (drive_log.py) and shows the driving score with
the rule behind each part, every metric with what it measures, and the
tips' timing, so the score can be explained in the study's report. Each run
also rewrites study_data/summary.csv, one row per participant with the
score, its parts and the metrics of both phases, to join with the
questionnaire answers on the participant id.
"""

import argparse
import csv
import html
import json
import os
import time

from smart_manual_dashboard.drive_log import DATA_DIR, FEATURE_NAMES, score as drive_score
from smart_manual_dashboard.tips import CURRICULA

# The tip ids of every level, to the feature each teaches.
TIP_FEATURES = {tip['id']: tip['learn'] for tips in CURRICULA.values() for tip in tips}

# The metrics in the report's table, with how to show them.
METRIC_ROWS = [
    ('duration_s', 'Duration', lambda v: '%.0f s' % v),
    ('distance_km', 'Distance', lambda v: '%.2f km' % v),
    ('mean_speed_kmh', 'Mean speed while moving', lambda v: '%.0f km/h' % v),
    ('collisions', 'Collisions', str),
    ('close_calls', 'Close calls (TTC)', str),
    ('min_ttc_s', 'Shortest time to collision', lambda v: '–' if v is None else '%.1f s' % v),
    ('following_s', 'Time following a car', lambda v: '%.0f s' % v),
    ('short_headway_share', 'Tailgating (of following time)', lambda v: '%.0f%%' % (100 * v)),
    ('sdlp_m', 'SDLP (lane wander)', lambda v: '–' if v is None else '%.0f cm' % (100 * v)),
    ('unsignalled_crossings', 'Lane crossings without blinker', str),
    ('speeding_share', 'Time over the speed limit', lambda v: '%.0f%%' % (100 * v)),
    ('harsh_brakes', 'Harsh brakes', str),
    ('assist_share', 'Time with each assist', lambda v: ', '.join(
        '%s %.0f%%' % (k if k != 'None' else 'none', 100 * x) for k, x in v.items()) or '–'),
    ('prompted_features_used', 'Prompted features used', lambda v: ', '.join(FEATURE_NAMES.get(f, f) for f in v) or 'none'),
    ('takeovers', 'Took over from autopilot', str),
]

SCORE_RULES = {
    'safety': '100, less 40 per collision and 15 per close call each minute, and up to 50 for tailgating',
    'lane': '100 at an SDLP of 20 cm or less, 0 at 50 cm; less 15 per lane crossing without blinker each minute',
    'speed': 'the share of the moving time at or under the limit (+5 km/h)',
    'smooth': '100, less 25 per harsh brake each minute',
    'features': 'the share of the features the tips showed that were used again; left out when no tip was shown',
}


def _e(v):
    return html.escape(str(v))


def _fmt(v, fmt='%d'):
    return '–' if v is None else fmt % v


def load(participant, data_dir=None):
    """The participant's drive.json, scored again with the current rules; None if there is none."""
    path = os.path.join(data_dir or DATA_DIR, participant, 'drive.json')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        drive = json.load(f)
    for phase in drive['phases'].values():
        if phase.get('score') is not None:
            phase['score'] = drive_score(phase['metrics'])
    return drive


def tip_timing(drive):
    """[(tip id, shown at s, done after s or None)]."""
    shown, rows = {}, []
    for t in drive.get('tips', []):
        if t['kind'] == 'tip-shown':
            shown[t['tip']] = t['t']
        elif t['tip'] in shown:
            at = shown.pop(t['tip'])
            rows.append((t['tip'], at, t['t'] - at))
    return rows + [(tip, at, None) for tip, at in shown.items()]


# ==============================================================================
# -- report.html ---------------------------------------------------------------
# ==============================================================================

CSS = """
:root { --accent: #3E6AE1; --ink: #171A20; --muted: #5C5E62; --line: #E4E5E7; --good: #1E9E52; --warn: #D97706; }
* { box-sizing: border-box; }
body { margin: 0; background: #F2F3F5; color: var(--ink); font: 15px/1.45 'Segoe UI', system-ui, sans-serif; }
.page { max-width: 980px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 30px; margin: 0; } h2 { font-size: 20px; margin: 36px 0 10px; }
.sub { color: var(--muted); }
.card { background: #FFFFFF; border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-top: 12px; overflow-x: auto; }
.kpis { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; }
.kpi { flex: 1 1 160px; background: #FFFFFF; border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; }
.kpi b { display: block; font-size: 28px; font-variant-numeric: tabular-nums; }
.kpi span { color: var(--muted); font-size: 13px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
td.n { text-align: right; white-space: nowrap; }
.why { color: var(--muted); font-size: 13px; }
.bar { height: 10px; border-radius: 5px; background: var(--line); min-width: 120px; }
.bar div { height: 10px; border-radius: 5px; }
.no { color: var(--warn); font-weight: 600; }
@media print { body { background: #FFFFFF; } .card, .kpi { break-inside: avoid; } }
"""


def _bar(score):
    colour = '#1E9E52' if score >= 80 else ('#3E6AE1' if score >= 50 else '#D97706')
    return '<div class="bar"><div style="width: %d%%; background: %s"></div></div>' % (score, colour)


def report_html(participant, drive):
    phases = drive['phases']
    free, guided = phases.get('free'), phases.get('guided')
    free_s = free.get('score') if free else None
    guided_s = guided.get('score') if guided else None
    h = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width, initial-scale=1">'
         '<title>Participant %s</title><style>%s</style></head><body><div class="page">' % (_e(participant), CSS)]
    h.append('<h1>Participant %s</h1><div class="sub">Context-aware manual user test · driving in CARLA · '
             'drive started %s, report written %s</div>'
             % (_e(participant), _e(drive.get('started', '')), time.strftime('%Y-%m-%d %H:%M')))

    h.append('<div class="kpis">')
    h.append('<div class="kpi"><span>Level</span><b>%s</b><span>the manual\'s tips</span></div>' % _e(drive.get('level') or '–'))
    h.append('<div class="kpi"><span>Driving score, free drive</span><b>%s</b><span>out of 100</span></div>'
             % _fmt(free_s['total'] if free_s else None))
    h.append('<div class="kpi"><span>Driving score, guided</span><b>%s</b><span>out of 100, with tips</span></div>'
             % _fmt(guided_s['total'] if guided_s else None))
    done = [t for t in tip_timing(drive) if t[2] is not None]
    h.append('<div class="kpi"><span>Tips done</span><b>%d</b><span>of %d shown</span></div>'
             % (len(done), len(tip_timing(drive))))
    h.append('</div>')

    h.append('<h2>Driving score</h2>')
    if not free_s and not guided_s:
        h.append('<div class="card sub">No drive scored yet.</div>')
    else:
        h.append('<div class="card"><p class="sub" style="margin-top: 0">Five parts of 0 to 100, each a plain rule '
                 'over CARLA measurements; the total is their mean. The free drive is the end of the test, '
                 'without tips (%s); the guided phase, with the manual, is beside it to compare.</p><table>'
                 '<tr><th>Part</th><th>Free drive</th><th></th><th>Guided</th><th>What happened (free drive)</th>'
                 '<th>Rule</th></tr>' % ('%.0f s' % free['metrics']['duration_s'] if free else 'not driven'))
        free_parts = {p['key']: p for p in free_s['parts']} if free_s else {}
        guided_parts = {p['key']: p for p in guided_s['parts']} if guided_s else {}
        for key in ('safety', 'lane', 'speed', 'smooth', 'features'):
            fp, gp = free_parts.get(key), guided_parts.get(key)
            if fp is None and gp is None:
                continue
            h.append('<tr><td><b>%s</b></td><td class="n">%s</td><td>%s</td><td class="n">%s</td><td>%s</td>'
                     '<td class="why">%s</td></tr>' % (
                         _e((fp or gp)['name']), _fmt(fp and fp['score']), _bar(fp['score']) if fp else '',
                         _fmt(gp and gp['score']), _e(fp['note']) if fp else '–', _e(SCORE_RULES[key])))
        h.append('<tr><td><b>Total</b></td><td class="n"><b>%s</b></td><td></td><td class="n">%s</td><td></td><td></td></tr>'
                 % (_fmt(free_s and free_s['total']), _fmt(guided_s and guided_s['total'])))
        h.append('</table></div>')

    definitions = drive.get('definitions', {})
    h.append('<h2>Driving metrics</h2><div class="card"><table><tr><th>Metric</th><th>Guided</th>'
             '<th>Free drive</th><th>What it measures</th></tr>')
    for key, name, show in METRIC_ROWS:
        cells = ['–' if phase is None or key not in phase['metrics'] else _e(show(phase['metrics'][key]))
                 for phase in (guided, free)]
        h.append('<tr><td>%s</td><td class="n">%s</td><td class="n">%s</td><td class="why">%s</td></tr>'
                 % (_e(name), cells[0], cells[1], _e(definitions.get(key, ''))))
    h.append('</table></div>')

    h.append('<h2>Tips</h2><div class="card"><table><tr><th>Tip</th><th>Shown at</th><th>Done after</th></tr>')
    rows = tip_timing(drive)
    for tip, at, after in rows:
        h.append('<tr><td>%s</td><td class="n">%.0f s</td><td class="n%s">%s</td></tr>' % (
            _e(FEATURE_NAMES.get(TIP_FEATURES.get(tip), tip)), at, '' if after is not None else ' no',
            '%.0f s' % after if after is not None else 'not done'))
    if not rows:
        h.append('<tr><td colspan="3" class="sub">No tips were shown.</td></tr>')
    h.append('</table></div></div></body></html>')
    return ''.join(h)


# ==============================================================================
# -- summary.csv ---------------------------------------------------------------
# ==============================================================================


def summary_row(participant, drive):
    row = dict(participant=participant, level=drive.get('level'), started=drive.get('started'),
               tips_shown=len(tip_timing(drive)),
               tips_done=sum(1 for t in tip_timing(drive) if t[2] is not None))
    for name in ('free', 'guided'):
        phase = drive['phases'].get(name)
        s = phase.get('score') if phase else None
        row['score_%s' % name] = s['total'] if s else None
        for key in ('safety', 'lane', 'speed', 'smooth', 'features'):
            row['%s_%s' % (name, key)] = next((p['score'] for p in s['parts'] if p['key'] == key), None) if s else None
        m = phase['metrics'] if phase else {}
        for key in ('duration_s', 'distance_km', 'collisions', 'close_calls', 'short_headway_share', 'sdlp_m',
                    'unsignalled_crossings', 'speeding_share', 'harsh_brakes', 'takeovers'):
            row['%s_%s' % (name, key)] = m.get(key)
        row['%s_features_used' % name] = len(m['prompted_features_used']) if 'prompted_features_used' in m else None
        row['%s_features_prompted' % name] = len(m['prompted_features']) if 'prompted_features' in m else None
        row['%s_assist_share' % name] = (round(1 - m['assist_share'].get('None', 0.0), 3)
                                         if m.get('assist_share') else None)
    return row


def participants(data_dir=None):
    data_dir = data_dir or DATA_DIR
    if not os.path.isdir(data_dir):
        return []
    return [p for p in sorted(os.listdir(data_dir)) if os.path.isfile(os.path.join(data_dir, p, 'drive.json'))]


def write_summary(data_dir=None):
    data_dir = data_dir or DATA_DIR
    rows = [summary_row(p, load(p, data_dir)) for p in participants(data_dir)]
    path = os.path.join(data_dir, 'summary.csv')
    if rows:
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return path, len(rows)


def write_report(participant, data_dir=None):
    drive = load(participant, data_dir)
    if drive is None:
        return None
    path = os.path.join(data_dir or DATA_DIR, participant, 'report.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(report_html(participant, drive))
    return path


def main():
    argparser = argparse.ArgumentParser(description='Context-aware manual driving report')
    argparser.add_argument('participant', nargs='?', help='participant id, e.g. P07')
    argparser.add_argument('--all', action='store_true', help='a report for every participant')
    argparser.add_argument('--data', default=None, help='data folder (default: %s)' % DATA_DIR)
    args = argparser.parse_args()
    if args.all:
        names = participants(args.data)
    elif args.participant:
        names = [args.participant]
    else:
        argparser.error('give a participant id or --all')
    for p in names:
        path = write_report(p, args.data)
        print('%s: %s' % (p, path or 'no drive.json yet'))
    path, n = write_summary(args.data)
    print('Summary of %d participant%s: %s' % (n, '' if n == 1 else 's', path))


if __name__ == '__main__':
    main()
