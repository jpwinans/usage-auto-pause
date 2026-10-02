"""Quota adapter for both native and browser displays."""
import math
from pathlib import Path
import sys
import time

if not getattr(sys, 'frozen', False) and 'pace' not in sys.modules:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'codex'))
import pace
import claude_usage

REFRESH_SECONDS = 300
DISPLAY_MAX_AGE = REFRESH_SECONDS + 60


def reading(bucket, minutes, now, key=None):
    window = next((w for w in bucket.get('windows', [])
                   if w.get('minutes') == minutes and (key is None or w.get('key') == key)
                   and w.get('reset', 0) > now), None)
    if window is None:
        return None
    used = window.get('used')
    if not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
        return None
    ideal = pace.ideal(window, now)
    observed = bucket.get('observed_at', 0)
    return dict(used=used, leadHours=(used-ideal)/100*minutes/60,
                resetsAt=window['reset'], stale=bool(bucket.get('error')) or not 0 <= now-observed <= DISPLAY_MAX_AGE,
                observedAt=observed, label=window.get('label') or ('7D weekly' if minutes == 10080 else '5H session'))


def claude_readings(data, now):
    result = {'session': reading(data, 300, now, 'five_hour'),
              'weekly': reading(data, 10080, now, 'seven_day')}
    for w in data.get('windows', []):
        if w.get('scope'):
            result[w['key']] = reading(data, w['minutes'], now, w['key'])
    return result


def allowance_view(result, allowance):
    rows = result.get('windows', {})
    selected = rows.get('seven_day:Fable' if allowance == 'fable' else 'weekly')
    windows = {'session':rows.get('session'), 'weekly':selected}
    problem = result.get('problem') or any(r is None or r['stale'] for r in windows.values())
    return dict(result, windows=windows, summary=summary(windows, problem))


def model_view(result, model):
    if not model:
        return dict(result, windows={'session':result.get('windows',{}).get('session'), 'weekly':None},
                    summary='WAITING FOR SESSION MODEL')
    available = {k:r for k,r in result.get('windows', {}).items()
                 if claude_usage.applies(k, model)}
    weekly = [r for k,r in available.items() if k != 'session' and r]
    # All Models still limits Fable. Never substitute a model-specific allowance
    # for the account-wide quota; choose the binding applicable weekly window.
    selected = max(weekly, key=lambda r:(r['used'] > pace.HARD_PCT, r['leadHours']), default=None)
    problem = result.get('problem') or any(r is None or r['stale'] for r in available.values())
    return dict(result, windows={'session':available.get('session'), 'weekly':selected},
                summary=summary(available, problem))


def summary(windows, problem=None):
    if problem:
        return 'STALE · FRESH QUOTA REQUIRED'
    candidates = [(key, r) for key, r in windows.items() if r]
    if not candidates:
        return 'QUOTA UNAVAILABLE'
    hard = [(key, r) for key, r in candidates if r['used'] > pace.HARD_PCT]
    ahead = [(key, r) for key, r in candidates if key != 'session' and r['leadHours'] >= 8]
    if hard or ahead:
        _, r = max(hard or ahead, key=lambda pair: pair[1]['used'] if hard else pair[1]['leadHours'])
        label = r['label'].replace('7D weekly ', '').replace('7D weekly', 'weekly').replace('5H session', 'session')
        return f"PACE HOLD · {label} {r['used']:.0f}%"
    return ''


def fetch_provider(provider):
    now = time.time()
    try:
        if provider == 'Codex':
            data = pace.snapshot(pace.state_dir(), refresh_sec=REFRESH_SECONDS)
            bucket = dict(data.get('buckets', {}).get('codex') or {})
            bucket.update(error=data.get('error'), observed_at=data.get('observed_at', 0))
            result = {'weekly': reading(bucket, 10080, time.time())}
            for w in bucket.get('windows', []):
                if w['minutes'] != 10080:
                    result['session' if w['minutes'] == 300 else str(w['minutes'])] = reading(bucket,w['minutes'],time.time())
            problem = data.get('error') or not bucket.get('windows') or any(r is None or r['stale'] for r in result.values())
        else:
            data = claude_usage.snapshot(refresh_sec=REFRESH_SECONDS)
            result = claude_readings(data, time.time())
            problem = claude_usage.problem(data, time.time(), max_age=DISPLAY_MAX_AGE)
        return {'provider': provider, 'windows': result, 'checkedAt': time.time(),
                'summary': summary(result, problem), 'problem': problem, 'failed': False}
    except Exception:
        return {'provider': provider, 'windows': {}, 'checkedAt': time.time(),
                'summary': 'STALE · FRESH QUOTA REQUIRED', 'problem': True, 'failed': True}


def demo_provider(provider, state="normal"):
    """Synthetic readings for screenshots and previews; never reads accounts."""
    now = time.time()
    def row(used, hours, label):
        return dict(used=used, leadHours=(used-50)/100*hours,
                    resetsAt=now+hours*1800, observedAt=now, stale=False, label=label)
    windows = {'weekly': row(54 if provider == 'Codex' else 48, 168, '7D weekly')}
    if provider == 'Claude':
        windows.update(session=row(32, 5, '5H session'),
                       **{'seven_day:Fable': row(52, 168, '7D weekly Fable')})
    problem = None
    if state == 'unavailable':
        windows = {}
    elif state == 'expired':
        for reading in windows.values():
            reading['resetsAt'] = now-1
    elif state == 'stale':
        problem = 'demo refresh unavailable'
        for reading in windows.values():
            reading['stale'] = True
    elif state in ('hold', 'hard', 'behind', 'on_pace'):
        for key, reading in windows.items():
            hours = 5 if key == 'session' else 168
            used = {'hold': 55, 'hard': 99, 'behind': 40, 'on_pace': 50}[state]
            reading.update(used=used, leadHours=(used-50)/100*hours)
    return dict(provider=provider, windows=windows, checkedAt=now,
                summary=summary(windows, problem), problem=problem, failed=False)
