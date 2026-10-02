# Claude Code setup

Use Python 3.9+ on macOS with Claude Code signed in through `/login`. The shared
reader obtains the existing access token from the `Claude Code-credentials`
Keychain service and sends it only to the Anthropic usage endpoint. It does not
refresh credentials itself. API-key-only and environment-token-only setups are
not supported by this credential reader.

## Register the hook and statusline

1. Run `python3 scripts/check.py` offline first, then keep the entire repository
   in a stable location.
2. Edit a copy of [settings.example.json](settings.example.json), replacing
   `/ABSOLUTE/PATH/usage-auto-pause` with the checkout's absolute path. Use an
   absolute Python executable if Claude's PATH differs from your shell.
3. Merge its `PreToolUse` entry into `~/.claude/settings.json`, retaining unrelated
   hooks. Replace or combine your existing `statusLine` deliberately: Claude has
   one statusline command. Do not install a duplicate gate alongside an older copy.
4. Reload/restart Claude and inspect `/hooks`. Make an ordinary tool request and
   check the statusline.

The command stays synchronous. **Do not enable async execution.** Its seven-day
configured timeout exceeds the script's six-day safety deadline. At that deadline
the script denies the pending tool rather than relying on hook timeout behavior.
The denial ends that waiting call; it is not an automatic resumption mechanism.

No `UserPromptSubmit` gate is included. The implementation identifies the actual
model from the latest assistant entry in the calling agent's transcript. This
handles switches and subagents more reliably at tool boundaries. Missing model
identity causes conservative checking of all reported allowances.

## Read the statusline

`C` is context-window usage, not subscription quota. `S` is the shared five-hour
allowance, followed by the percentage of that time window elapsed. `W` is the most
constraining applicable weekly allowance and its signed lead in hours. Model
scoped windows can appear as `W:Fable`. `STALE` means the last refresh cannot be
relied on. The pause time is an estimate, recalculated as usage changes.

The statusline records session/model activity for standalone displays and can
establish or clear the weekly latch. Rendering the statusline never sleeps to
hold a tool; that happens in `gate`.

## Bypass and re-arm

From an external terminal (a gated tool cannot bypass its own waiting hook):

```sh
mkdir -p ~/.claude/usage
touch ~/.claude/usage/override
# Re-arm when ready:
python3 -c 'from pathlib import Path; (Path.home()/".claude/usage/override").unlink(missing_ok=True)'
```

Waiting Claude gates observe the override on their next poll, normally within
30 seconds plus any ongoing refresh. It bypasses the gate without clearing the
weekly latch. Re-arming can therefore immediately hold again. Adapt the path if
using `CLAUDE_PACING_DIR`.

See [configuration](../docs/configuration.md) for session-specific thresholds and
[troubleshooting](../docs/troubleshooting.md) for stale login or model readings.

## Uninstall

Enable the override first if calls are waiting. Remove only this gate's entry
and statusline registration from Claude settings, then restart the affected
sessions. Keep unrelated hooks. Once no process uses the checkout, it can be
removed. Caches and logs may be retained or removed separately; don't delete a
shared state directory while another installed pacing integration still uses it.

## Failure-policy limits

The captured Claude policy releases a latched weekly hold when stale data projects
below +8h, even if it is still above the normal +4h release point. A single refresh
failure, a 429 backoff, or **any** window in the snapshot passing its reset can
cause this, including a window for another model. For example, a latched +6h
reading can proceed during a failed refresh; a stale +9h reading falls below the
trigger after about an hour rather than waiting about five hours to reach +4h.
The latch file remains, so a later fresh +6h reading can hold again. This policy
is intentionally preserved; it is less conservative than the Codex gate.

Model discovery scans the last 1 MiB of the calling transcript. A large trailing
entry can hide its latest assistant model; the conservative fallback checks all
reported allowances. Malformed Claude state objects fall back to defaults, while
Codex deliberately blocks on a corrupt hold file. Malformed Codex hook payloads
also block unless its override is enabled; Claude uses unknown-model checks for
a non-object payload.
