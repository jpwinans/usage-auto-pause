#!/usr/bin/env python3
"""Usage pacing for Claude Code: statusline renderer + PreToolUse gate.

  pace.py statusline   statusLine command. Reads the shared account quota
                       snapshot, renders context battery + pacing meters.
  pace.py gate         PreToolUse hook. Sleeps while a pause is active, then
                       exits 0 so the tool call proceeds. Each agent reaching a
                       configured PreToolUse hook checks the shared state.
                       Already-running work and inference are not frozen.
                       The local sleep does not make model requests.
  pace.py selftest     Asserts on the pause rules.

Pause rules are a function of the latest limits, the wall clock, and one small
piece of shared state (the weekly-lead hold), so every process agrees:
  session hard   used > 98% -> hold until the window resets
  weekly lead    usage >= 8h ahead of the ideal pace -> hold until the lead has
                 fallen back to <= 4h ahead (hysteresis: releasing at the 8h line
                 made agents run in short bursts against it). Holds are recorded
                 per window in ~/.claude/usage/hold.json so a gate that starts
                 fresh at +6h agrees with one that has been sleeping since +9h,
                 and each is released on the lead read live at that moment, not
                 on a resume time computed when it triggered — other sessions on
                 the account keep spending the same budget. Cleared on release
                 and when the week turns over.
  weekly hard    used > 98% -> hold until the week resets. Takes precedence over
                 the weekly-lead hold, which stays recorded underneath it.
The longest hold wins.

Rules are judged per agent, for the model making the tool call (read from its
transcript). The session and plan-wide weekly windows limit every model; a
per-model bucket (seven_day:Fable, from the account's usage endpoint) limits
only its own model. So with all-models at 50% / -3h and Fable at 75% / +9h, a
Fable agent holds while an Opus agent keeps working.

Escape hatch: touch ~/.claude/usage/override   (rm it to re-arm)
Events log:   ~/.claude/usage/pace.log
"""
import json
from pathlib import Path
import fcntl
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

USAGE_DIR = os.path.expanduser(os.environ.get("CLAUDE_PACING_DIR", "~/.claude/usage"))
LIMITS = os.path.join(USAGE_DIR, "limits.json")
HOLD = os.path.join(USAGE_DIR, "hold.json")
OVERRIDE = os.path.join(USAGE_DIR, "override")
NOTIFIED = os.path.join(USAGE_DIR, "notified")
SESSION_PACE = os.path.join(USAGE_DIR, "session-pace.json")   # {"<session id>": {"lead_hours": 2, "resume_hours": 0}}
LOG = os.path.join(USAGE_DIR, "pace.log")

HARD_PCT = 98                 # strict: exactly 98% proceeds
WEEKLY_LEAD_MIN = 8 * 60      # weekly: hold when usage runs this far ahead of the clock
WEEKLY_LEAD_RESUME = 4 * 60   # weekly: ...and keep holding until it is back to this
SESSION_MIN = 300             # the five_hour window, in minutes
WEEKLY_MIN = 7 * 24 * 60      # every seven_day* window, in minutes
STAMP = os.path.join(USAGE_DIR, "fetched")
USAGE_API = "https://api.anthropic.com/api/oauth/usage"
FETCH_EVERY = 300             # seconds between usage fetches, across all sessions (Claude Code's own cadence)
POLL_SEC = 30
TAIL_BYTES = 1 << 20          # how much of a transcript to scan for the agent's model
MAX_HOLD_SEC = 6 * 24 * 3600  # must stay under the hook timeout in settings.json:
                              # a timed-out hook is fail-open (tool runs), so past
                              # this we deny instead and the agent retries the call


def is_window(key):
    return key == "five_hour" or key.startswith("seven_day")


def label(key):
    """How a window reads in a message: 'weekly', 'weekly Fable', 'weekly opus'."""
    name = key.replace("seven_day", "", 1).lstrip(":_")
    return f"weekly {name}" if name else "weekly"


def applies(key, model):
    """Does this window limit an agent running `model`? Session and plan-wide
    weekly windows limit every model; 'seven_day:Fable' only a Fable model."""
    if ":" not in key:
        return True
    return not model or key.startswith("seven_day:surface:") or key.split(":", 1)[1].lower() in model.lower()


def live_windows(limits, now, model=None):
    """(key, used, resets, lead) for every window limiting `model` that is
    present and not yet reset.

    lead is minutes of usage ahead of a flat burn of the window — the weekly
    pacing signal, and meaningless for the session window.
    """
    out = []
    for key, w in limits.items():
        if not is_window(key) or not isinstance(w, dict) or not applies(key, model):
            continue
        used, resets = w.get("used_percentage"), w.get("resets_at")
        if used is None or not resets or now >= resets:
            continue
        span = SESSION_MIN if key == "five_hour" else WEEKLY_MIN
        out.append((key, used, resets, used / 100 * span - (span - (resets - now) / 60)))
    return out


def session_pace(payload):
    """(lead_min, resume_min, hold_path) for the calling session. A session listed in
    session-pace.json paces by its own weekly lead and resume points and latches in its
    own hold file (keyed by those points), so its stricter hold never holds a session on
    the defaults. Subagents carry their parent's session_id. Anything unreadable or out
    of order (resume not below lead) falls back to the defaults."""
    entry = read_json(SESSION_PACE).get((payload or {}).get("session_id") or "")
    try:
        lead, resume = float(entry["lead_hours"]) * 60, float(entry["resume_hours"]) * 60
    except (TypeError, KeyError, ValueError):
        return WEEKLY_LEAD_MIN, WEEKLY_LEAD_RESUME, HOLD
    if not 0 <= resume < lead:
        return WEEKLY_LEAD_MIN, WEEKLY_LEAD_RESUME, HOLD
    return lead, resume, os.path.join(USAGE_DIR, f"hold-{lead:g}-{resume:g}.json")


def evaluate(limits, now, model=None, hold_path=None, persist=True, unavailable=False,
             lead_min=WEEKLY_LEAD_MIN, resume_min=WEEKLY_LEAD_RESUME):
    """Serialize shared hold updates; persist=False leaves the latch unchanged."""
    hold_path = HOLD if hold_path is None else hold_path
    if not persist:
        return _evaluate(limits, now, model, hold_path, False, unavailable, lead_min, resume_min)
    os.makedirs(os.path.dirname(hold_path), exist_ok=True)
    with open(hold_path + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _evaluate(limits, now, model, hold_path, True, unavailable, lead_min, resume_min)


def _evaluate(limits, now, model, hold_path, persist, unavailable,
              lead_min=WEEKLY_LEAD_MIN, resume_min=WEEKLY_LEAD_RESUME):
    """Active pause for an agent running `model`, as (resume_at, kind, detail),
    or None. Only the windows limiting that model count; the longest hold wins.

    A function of the limits and the clock, save for the weekly-lead hysteresis:
    holds are read from and written to hold_path, one per window, so every
    process shares them — and a gate only ever updates the windows it judged,
    so an Opus agent never releases a Fable agent's hold. A hold's resume_at is
    an estimate for the log and the statusline — the release is judged live,
    from the lead at that moment.
    """
    holds = {k: h for k, h in read_json(hold_path).items()
             if isinstance(h, dict) and is_window(k)}
    before = dict(holds)
    pauses = []
    if unavailable:
        # Stale data never pauses by itself. A latched weekly hold survives a failed
        # refresh only while the LAST KNOWN reading, projected to the clock now, still
        # shows the full +8h trigger lead; a stale token or a dead endpoint must not
        # hold an agent forever on a lead nobody can verify.
        for key, used, resets, lead in live_windows(limits, now, model):
            if used > HARD_PCT:
                pauses.append((resets, 'stale-hard', f'{label(key)} {used}%, until reset'))
            elif (key != "five_hour" and (holds.get(key) or {}).get("resets_at") == resets
                  and lead >= lead_min - 1e-7):
                pauses.append((now + POLL_SEC, 'weekly-lead-unverified',
                               f'{label(key)} last seen {lead / 60:.1f}h ahead; awaiting fresh lead'))
        return max(pauses) if pauses else None
    for key, used, resets, lead in live_windows(limits, now, model):
        if key == "five_hour":
            if used > HARD_PCT:
                pauses.append((resets, "session-hard", f"session {used}%, until reset"))
            continue
        held = (holds.get(key) or {}).get("resets_at") == resets
        if lead >= lead_min - 1e-7 or (held and lead > resume_min + 1e-7):
            holds[key] = {"resets_at": resets, "since": holds[key]["since"] if held else now}
        else:
            holds.pop(key, None)
        if used > HARD_PCT:                             # beats a lead hold, which stays recorded
            pauses.append((resets, "weekly-hard", f"{label(key)} {used}%, until reset"))
        elif key in holds:
            pauses.append((min(resets, now + (lead - resume_min) * 60),
                           "weekly-lead", f"{label(key)} {lead / 60:.1f}h ahead of pace"))
    if persist and holds != before:                     # trigger, release, or new week
        write_json(hold_path, holds)
    return max(pauses) if pauses else None


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def read_limits():
    return read_json(LIMITS)


def write_limits(limits):
    write_json(LIMITS, limits)


def merge_limits(old, new):
    """Several sessions write this file. Keep the newest window and, within a
    window, the highest reading: usage only grows until a reset."""
    out = dict(old)
    for key, n in new.items():
        if not is_window(key) or not isinstance(n, dict):
            continue
        used, resets = n.get("used_percentage"), n.get("resets_at")
        if used is None or not resets:
            continue
        o = out.get(key) or {}
        if resets < o.get("resets_at", 0) or (resets == o.get("resets_at") and used < o.get("used_percentage", 0)):
            continue
        out[key] = {"used_percentage": used, "resets_at": resets}
    return out


def iso_epoch(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    ts = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return round(ts / 60) * 60      # the API jitters resets_at by a second across fetches


def keychain_token(now):
    """Claude Code's OAuth access token from the keychain, or None when missing
    or expired. Never refreshed here: rotating the refresh token out from under
    a live session can log it out. A session signed in via /login keeps it fresh;
    one running on CLAUDE_CODE_OAUTH_TOKEN does not."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True, timeout=3)
        c = json.loads(r.stdout)["claudeAiOauth"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None
    return c.get("accessToken") if c.get("expiresAt", 0) / 1000 > now + 60 else None


def fetch_usage(now, stamp=STAMP):
    """The account's usage response, or {} — at most once per FETCH_EVERY
    across every session. The statusline payload carries only five_hour and
    seven_day, and Claude Code never fetches this itself under an
    inference-only token, so pace.py asks."""
    try:
        if now - os.path.getmtime(stamp) < FETCH_EVERY:
            return {}
    except OSError:
        pass
    os.makedirs(os.path.dirname(stamp), exist_ok=True)
    with open(stamp, "w"):                      # claim the slot before the slow part
        pass
    token = keychain_token(now)
    if not token:
        return {}
    req = urllib.request.Request(USAGE_API, headers={"Authorization": f"Bearer {token}",
                                                     "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)
    except (OSError, ValueError):               # URLError, HTTPError and timeouts are OSErrors
        return {}


def model_windows(usage, now):
    """Per-model weekly windows from a usage response, keyed 'seven_day:<name>':
    the limits[] rows carrying a model scope, whose percent is already 0-100.
    Nothing to read leaves pacing as it was; merge_limits keeps the last
    reading until its window resets, so a failed fetch never lifts a brake."""
    out = {}
    for row in usage.get("limits") or []:
        name = ((row.get("scope") or {}).get("model") or {}).get("display_name")
        resets = iso_epoch(row.get("resets_at"))
        if not name or row.get("percent") is None or not resets or now >= resets:
            continue
        out[f"seven_day:{name}"] = {"used_percentage": row["percent"], "resets_at": resets}
    return out


def fmt_time(epoch):
    dt = datetime.fromtimestamp(epoch)
    return dt.strftime("%H:%M") if dt.date() == datetime.now().date() else dt.strftime("%a %H:%M")


def log(msg):
    try:
        os.makedirs(USAGE_DIR, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{os.getpid()}] {msg}\n")
    except OSError:
        pass


def notify(key, text):
    """macOS notification, once per key across all gate processes."""
    try:
        with open(NOTIFIED) as f:
            if f.read() == key:
                return
    except OSError:
        pass
    try:
        with open(NOTIFIED, "w") as f:
            f.write(key)
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "Claude pacing"'],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def agent_model(payload):
    """The model making the gated tool call: the newest assistant message in the
    calling agent's transcript, which Claude Code writes before PreToolUse runs.
    A subagent's transcript sits under the session's, by agent_id. None when it
    cannot be read — all reported windows are checked conservatively."""
    path = payload.get("transcript_path") or ""
    if payload.get("agent_id") and path.endswith(".jsonl"):
        path = f"{path[:-len('.jsonl')]}/subagents/agent-{payload['agent_id']}.jsonl"
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - TAIL_BYTES))
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if b'"assistant"' not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:                  # the partial first line of the tail
            continue
        model = (r.get("message") or {}).get("model") if r.get("type") == "assistant" else None
        if model and not model.startswith("<"):   # skip <synthetic> error/interrupt entries
            return model
    return None


def stale_pause(limits, now, model=None, hold_path=None,
                lead_min=WEEKLY_LEAD_MIN, resume_min=WEEKLY_LEAD_RESUME):
    # The whole last-known snapshot: the unavailable branch needs each window's lead,
    # not only the rows already over the hard limit.
    known = {key: row for key, row in limits.items()
             if is_window(key) and isinstance(row, dict)
             and isinstance(row.get('used_percentage'), (int, float))
             and 0 <= row['used_percentage'] <= 100}
    return evaluate(known, now, model, hold_path, persist=False, unavailable=True,
                    lead_min=lead_min, resume_min=resume_min)


def shared_usage():
    # One quota source for the terminal, native app, statusline, and gate.
    source_dir = str(Path(__file__).resolve().parents[1] / 'codex')
    if source_dir not in sys.path:
        sys.path.append(source_dir)
    import claude_usage
    try:
        data = claude_usage.snapshot()
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        cached = read_json(str(claude_usage.CACHE_DIR / 'claude-v2.json'))
        return claude_usage.as_limits(cached), 'quota refresh unavailable'
    return claude_usage.as_limits(data), claude_usage.problem(data, time.time())


def record_active_session(payload, model):
    try:
        source_dir = str(Path(__file__).resolve().parents[1] / 'codex')
        if source_dir not in sys.path:
            sys.path.append(source_dir)
        import claude_usage
        claude_usage.record_session(payload, model)
    except (OSError, ValueError, ImportError):
        pass  # Display selection cannot change gate decisions.


def gate():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    event = payload.get('hook_event_name', 'PreToolUse') if isinstance(payload, dict) else 'PreToolUse'
    # PreToolUse reads the calling agent's own model, including subagents.
    # Do not install this as a prompt hook without an authoritative model.
    held, started = None, time.monotonic()
    while True:
        if os.path.exists(OVERRIDE):
            break
        # A waiting hook can outlive a model switch or the transcript write
        # which identifies the current call. Never latch a model for the hold's
        # lifetime, and never borrow another session's display selection.
        model = agent_model(payload) if isinstance(payload, dict) else None
        record_active_session(payload, model)
        lead_min, resume_min, hold_path = session_pace(payload if isinstance(payload, dict) else None)
        try:
            limits, problem = shared_usage()
            pause = (stale_pause(limits, time.time(), model, hold_path, lead_min, resume_min)
                     if problem else evaluate(limits, time.time(), model, hold_path,
                                              lead_min=lead_min, resume_min=resume_min))
        except (OSError, ValueError, RuntimeError, ImportError, TypeError, KeyError):
            pause = stale_pause(read_limits(), time.time(), model, hold_path, lead_min, resume_min)
        if not pause:
            break
        resume_at, kind, detail = pause
        if (kind, detail) != held:
            held = (kind, detail)
            log(f"hold {kind} ({model}): {detail}, resume ~{fmt_time(resume_at)}")
            notify(kind, f"Paused: {detail}. Rechecking usage automatically.")
        if time.monotonic() - started > MAX_HOLD_SEC:
            reason = f"Usage pacing pause still active: {detail}. Retry after quota refresh/reset."
            if event == 'PreToolUse':
                json.dump({'hookSpecificOutput': {'hookEventName': event,
                          'permissionDecision': 'deny', 'permissionDecisionReason': reason}}, sys.stdout)
            else:
                json.dump({'decision': 'block', 'reason': reason}, sys.stdout)
            return
        time.sleep(POLL_SEC)
    if held:
        log("release")
        notify("release", "Pacing pause lifted, agents resuming.")


F, E = "▰", "▱"


def bar(pct, n=10):
    filled = min(n, max(0, int(pct) * n // 100))
    return F * filled + E * (n - filled)


def git_branch(cwd):
    if not cwd:
        return ""
    try:
        r = subprocess.run(["git", "-C", cwd, "branch", "--show-current"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def statusline():
    d = json.load(sys.stdin)
    now = time.time()
    try:
        limits, quota_problem = shared_usage()
    except (OSError, ValueError, RuntimeError, ImportError, TypeError, KeyError):
        limits, quota_problem = read_limits(), 'quota refresh unavailable'
    model_id = (d.get("model") or {}).get("id")      # the statusline paces the session's own model
    record_active_session(d, model_id)
    lead_min, resume_min, hold_path = session_pace(d)   # this session's own pacing points, if listed
    pause = (stale_pause(limits, now, model_id, hold_path, lead_min, resume_min) if quota_problem
             else evaluate(limits, now, model_id, hold_path, lead_min=lead_min, resume_min=resume_min))
    holds = read_json(hold_path)                      # after evaluate: it maintains this

    out = []
    branch = git_branch((d.get("workspace") or {}).get("current_dir") or "")
    if branch:
        out.append(f"\033[33m{branch} \033[0m")

    cw = d.get("context_window") or {}
    u, size = cw.get("current_usage"), cw.get("context_window_size")
    color, b, pct = "90", E * 10, "??"
    used = cw.get("used_percentage")
    if isinstance(used, (int, float)) or (u and size):
        # The window share /context reports. Claude Code sends it; older payloads don't, so
        # fall back to the same arithmetic. (An 88%-of-window threshold overstated usage on a
        # 1M window, where auto-compact keeps only a fixed ~33k buffer.)
        if isinstance(used, (int, float)):
            pct = min(100, int(used))
        else:
            current = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
            pct = min(100, current * 100 // size)
        b = bar(pct)
        color = "31" if pct >= 90 else "33" if pct >= 80 else "34" if pct >= 60 else "32"
    model = (d.get("model") or {}).get("display_name", "unknown")
    out.append(f"\033[90mC\033[0m\033[37m[\033[{color}m{b}\033[37m] {pct}%\033[0m \033[90m({model})\033[0m")

    live = live_windows(limits, now, model_id)
    weekly = [w for w in live if w[0] != "five_hour"]
    meters = [w for w in live if w[0] == "five_hour"]
    if weekly:                                   # one W meter: the bucket that is pacing
        meters.append(max(weekly, key=lambda w: (w[1] > HARD_PCT, w[3])))
    for key, used, resets, lead in meters:
        if key == "five_hour":
            elapsed = SESSION_MIN - (resets - now) / 60
            tag, tail = "S", f"|{int(elapsed / SESSION_MIN * 100)}%"
            color = "31" if used > HARD_PCT else "33" if used > elapsed / SESSION_MIN * 100 else "32"
        else:
            name = label(key)[len("weekly"):].strip()
            tag, tail = f"W:{name}" if name else "W", f"{lead / 60:+.1f}h"
            color = "31" if used > HARD_PCT or key in holds else "33" if lead > 0 else "32"   # red stays on through the hysteresis band
        out.append(f" \033[90m{tag}\033[37m[\033[{color}m{bar(used)}\033[37m]\033[{color}m{int(used)}%\033[90m{tail}\033[0m")

    if quota_problem:
        out.append(f" \033[31mSTALE: {quota_problem}\033[0m")
    if pause:
        resume_at, _, detail = pause
        out.append(f" \033[31m⏸ {detail}, until {fmt_time(resume_at)}\033[0m")
    if os.path.exists(OVERRIDE):
        out.append(" \033[33m⚠ pacing override\033[0m")
    sys.stdout.write("".join(out))


def selftest():
    H, now = 3600, 1_000_000
    hp = os.path.join(tempfile.mkdtemp(), "hold.json")      # never the real state file

    def lim(s_used=None, s_left=None, w_used=None, w_left=None):
        l = {}
        if s_used is not None:
            l["five_hour"] = {"used_percentage": s_used, "resets_at": now + s_left * H}
        if w_used is not None:
            l["seven_day"] = {"used_percentage": w_used, "resets_at": now + w_left * H}
        return l

    def ev(limits, hold=None, model=None):
        """evaluate() for `model` against a scratch hold file seeded with `hold`."""
        write_json(hp, hold or {})
        return evaluate(limits, now, model, hold_path=hp)

    def w_pct(h):
        """weekly used% that puts usage exactly h hours ahead of pace at mid-window."""
        return (WEEKLY_MIN / 2 + h * 60) / (WEEKLY_MIN / 100)

    def plus(limits, name, used):
        """limits with one more weekly bucket, sharing the weekly reset."""
        return {**limits, f"seven_day:{name}": {"used_percentage": used, "resets_at": now + 84 * H}}

    WEEK = now + 84 * H
    HELD = {"seven_day": {"resets_at": WEEK, "since": now - 9 * H}}
    FABLE, OPUS = "claude-fable-5-1", "claude-opus-5"

    assert ev({}) is None
    assert ev(lim(50, 2)) is None                                 # under pace
    assert ev(lim(90, 2)) is None
    assert ev(lim(98, 0.2)) is None                               # strict boundary
    assert ev(lim(98.1, 0.2))[1] == "session-hard"
    assert ev(lim(98.1, 0.2))[0] == now + 0.2 * H                 # resumes at reset
    assert ev(lim(w_used=50, w_left=84)) is None                  # exactly on pace
    assert ev(lim(w_used=54, w_left=84)) is None                  # 6.7h ahead: fine
    assert ev(lim(w_used=98, w_left=1)) is None                 # strict hard boundary, below +8h
    assert ev(lim(w_used=98.1, w_left=84))[1] == "weekly-hard"
    assert ev(lim(98.1, 1, 98.1, 84))[1] == "weekly-hard"         # longest hold wins
    assert ev(lim(50, -1)) is None                                # window already reset

    # weekly-lead hysteresis: trigger at +8h, release only once back to +4h
    assert ev(lim(w_used=w_pct(8.4), w_left=84))[1] == "weekly-lead"
    assert read_json(hp) == {"seven_day": {"resets_at": WEEK, "since": now}}
    assert ev(lim(w_used=w_pct(8.4), w_left=84))[0] == now + 4.4 * H   # estimated catch-up to +4h
    assert ev(lim(w_used=w_pct(6), w_left=84), HELD)[1] == "weekly-lead"   # still held at +6h
    assert ev(lim(w_used=w_pct(6), w_left=84), HELD)[2] == "weekly 6.0h ahead of pace"
    assert read_json(hp)["seven_day"]["since"] == now - 9 * H     # trigger time kept across polls
    assert ev(lim(w_used=w_pct(3.9), w_left=84), HELD) is None    # released at +3.9h
    assert read_json(hp) == {}                                    # and the state cleared
    assert ev(lim(w_used=w_pct(6), w_left=84)) is None            # +6h with no hold: no hold
    assert ev(lim(w_used=98.1, w_left=84), HELD)[1] == "weekly-hard"   # hard beats a lead hold
    assert "seven_day" in read_json(hp)                                # ...which stays recorded
    stale = {"seven_day": {"resets_at": now + 10 * H, "since": now - 20 * H}}
    assert ev(lim(w_used=w_pct(6), w_left=84), stale) is None     # hold belonged to a past week
    assert read_json(hp) == {}
    assert ev(lim(w_used=w_pct(6), w_left=-1), HELD) is None      # expired window: nothing to verify, no pause

    # stale data: pause only on a latched hold whose last known reading is still >= +8h
    def stale(limits, hold=None, model=None):
        write_json(hp, hold or {})
        return evaluate(limits, now, model, hold_path=hp, persist=False, unavailable=True)

    assert stale(lim(w_used=w_pct(9), w_left=84), HELD)[1] == 'weekly-lead-unverified'
    assert stale(lim(w_used=w_pct(6), w_left=84), HELD) is None   # latched, but last check was +6h
    assert stale(lim(w_used=w_pct(9), w_left=84)) is None         # +9h but never latched by a fresh read
    assert stale({}, HELD) is None                                # no reading at all: never hold forever
    assert stale(lim(w_used=98.1, w_left=84))[1] == 'stale-hard'  # the hard limit still holds when stale
    old_slot = {"kind": "weekly-lead", "resets_at": WEEK, "since": now - 9 * H}
    assert ev(lim(w_used=w_pct(6), w_left=84), old_slot) is None  # the old one-slot format drops out

    # per-model buckets limit only their own model
    assert applies("seven_day", OPUS) and applies("five_hour", None)
    assert applies("seven_day:Fable", FABLE) and not applies("seven_day:Fable", OPUS)
    assert applies("seven_day:Fable", None)                   # unknown model: shared windows only
    on_pace = lim(w_used=50, w_left=84)
    fable9 = plus(on_pace, "Fable", w_pct(9))                     # all-models on pace, Fable +9h
    assert ev(fable9, model=FABLE)[2] == "weekly Fable 9.0h ahead of pace"
    assert read_json(hp) == {"seven_day:Fable": {"resets_at": WEEK, "since": now}}
    assert ev(fable9, model=OPUS) is None                         # Opus keeps working
    assert ev(fable9)[1] == "weekly-lead"
    assert ev(plus(lim(w_used=w_pct(9), w_left=84), "Fable", 10), model=FABLE)[2] \
        == "weekly 9.0h ahead of pace"                            # plan-wide still limits Fable
    assert ev(plus(on_pace, "Fable", 99), model=FABLE)[2] == "weekly Fable 99%, until reset"
    assert ev(plus(on_pace, "Fable", 99), model=OPUS) is None
    held_fable = {"seven_day:Fable": {"resets_at": WEEK, "since": now - 9 * H}}
    fable6 = plus(on_pace, "Fable", w_pct(6))
    assert ev(fable6, held_fable, model=FABLE)[1] == "weekly-lead"     # Fable hysteresis
    assert ev(fable6, held_fable, model=OPUS) is None                  # an Opus gate passes...
    assert read_json(hp) == held_fable                                 # ...without releasing it
    assert ev(fable6, model=FABLE) is None                             # and the hold is per window

    # the agent's model comes from the newest assistant entry of its own transcript
    tdir = os.path.dirname(hp)
    main_t = os.path.join(tdir, "sess.jsonl")
    def transcript(path, *models):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write('{"type":"user","message":{"content":"say \\"assistant\\""}}\n')
            for m in models:
                f.write(json.dumps({"type": "assistant", "message": {"model": m}}) + "\n")
            f.write('{"type":"user","message":{"content":[{"type":"tool_result"}]}}\n')
    transcript(main_t, OPUS, FABLE, "<synthetic>")
    assert agent_model({"transcript_path": main_t}) == FABLE      # after /model, past <synthetic>
    sub_t = os.path.join(tdir, "sess", "subagents", "agent-a1.jsonl")
    transcript(sub_t, "claude-sonnet-5")
    assert agent_model({"transcript_path": main_t, "agent_id": "a1"}) == "claude-sonnet-5"
    assert agent_model({"transcript_path": main_t, "agent_id": "gone"}) is None
    assert agent_model({}) is None
    os.remove(main_t); os.remove(sub_t)
    os.rmdir(os.path.dirname(sub_t)); os.rmdir(os.path.dirname(os.path.dirname(sub_t)))
    assert iso_epoch("2026-09-18T16:00:00.377830+00:00") == iso_epoch("2026-09-18T15:59:59.628799+00:00")

    # per-model buckets: the model-scoped limits[] rows of the usage response (shape as served)
    usage = {"limits": [
        {"kind": "weekly_all", "group": "weekly", "percent": 56, "scope": None,
         "resets_at": "2099-01-01T16:00:00.377627+00:00"},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 86,
         "resets_at": "2099-01-01T16:00:00.377830+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 20, "resets_at": "2099-01-01T16:00:00Z",
         "scope": {"model": None, "surface": {"display_name": "Claude Code"}}}]}
    mw = model_windows(usage, now)
    assert list(mw) == ["seven_day:Fable"], mw            # unscoped and surface rows are not model buckets
    assert mw["seven_day:Fable"]["used_percentage"] == 86
    assert model_windows({}, now) == {} and model_windows({"error": {}}, now) == {}

    stamp = os.path.join(os.path.dirname(hp), "fetched")
    with open(stamp, "w"):
        pass
    assert fetch_usage(time.time(), stamp) == {}          # a fetch this window already: no network

    assert merge_limits({}, {"seven_day:Fable": {"used_percentage": 75, "resets_at": 9}, "x": 1}) \
        == {"seven_day:Fable": {"used_percentage": 75, "resets_at": 9}}
    m = merge_limits({"five_hour": {"used_percentage": 40, "resets_at": 5}},
                     {"five_hour": {"used_percentage": 30, "resets_at": 5}})
    assert m["five_hour"]["used_percentage"] == 40                # stale lower reading ignored
    m = merge_limits(m, {"five_hour": {"used_percentage": 1, "resets_at": 9}})
    assert m["five_hour"] == {"used_percentage": 1, "resets_at": 9}   # new window replaces
    os.remove(hp)
    os.remove(hp + '.lock')
    os.remove(stamp)
    os.rmdir(os.path.dirname(hp))
    print("ok")


if __name__ == "__main__":
    try:
        {"statusline": statusline, "gate": gate, "selftest": selftest}[sys.argv[1]]()
    except Exception:
        if len(sys.argv) > 1 and sys.argv[1] == 'gate':
            print('Usage gate unavailable; blocking until quota checks recover.', file=sys.stderr)
            sys.exit(2)
        raise
