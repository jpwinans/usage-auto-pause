#!/usr/bin/env python3
"""Codex quota pacing. Uses the local app-server protocol, never reads tokens."""
import argparse
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import claude_usage

REFRESH_SEC = 60
METER_REFRESH_SEC = 300
POLL_SEC = 5
MAX_HOLD_SEC = 7 * 24 * 3600 + 60  # Less than the configured eight-day hook timeout.
HARD_PCT = 98
MAX_AGE_SEC = 90
WEEKLY_LEAD_TRIGGER_HOURS, WEEKLY_LEAD_RESUME_HOURS = 8, 4


def rpc(method="account/rateLimits/read", params=None, timeout=20):
    """Make no model requests: initialize, read quota, then close the server."""
    proc = subprocess.Popen(
        [shutil.which("codex") or "codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline, pending = time.monotonic() + timeout, b""

    def send(message):
        proc.stdin.write((json.dumps(message) + "\n").encode())
        proc.stdin.flush()

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "codex_pacing", "version": "0.1.0"}}})
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Codex app-server closed before returning limits")
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                message = json.loads(line)
                if message.get("id") not in (1, 2):
                    continue
                if "error" in message:
                    raise RuntimeError("Codex quota query failed: " +
                                       str(message["error"].get("message", "unknown error")))
                if message["id"] == 1:
                    send({"method": "initialized", "params": {}})
                    request = {"id": 2, "method": method}
                    if params is not None:
                        request["params"] = params
                    send(request)
                else:
                    return message["result"]
        raise TimeoutError("Codex quota query timed out")
    finally:
        selector.close()
        proc.stdin.close()
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()


def normalize(result):
    """Keep quota fields only. Do not assume primary means five hours."""
    buckets = result.get("rateLimitsByLimitId") or {}
    single = result.get("rateLimits") or {}
    if not buckets and single:
        buckets = {single.get("limitId") or "codex": single}
    out = {}
    for key, bucket in buckets.items():
        windows = []
        for slot in ("primary", "secondary"):
            w = bucket.get(slot) or {}
            values = [w.get(k) for k in ("usedPercent", "windowDurationMins", "resetsAt")]
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in values):
                continue
            used, minutes, reset = values
            if not 0 <= used <= 100 or minutes <= 0 or reset <= 0:
                continue
            windows.append({"used": used, "minutes": minutes, "reset": reset})
        out[key] = {"name": bucket.get("limitName"), "windows": windows,
                    "complete": len(windows) == sum(bool(bucket.get(slot)) for slot in ("primary", "secondary"))}
    return out


def state_dir():
    return Path(os.environ.get("CODEX_PACING_DIR") or
                str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "usage-pacing"))


def read_cache(directory):
    try:
        data = json.loads((directory / "limits.json").read_text())
        if isinstance(data.get("buckets"), dict) and isinstance(data.get("checked_at"), (int, float)):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"buckets": {}, "checked_at": 0}


def atomic_json(path, data):
    fd, name = tempfile.mkstemp(prefix=".limits-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def snapshot(directory, force=False, refresh_sec=REFRESH_SEC):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serialize refreshes across all sessions. Readers cannot see partial JSON.
    with (directory / "refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_cache(directory)
        if not force and time.time() - data["checked_at"] < refresh_sec:
            return data
        try:
            buckets = normalize(rpc())
            if not buckets or not any(b["windows"] for b in buckets.values()):
                raise RuntimeError("Codex returned no usable quota windows")
            data = {"buckets": buckets, "observed_at": time.time(),
                    "checked_at": time.time(), "error": None}
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            # Preserve the last successful reading; throttle failed refreshes too.
            data.update(checked_at=time.time(), error=str(exc))
        atomic_json(directory / "limits.json", data)
        return data


def ideal(w, now):
    return max(0, min(100, 100 * (1 - (w["reset"] - now) / (w["minutes"] * 60))))


def evaluate(windows, now, holds=None):
    """Keep the bucket latched until every fresh weekly lead is at most +4h.

    A reset timestamp is quota metadata, not the identity of an active hold.
    Provider corrections must not turn a latched +6h reading into a new call.
    """
    holds = {} if holds is None else holds
    pauses, weekly = [], []
    for w in windows:
        used, minutes, reset = w["used"], w["minutes"], w["reset"]
        if reset <= now:
            continue
        label = w.get("label") or ("weekly" if minutes == 10080 else "session" if minutes == 300 else f"{minutes:g}m")
        if minutes == 10080:
            lead = (used - ideal(w, now)) / 100 * minutes / 60
            weekly.append((lead, w))
        if used > HARD_PCT:
            pauses.append((reset, f"{label}-hard", f"{label} {used:g}%, until reset"))
    if weekly:
        lead, w = max(weekly, key=lambda item: item[0])
        if lead >= WEEKLY_LEAD_TRIGGER_HOURS - 1e-9 or (
                holds and lead > WEEKLY_LEAD_RESUME_HOURS + 1e-9):
            holds.update(reset=w["reset"])
            resume = w["reset"] - w["minutes"] * 60 * (1 - w["used"] / 100) - WEEKLY_LEAD_RESUME_HOURS * 3600
            pauses.append((min(w["reset"], resume), "weekly-lead", f"weekly {lead:.1f}h ahead of pace"))
        else:
            holds.clear()
    return max(pauses) if pauses else None


def weekly_pause(directory, bucket, windows, now, unavailable=False, persist=True, active_hold=None):
    """Serialize account-wide hysteresis; displays read it without changing it.

    Missing/stale data cannot clear an established hold, even at its old ETA.
    Only a fresh live weekly reading (including a new week) can release it.
    """
    def decide():
        path = directory / 'hold.json'
        try:
            state = json.loads(path.read_text())
        except FileNotFoundError:
            state = {}
        if not isinstance(state, dict) or not isinstance(state.get(bucket, {}), dict):
            raise ValueError('Invalid pacing hold state')
        held = dict(state.get(bucket, {}))
        before = dict(held)
        # A waiting hook also remembers its own hold. An older process or another
        # writer clearing the shared file cannot prematurely release this call.
        if not held and active_hold:
            held.update(active_hold)
        weekly = any(w['minutes'] == 10080 and w['reset'] > now for w in windows)
        if unavailable or (held and not weekly):
            pause = stale_pause(windows, now)
            if held:
                pending = (now + POLL_SEC, 'weekly-lead-unverified',
                           'weekly hold: awaiting fresh lead <=4h')
                pause = max(pause, pending) if pause else pending
        else:
            pause = evaluate(windows, now, held)
        if persist and held != before:
            if held:
                state[bucket] = held
            else:
                state.pop(bucket, None)
            atomic_json(path, state)
        if persist and active_hold is not None:
            active_hold.clear()
            active_hold.update(held)
        return pause
    if not persist:
        return decide()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'hold.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return decide()


def stale_pause(windows, now):
    """Old pacing leads are ignored; only known usage >98% can hold until reset."""
    high = [w for w in windows if isinstance(w, dict)
            and isinstance(w.get('used'), (int,float)) and HARD_PCT < w['used'] <= 100
            and isinstance(w.get('reset'), (int,float)) and w['reset'] > now]
    return evaluate(high, now)


def bucket_for(model):
    # Reserve is a fallback bucket, not a replacement for the ordinary quota.
    return "codex_bengalfox" if "spark" in model.lower() else "codex"


def fmt_time(epoch):
    return datetime.fromtimestamp(epoch).astimezone().strftime("%a %H:%M %Z")


def render(data, bucket="codex", now=None, color=False, override=False, directory=None):
    now = time.time() if now is None else now
    windows = (data.get("buckets", {}).get(bucket) or {}).get("windows", [])
    parts = [bucket + (f" ({data.get('model', 'all reported allowances')})" if bucket == 'claude' else '')]
    if not windows:
        parts.append("7D weekly: not reported")
    for w in windows:
        minutes = w['minutes']
        label = w.get('label') or ("7D weekly" if minutes == 10080 else "5H session" if minutes == 300 else f"{minutes:g}m")
        if now >= w["reset"]:
            parts.append(f"{label}: reset passed; awaiting refresh")
            continue
        used, pace = w["used"], ideal(w, now)
        filled = min(10, int(used / 10))
        bar = "▰" * filled + "▱" * (10 - filled)
        lead = (used - pace) / 100 * w["minutes"] / 60
        text = f"{label} [{bar}] used {used:g}% | pace {pace:.1f}% | {lead:+.1f}h"
        if color:
            tone = 31 if evaluate([w], now) else 33 if lead > 0 else 32
            text = f"\033[{tone}m{text}\033[0m"
        parts.append(text)
    if not windows:
        parts.append("quota unavailable")
    if bucket == 'claude':
        helper = claude_usage.helper()
        limits = claude_usage.as_limits({'windows':windows})
        pause = (helper.stale_pause(limits, now, data.get('model')) if data.get('error')
                 else helper.evaluate(limits, now, data.get('model'), persist=False))
    else:
        unavailable = (bool(data.get('error'))
                       or (data.get('buckets', {}).get(bucket) or {}).get('complete') is False
                       or ('observed_at' in data and not 0 <= now - data['observed_at'] <= MAX_AGE_SEC)
                       or any(w['reset'] <= now for w in windows))
        pause = weekly_pause(state_dir() if directory is None else directory,
                             bucket, windows, now, unavailable=unavailable, persist=False)
    if pause:
        parts.append(f"⏸ {pause[2]}; resume {fmt_time(pause[0])}")
    if override:
        parts.append("OVERRIDE: gate disabled")
    if data.get("error"):
        age = max(0, now - data.get("observed_at", now))
        parts.append(f"STALE ({age / 60:.0f}m): {data['error']}")
    return " | ".join(parts)


def log(directory, message):
    with (directory / "pace.log").open("a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} [{os.getpid()}] {message}\n")


def gate(directory, payload, get_snapshot=snapshot, sleep=time.sleep,
         wall=time.time, monotonic=time.monotonic):
    bucket = bucket_for(payload.get("model", ""))
    event = payload.get("hook_event_name", "PreToolUse")
    started, held = monotonic(), None
    active_hold = {}
    while True:
        # Recheck during holds so an override releases already-waiting hooks.
        if (directory / "override").exists():
            if held:
                log(directory, "release: override enabled")
            return
        try:
            data = get_snapshot(directory)
            windows = (data["buckets"].get(bucket) or {}).get("windows", [])
            age = wall() - data.get('observed_at', 0)
            unavailable = (data.get('error') or (data['buckets'].get(bucket) or {}).get('complete') is False
                           or not windows or not 0 <= age <= MAX_AGE_SEC
                           or any(w['reset'] <= wall() for w in windows))
            pause = weekly_pause(directory, bucket, windows, wall(), unavailable=bool(unavailable), active_hold=active_hold)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            cached = read_cache(directory)
            windows = (cached.get('buckets', {}).get(bucket) or {}).get('windows', [])
            pause = weekly_pause(directory, bucket, windows, wall(), unavailable=True, active_hold=active_hold)
        if not pause:
            if held:
                weekly = [{**w, "leadHours": (w["used"] - ideal(w, wall())) / 100 * w["minutes"] / 60}
                          for w in windows if w["minutes"] == 10080]
                log(directory, "release: back within pacing rules; weekly=" + json.dumps(weekly, sort_keys=True))
            return
        if held is None or pause[1:] != held[1:]:
            log(directory, f"hold {bucket} {pause[1]}: {pause[2]}; resume {fmt_time(pause[0])}")
            held = pause
        if monotonic() - started >= MAX_HOLD_SEC:
            reason = "Codex pacing hold exceeded seven days. Retry when usage is within pace."
            if event == "PreToolUse":
                print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                      "permissionDecision": "deny", "permissionDecisionReason": reason}}))
            else:
                print(json.dumps({"continue": False, "stopReason": reason}))
            return
        sleep(min(POLL_SEC, max(0.01, pause[0] - wall())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["query", "status", "watch", "gate", "override", "hooks"])
    parser.add_argument("value", nargs="?", choices=["on", "off"])
    parser.add_argument("--bucket", default="codex")
    parser.add_argument("--state-dir", type=Path, default=state_dir())
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--claude-model", help="Claude model to meter; defaults to the most recently active Claude session")
    args = parser.parse_args()
    if args.command == "query":
        # Codex probe skips its cache; the Claude snapshot below can write its cache.
        buckets = normalize(rpc())
        buckets['claude'] = claude_usage.snapshot()
        print(json.dumps(buckets, indent=2))
    elif args.command == "hooks":
        print(json.dumps(rpc("hooks/list", {"cwds": [os.getcwd()]}), indent=2))
    elif args.command == "gate":
        gate(args.state_dir, json.load(sys.stdin))
    elif args.command == "override":
        if args.value is None:
            parser.error("override requires on or off")
        args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = args.state_dir / "override"
        if args.value == "on":
            path.touch(mode=0o600)
        else:
            path.unlink(missing_ok=True)
        print(f"Pacing override {args.value}")
    else:
        while True:
            data = snapshot(args.state_dir, refresh_sec=METER_REFRESH_SEC)
            model = args.claude_model or claude_usage.display_model()
            claude = claude_usage.for_model(claude_usage.snapshot(refresh_sec=METER_REFRESH_SEC), model)
            data['buckets']['claude'] = claude
            if args.bucket == 'claude':
                data.update(error=claude.get('error'), observed_at=claude.get('observed_at', 0), model=model or 'model unknown; all reported allowances')
            output = json.dumps(data, indent=2) if args.json else render(
                data, args.bucket, color=sys.stdout.isatty(),
                override=(args.state_dir / "override").exists(), directory=args.state_dir)
            if not args.json and args.bucket != 'claude':
                output += '\n' + render({'buckets': {'claude': claude},
                                         'error': claude.get('error'),
                                         'observed_at': claude.get('observed_at', 0), 'model': model or 'model unknown; all reported allowances'},
                                        'claude', color=sys.stdout.isatty())
            if args.command == "watch" and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(output, flush=True)
            if args.command != "watch":
                break
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"Codex pacing: {exc}", file=sys.stderr)
        sys.exit(2 if "gate" in sys.argv else 1)
