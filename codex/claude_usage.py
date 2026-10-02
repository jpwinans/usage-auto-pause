"""Shared Claude quota snapshot for displays, statusline, and gates.

Credentials stay in the existing hook's Keychain helper. Only quota data is cached.
"""
import email.utils
import fcntl
import importlib.util
import json
import os
import sys
import hashlib
import math
from pathlib import Path
import time
import urllib.request
import urllib.error

CACHE_DIR = Path(os.environ.get('CLAUDE_PACING_CACHE_DIR', str(Path.home() / '.cache' / 'llm-pacing'))).expanduser()
# py2app copies the helper as a resource; source runs use the sibling folder.
DEFAULT_HOOK = (Path(sys.executable).resolve().parent.parent / 'Resources' / 'claude_pace.py'
                if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1] / 'claude' / 'pace.py')
HOOK = Path(os.environ.get('CLAUDE_PACING_HOOK', str(DEFAULT_HOOK))).expanduser()
REFRESH_SEC = 60
MAX_AGE_SEC = 90
SCHEMA = 2


def helper():
    spec = importlib.util.spec_from_file_location('claude_pacing_source', HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize(source, iso_epoch):
    rows = {}
    for key, row in source.items():
        if (key == 'five_hour' or key.startswith('seven_day')) and isinstance(row, dict) and any(field in row for field in ('utilization', 'used_percentage', 'resets_at')):
            if key not in ('five_hour', 'seven_day') and row.get('utilization', row.get('used_percentage')) is None and row.get('resets_at') is None:
                continue
            name = key.removeprefix('seven_day').lstrip(':_') if key != 'five_hour' else ''
            canonical = 'five_hour' if key == 'five_hour' else 'seven_day' + (':' + name if name else '')
            rows[canonical] = (row.get('utilization', row.get('used_percentage')), row.get('resets_at'))
    for row in source.get('limits') or []:
        scope = row.get('scope') or {}
        model = (scope.get('model') or {}).get('display_name')
        surface = (scope.get('surface') or {}).get('display_name')
        kind = row.get('kind', '')
        if kind == 'session' and not scope:
            key = 'five_hour'
        elif kind == 'weekly_all' and not scope:
            key = 'seven_day'
        elif kind.startswith('weekly') and (model or surface):
            # Surface limits conservatively apply to all local Claude models.
            key = 'seven_day:' + model if model else 'seven_day:surface:' + surface
        else:
            continue
        rows[key] = (row.get('percent'), row.get('resets_at'))
    windows = []
    for key, (used, reset) in rows.items():
        if isinstance(reset, str):
            reset = iso_epoch(reset)
        if (all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) for v in (used, reset))
                and 0 <= used <= 100 and reset > 0):
            name = key.removeprefix('seven_day:') if ':' in key else None
            windows.append({'key': key, 'used': used, 'minutes': 300 if key == 'five_hour' else 10080,
                            'reset': reset, 'scope': name,
                            'label': '5H session' if key == 'five_hour' else '7D weekly ' + (name or 'all models')})
    return {'name': 'Claude', 'windows': windows,
            'complete': len(windows) == len(rows) and {'five_hour', 'seven_day'} <= {w['key'] for w in windows}}


def applies(key, model):
    if ':' not in key or key.startswith('seven_day:surface:'):
        return True
    return not model or key.split(':', 1)[1].lower() in model.lower()


def for_model(data, model):
    return dict(data, windows=[w for w in data.get('windows', [])
                              if applies(w.get('key', ''), model)])


def active_session(directory=CACHE_DIR):
    try:
        data = json.loads((directory / 'active-session.json').read_text())
        return data if isinstance(data, dict) and data.get('model') else {}
    except (OSError, ValueError):
        return {}


def record_session(payload, model, directory=CACHE_DIR):
    """Periodic statusline redraws must not steal focus from an active session.

    Activity comes from its transcript mtime, or a changed model. Subagents do
    not replace their parent session in the standalone display.
    """
    key = payload.get('session_id') or payload.get('transcript_path')
    if not key or not model or payload.get('agent_id'):
        return
    now = time.time()
    try:
        activity = min(now, Path(payload['transcript_path']).stat().st_mtime)
    except (OSError, KeyError, TypeError):
        activity = 0
    directory.mkdir(parents=True, exist_ok=True)
    session_dir = directory / 'sessions'
    session_dir.mkdir(exist_ok=True)
    path = session_dir / (hashlib.sha256(str(key).encode()).hexdigest() + '.json')
    with (directory / 'sessions.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = {}
        if previous and previous.get('model') != model:
            activity = now
        if not previous and not activity:
            activity = now
        activity = max(activity, previous.get('active_at', 0))
        row = {'session_key':str(key), 'model':model, 'active_at':activity}
        path.write_text(json.dumps(row))
        active = active_session(directory)
        if activity >= active.get('active_at', 0):
            temporary = directory / 'active-session.json.tmp'
            temporary.write_text(json.dumps(row))
            temporary.replace(directory / 'active-session.json')


def display_model():
    return active_session().get('model')


def as_limits(data):
    return {w['key']: {'used_percentage': w['used'], 'resets_at': w['reset']}
            for w in data.get('windows', []) if 'key' in w}


def retry_after_sec(header, now, default=300, floor=REFRESH_SEC, cap=3600):
    """Seconds to wait from a Retry-After header: delta-seconds or an HTTP-date
    (RFC 9110 10.2.3). Absent or unparseable waits the default."""
    try:
        delay = float(header)
    except (TypeError, ValueError):
        try:
            delay = email.utils.parsedate_to_datetime(header).timestamp() - now
        except (TypeError, ValueError, IndexError):
            delay = default
    if not math.isfinite(delay):
        delay = default
    return min(max(delay, floor), cap)


def _short(seconds):
    return f'{seconds / 3600:.0f}h' if seconds >= 5400 else f'{max(1, round(seconds / 60))}m'


def problem(data, now, max_age=MAX_AGE_SEC):
    """None when the reading is fresh and whole; else a terse reason for the statusline."""
    error = data.get('error')
    if error:
        detail = error.removeprefix('Claude usage refresh unavailable: ').removeprefix('Claude ')
        wait = data.get('retry_after', 0) - now
        return f'{detail}, retry {_short(wait)}' if wait > 0 else detail
    if not data.get('complete') or data.get('schema') != SCHEMA:
        return 'incomplete'
    age = now - data.get('observed_at', 0)
    if not 0 <= age <= max_age:
        return f'{_short(age)} old' if age > 0 else 'clock skew'
    if any(w['reset'] <= now for w in data.get('windows', [])):
        return 'reset passed'
    return None


def snapshot(directory=CACHE_DIR, refresh_sec=REFRESH_SEC, force=False):
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'claude-v2.json'
    with (directory / 'claude-v2.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(cache.read_text())
            if not isinstance(data, dict):
                raise ValueError('Invalid cache')
        except (OSError, ValueError):
            data = {'name': 'Claude', 'windows': [], 'checked_at': 0}
        now = time.time()
        if data.get('schema') == SCHEMA and now < data.get('retry_after', 0):
            return data
        if not force and data.get('schema') == SCHEMA and 0 <= now - data.get('checked_at', 0) < refresh_sec:
            return data
        try:
            source = helper()
            if not data.get('windows'):
                fallback = normalize(source.read_limits(), source.iso_epoch)
                data.update(fallback)  # display only; never mark fallback fresh
            token = source.keychain_token(now)
            if not token:
                raise RuntimeError('Claude login unavailable or expired')
            request = urllib.request.Request(source.USAGE_API, headers={
                'Authorization': 'Bearer ' + token,
                'anthropic-beta': 'oauth-2025-04-20'})
            with urllib.request.urlopen(request, timeout=10) as response:
                bucket = normalize(json.load(response), source.iso_epoch)
            if not bucket['complete']:
                raise RuntimeError('Claude quota response incomplete')
            known = {w.get('key') for w in data.get('windows', []) if w.get('reset', 0) > now and w.get('scope')}
            present = {w['key'] for w in bucket['windows']}
            if known - present:
                raise RuntimeError('Claude model quota missing from refresh')
            data.update(bucket, observed_at=time.time(), error=None, retry_after=0)
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError) as exc:
            detail = str(exc) if isinstance(exc, RuntimeError) else 'HTTP '+str(exc.code) if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            data['error'] = 'Claude usage refresh unavailable: ' + detail
            header = exc.headers.get('Retry-After') if isinstance(exc, urllib.error.HTTPError) and exc.headers else None
            # A 429 always backs off; a 503 only when the server says how long.
            if isinstance(exc, urllib.error.HTTPError) and (exc.code == 429 or (exc.code == 503 and header)):
                data['retry_after'] = time.time() + retry_after_sec(header, time.time())
        data.update(checked_at=time.time(), schema=SCHEMA)
        temporary = directory / 'claude-v2.json.tmp'
        temporary.write_text(json.dumps(data))
        temporary.replace(cache)
        return data
