# Claude Code setup

Use Python 3.9+ on macOS with Claude Code signed in through `/login`. The shared
reader obtains the existing access token from the `Claude Code-credentials`
Keychain service and sends it only to the Anthropic usage endpoint. It does not
refresh credentials itself. API-key-only and environment-token-only setups are
not supported by this credential reader.

## Register the hook and statusline

1. Keep the entire repository in a stable location.
2. Edit a copy of [settings.example.json](settings.example.json), replacing
   `/ABSOLUTE/PATH/usage-auto-pause` with the checkout's absolute path. Use an
   absolute Python executable if Claude's PATH differs from your shell.
3. Merge its `PreToolUse` entry into `~/.claude/settings.json`, retaining unrelated
   hooks. Replace or combine your existing `statusLine` deliberately: Claude has
   one statusline command. Do not install a duplicate gate alongside an older copy.
4. Reload/restart Claude and inspect `/hooks`. Make an ordinary tool request and
   check the statusline. Test the rules offline first with `python3 scripts/check.py`.

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
