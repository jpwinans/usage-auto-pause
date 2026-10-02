# Research notes: meters and usage gates

Researched 2026-10-02 against the installed pacing scripts, their integration settings, and official hook documentation. The code linked below is the portable copy in this repository. This describes the implementation, including differences between providers; it is not a promise that provider quota APIs or model mappings remain stable.

## Source map

| Original component | Repository copy | Role |
| --- | --- | --- |
| Claude pacing hook | [claude/pace.py](../claude/pace.py) | Statusline, model identification, weekly latch, synchronous tool gate |
| Codex quota reader and gate | [codex/pace.py](../codex/pace.py) | Codex RPC reader, terminal meter, prompt/tool gates |
| Shared Claude quota reader | [codex/claude_usage.py](../codex/claude_usage.py) | Shared Claude quota cache and active display model |
| Display implementations | [meters/widget.py](../meters/widget.py), [meters/native_data.py](../meters/native_data.py), [meters/native_app.py](../meters/native_app.py) | Browser server, display adapter, macOS app |

Only pacing-related configuration was inspected. Credentials, account identifiers, transcripts, runtime quota readings, and logs are not research artifacts in this repository. The original installation patches were configuration edits, not patches to Codex itself. In particular, their literal trust hashes belonged to the original exact hook definitions and are unsuitable for distribution.

## Quota math and thresholds

For a window ending at `reset`, duration `D` hours, and percentage `used`:

```text
elapsed_hours = D - (reset - now) / 3600
ideal_percent = 100 * elapsed_hours / D
lead_hours   = used / 100 * D - elapsed_hours
```

The Codex/display `ideal()` function clamps ideal percent to 0–100. Claude's `live_windows()` uses the equivalent un-clamped elapsed expression for live windows. Both ignore windows whose reset has passed for ordinary evaluation. Weekly means 10,080 minutes; the five-hour session means 300 minutes. Codex identifies durations from metadata rather than assuming `primary` is the session window. [Sources: `ideal`, `normalize`, `evaluate` in codex/pace.py; `live_windows` in claude/pace.py.](../codex/pace.py)

Weekly lead **at least +8 hours** starts a pause. Once recorded, a fresh reading in the same applicable hold remains paused until **at most +4 hours**. At 50% elapsed, approximately 54.76% used triggers +8 hours; approximately 52.38% used is +4 hours. A fresh, previously unlatched +6 hours does not pause. The separation between start and resume thresholds is hysteresis: it prevents repeated tiny bursts at the trigger line. [Sources: `evaluate` and `weekly_pause`](../codex/pace.py), [`_evaluate`](../claude/pace.py).

Any applicable window **strictly over 98%** causes a hard hold until reset; exactly 98% alone does not. There is no default five-hour pace hold, only its hard cutoff. Where multiple causes apply, the latest resume time wins. Resume times are estimates: other clients can keep spending, so gates repeatedly evaluate readings rather than sleeping once until the first ETA. [Sources: both providers' `evaluate`/`gate` functions.](../codex/pace.py)

## The provider policies are intentionally different

| Situation | Claude | Codex |
| --- | --- | --- |
| Latch identity | Individual applicable weekly key plus reset timestamp | Quota bucket; reset timestamp is metadata |
| Fresh +6h after a +8h trigger | Held if same window/reset | Held, including corrected reset metadata |
| Stale reading with established hold | Continue only if last-known same-reset weekly lead projected to now is still at least +8h | Keep holding pending a fresh weekly reading at or below +4h |
| Missing data with established hold | Call can proceed; persisted latch is not cleared by stale evaluation | Call stays held |
| Fresh changed-reset window at +6h | Old reset does not latch the new window | Existing bucket hold remains latched |
| Stale data without latch | Only known >98% usage can pause until reset | Only known >98% usage can pause until reset |
| In-process protection | Uses shared hold file on each evaluation | Also remembers `active_hold`, protecting a sleeping gate from another writer clearing the file |
| Gate polling | 30 seconds | 5 seconds |
| Internal wait deadline | 6 days | 7 days plus 60 seconds |
| Inspected hook timeout | 7 days | 8 days |

Both serialize persisted hold changes using file locks. Claude retains per-window state belonging to other models; Codex keeps bucket state independently. A stale Claude evaluation does not erase the saved hold even when it lets a call proceed, so a later fresh same-reset +6h reading can hold again. Codex's stale weekly latch can survive its old ETA and reset, until fresh evidence or override releases it. In practice, a single refresh error or HTTP backoff can release a Claude hold
in the +4h..+8h band. So can any quota window passing its reset, even if that
window belongs to another model: freshness currently applies to the whole
snapshot. These differences are visible in the implementation and must not be hidden behind a single “both require fresh +4h” explanation. [Sources: Claude `_evaluate`, `stale_pause`, `gate`](../claude/pace.py); [Codex `weekly_pause`, `stale_pause`, `gate`](../codex/pace.py).

Claude supports custom session thresholds via `session-pace.json`, keyed by `session_id`, with `lead_hours` and `resume_hours`. Valid settings require `0 <= resume < lead`; invalid entries fall back to +8/+4. The hold filename is keyed by the threshold pair, so sessions choosing the same pair share that hold state; this is not a unique hold file per session. Subagents using the parent session ID inherit the setting. [Source: `session_pace`.](../claude/pace.py)

## Readings, freshness, and model scope

Codex starts a short-lived `codex app-server --stdio`, initializes the protocol, reads `account/rateLimits/read`, and closes it. It does not request inference or directly read credentials. The normalizer keeps only usable finite percentages, durations, resets, bucket names, and completeness. Its gate refresh interval is 60 seconds, freshness limit 90 seconds, and terminal/native meter interval 300 seconds. Refresh errors preserve the prior observation, while `checked_at` throttles repeated failures.
Partial Codex responses retain missing, previously observed >98% windows within
their own duration/reset bounds, marking only the affected bucket incomplete.
A valid fresh replacement supersedes the retained value; low omitted windows
are not retained, so they cannot suppress fresh weekly decisions. [Source: `rpc`, `normalize`, `snapshot`, `gate`](../codex/pace.py); [official rate-limit API](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).

The Codex gate maps model names containing `spark` to `codex_bengalfox`; others use `codex`. It does not replace main quota with a reserve bucket. This is a local mapping to recheck against future runtime metadata, not a universal product guarantee. [Source: `bucket_for`.](../codex/pace.py)

Claude's shared reader calls the usage endpoint using the existing macOS Keychain access token, only if sufficiently unexpired; it never rotates refresh tokens. The endpoint and response shapes are implementation dependencies observed in this installation, not asserted as a stable public API. It normalizes both legacy top-level windows and newer scoped `limits` rows, requires complete global session/weekly readings, and rejects a refresh that loses a previously live scoped allowance. The old local limits file may supply an initial display fallback but is never marked fresh. [Sources: `keychain_token`](../claude/pace.py), [`normalize`, `snapshot`](../codex/claude_usage.py).

Claude's normal gate freshness is also 90 seconds with 60-second refresh throttling. HTTP 429 backs off using `Retry-After`; HTTP 503 does so when that header exists. Both delta-seconds and HTTP dates work, clamped to 60–3,600 seconds with a 300-second fallback. During that interval, even a forced snapshot respects backoff. Displays may tolerate 360 seconds because their intended refresh interval is five minutes. [Sources: `retry_after_sec`, `problem`, `snapshot`](../codex/claude_usage.py), [`DISPLAY_MAX_AGE`](../meters/native_data.py).

Global session/weekly allowances apply to every Claude model; scoped model names match the calling model, while surface limits conservatively apply to all. Unknown model means all reported allowances apply. The gate reads the newest real assistant model from the calling transcript, including a subagent-specific transcript when provided, and repeats identification while waiting. It does not borrow the standalone meter's selected session. This transcript-based identification is a local compatibility dependency. [Sources: `applies`, `agent_model`, `gate`.](../claude/pace.py)

## What the displays mean

Claude's statusline combines context occupancy (`C`), subscription session usage (`S`), and the binding weekly window (`W`, optionally scoped). Context occupancy is separate from account quota. The statusline calls `evaluate()` with persistence enabled: it can establish or clear the shared weekly latch, although it cannot suspend work itself. [Source: `statusline`.](../claude/pace.py)

Codex terminal rendering reads shared hold state without mutating it. The browser and native app are less precise about the gate: `native_data.summary()` checks immediate >98%/+8h thresholds and freshness, not latch files or overrides. At +6h an already-paused gate can remain held while their summary has no `PACE HOLD`. Their labels are quota indicators, not an authoritative audit of whether a specific call will run. The browser's Codex reading checks errors, completeness, and a 90-second age/clock-skew bound; native readings use a 360-second display budget. [Sources: `render`](../codex/pace.py), [`summary`, `reading`](../meters/native_data.py), [`window_reading`](../meters/widget.py).

The standalone Claude model selection follows transcript activity or model changes; periodic redraws do not steal focus and subagents do not replace the main session. Browser `model_view()` selects the binding applicable weekly window. The native app uses `allowance_view()` for explicit allowance panels. [Sources: `record_session`](../codex/claude_usage.py), [`model_view`, `allowance_view`](../meters/native_data.py), [native app](../meters/native_app.py).

## Hooks, blocking, and limits

The installed Claude integration uses synchronous `PreToolUse` plus `statusLine`; there is no pacing `UserPromptSubmit`. A held process sleeps locally and exits successfully when released, allowing the pending tool path to resume. At its own deadline it emits the documented `hookSpecificOutput.permissionDecision: deny` shape. An uncaught gate exception exits 2. This covers calls reaching the hook, not the initial model generation, previously launched jobs, or other account clients. [Source: `gate` and entry point](../claude/pace.py); [official Claude hooks](https://code.claude.com/docs/en/hooks).

Codex installs synchronous `UserPromptSubmit` and `PreToolUse`. Its payload supplies the active `model`; long-wait expiry emits an explicit tool denial or `continue: false` for the prompt event. Codex requires trust of each current non-managed hook definition through `/hooks`; editing a definition requires review again. Local and nested code-mode tools have documented hook coverage, with exceptions. Neither integration is a hook before every inference request. Keep long handler timeouts above the scripts' internal deadlines, and do not set asynchronous execution on a gate. [Source: `gate`](../codex/pace.py); [official Codex hooks](https://learn.chatgpt.com/docs/hooks).

Override files release sleeping gates on a subsequent loop; a slow quota refresh or lock wait can delay that response. Overrides do not delete the recorded hold, so disabling override can restore it. Native/browser displays are informational; they do not pause agents. The buffer and pacing rules cannot guarantee a spending cap or prevent in-flight/concurrent usage. [Sources: both `gate` implementations, Codex `override` command](../codex/pace.py), [Claude override check](../claude/pace.py).

## Regression-test adaptation

The copied `codex/test_hysteresis.py` contained older Claude expectations that missing or stale data must hold until fresh +4h. Its Claude-specific cases now assert the installed policy: stale +8h can preserve a latched wait, stale +6h or missing data can release the call, and the stored latch survives for future fresh evaluation. Test isolation patches the Claude `HOLD` constant rather than wrapping `evaluate`, which receives positional hold-path arguments from `stale_pause`. Codex policy assertions were preserved. [Source: regression tests](../codex/test_hysteresis.py).
