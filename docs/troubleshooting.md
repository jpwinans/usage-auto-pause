# Troubleshooting

## A gate is holding and I need to continue

Run the provider's override command from an external terminal; see
[Claude](../claude/README.md#bypass-and-re-arm) or
[Codex](../codex/README.md#inspect-and-control). A tool waiting inside a gate cannot
execute its own escape command. Overrides take effect on the next loop, after
any ongoing refresh/lock wait. Re-arm explicitly when ready. Do not clear the
hold file as a substitute: the rules can relatch it, and Codex's waiting process
also remembers its hold.

## Usage is STALE or unavailable

For Claude, refresh login in Claude Code with `/login`. This reader needs the
macOS Keychain credential; an environment-only token or API key is insufficient.
An expired access token is not renewed by these scripts. A 429 response respects
`Retry-After` (up to an hour), so repeated restarts/forced refreshes need not help.

For Codex, check that `codex` is on the same PATH as the hook and signed in.
`python3 codex/pace.py query` tests the quota RPC without a model request (and
also refreshes the shared Claude cache). An unsupported protocol or missing
bucket should be diagnosed as unavailable, not interpreted as zero usage.

Missing data does not universally mean “pause”: without an established latch,
the gates proceed unless last-known applicable usage is >98% before reset.
Codex keeps an existing weekly hold pending fresh confirmation. Claude's stale
policy is less strict; see [research](research.md).

## Hook never runs, or tools run despite it

Check the absolute script/Python paths and that the whole repo is present.
Inspect `/hooks`; Codex skips untrusted or changed definitions until reviewed.
Restart/resume the client after configuration changes. Preserve synchronous
execution and long timeouts. Check `pace.log` in the provider's state directory;
ordinary calls that proceed need not produce a log entry. Errors/timeouts are
not a reliable policy enforcement mechanism. These hooks cover supported
lifecycle boundaries, not every inference call or previously launched job.

## Display disagrees with another display

Compare the model, quota bucket, duration, refresh age, state directory, override,
and any custom Claude session thresholds. Native/browser warnings do not inspect
latched holds. Native Claude is an explicit allowance selector; the browser and
terminal follow active session activity. A Claude subagent does not replace the
main session's display selection. Context usage (`C`) is unrelated to the
subscription allowance. A weekly-only Codex response has no session quota to show.

At +6h a saved +8h hold may still be active; it releases at +4h with fresh data.
An ETA is only an estimate: usage from other sessions changes it. Codex can stay
held past a reset when no fresh reading arrives. Claude's reset and stale
semantics differ, as the offline demo shows.

## Native app cannot find Codex or shows old behavior

The app needs a discoverable Codex CLI even though its Python readers are bundled.
Finder's PATH differs from your interactive shell. Rebuild after changing Python
sources; restarting an old app bundle does not import new checkout code. Running
`python native_app.py` from the configured virtual environment is useful before
packaging. For browser changes, restart `widget.py` and reload the page.

## Safe local verification

Run `python3 scripts/check.py` and `python3 scripts/demo.py`. They use mocked quota
responses, temporary state and simulated time. They do not prove live account
compatibility or a multi-day real wait. Use a disposable session for any live
hook experiment and retain an external terminal for the override command.
