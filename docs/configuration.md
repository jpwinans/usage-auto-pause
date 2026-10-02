# Configuration and local state

The examples preserve the source rule constants rather than introducing a new
configuration framework. Change both implementations if you want a common policy.

| Setting | Claude | Codex |
|---|---|---|
| Hard usage cutoff | `HARD_PCT = 98` (strictly greater) | Same |
| Weekly trigger/release | `WEEKLY_LEAD_MIN = 480`, `WEEKLY_LEAD_RESUME = 240` (minutes) | `WEEKLY_LEAD_TRIGGER_HOURS = 8`, `WEEKLY_LEAD_RESUME_HOURS = 4` |
| Poll sleep | `POLL_SEC = 30` | `POLL_SEC = 5` |
| Shared reader refresh | `codex/claude_usage.py`: 60 seconds | `codex/pace.py`: 60 seconds |
| Gate freshness | 90 seconds | 90 seconds |
| Native/terminal refresh | 300 seconds | 300 seconds |
| Hook internal deadline | 6 days | 7 days + 60 seconds |
| Example hook timeout | 7 days | 8 days |

Claude's older `fetch_usage()` helper retains a 300-second constant. The active
`shared_usage()` path uses `claude_usage.snapshot()` and its 60-second default.
The hard cutoff and the weekly lead rule are independent: 98% may still trigger
weekly pacing if far ahead, even though it does not trigger the hard cutoff.

## Environment and paths

| Variable | Default / purpose |
|---|---|
| `CODEX_HOME` | `~/.codex`; inherited by the Codex CLI and used for default state |
| `CODEX_PACING_DIR` | `${CODEX_HOME}/usage-pacing`; `--state-dir` can override it |
| `CLAUDE_PACING_DIR` | `~/.claude/usage`; hook hold/log/override/session files |
| `CLAUDE_PACING_CACHE_DIR` | `~/.cache/llm-pacing`; shared snapshot and session index |
| `CLAUDE_PACING_HOOK` | Repository's `claude/pace.py`, or bundled helper in the native app; advanced override |
| `PACING_TEST_HOOK` | Test-only hook selection; the test runner selects this repo's hook |

The `CLAUDE_PACING_*` path overrides were added for portability and isolated tests.
Set paths consistently in the environments launching your hooks and meters, or
they will report different state. Relative state paths resolve against the
launching process's working directory; absolute paths are recommended.

Codex state includes `limits.json`, `refresh.lock`, `hold.json`, `hold.lock`,
`pace.log`, and optional `override`. Claude state includes `hold.json`, optional
`hold-<trigger-minutes>-<resume-minutes>.json`, lock files, `pace.log`, `notified`,
optional `override`, and `session-pace.json`. Legacy `limits.json`/`fetched` can
provide fallback data. Shared Claude cache uses `claude-v2.json`/`.lock`, plus
session activity metadata. Tokens are not cached here; local session metadata
and usage can still be private and are not intended for sharing.

## Claude session-specific pacing

Create or merge an entry in `~/.claude/usage/session-pace.json`:

```json
{
  "YOUR_SESSION_ID": {"lead_hours": 2, "resume_hours": 0}
}
```

Get the session ID from that session's hook/statusline payload. Preserve other
entries. Valid thresholds satisfy `0 <= resume < lead`; otherwise defaults are
used. Sessions choosing the same threshold pair share a hold file; different
pairs are isolated. Subagents with the parent session ID inherit its thresholds.
Remove that entry to return to default +8/+4 behavior. Graphical meters keep the
default threshold warnings and do not know this custom policy.

## Credentials and data flow

Codex talks to its local app-server, which uses its existing signed-in account.
Claude reads its OAuth access token from the macOS Keychain and sends it to
`https://api.anthropic.com/api/oauth/usage` with the OAuth beta header. This is an
implementation dependency, not a stable public API contract. Refresh-token
rotation is deliberately left to Claude Code. HTTP backoff protects the endpoint
from repeated retries. No external telemetry or judging service is added.
