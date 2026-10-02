# Verification and packaging

Prepared on 2026-10-02. [source-manifest.json](source-manifest.json) records
SHA-256 hashes of the bundled implementation files after portability and privacy
edits. It contains no original workstation paths.

## Offline checks

```sh
python3 scripts/check.py
python3 scripts/demo.py
python3 meters/widget.py --demo --once
```

The check runner parses Python syntax and runs the three unittest suites in
separate processes, with temporary Claude/Codex/cache state. Provider calls and
clock waits in those tests use fixtures or mocks. It also runs Claude's built-in
rule selftest. On Python 3.11, **64 tests pass** (49 shared/Codex, 4 Claude model
switch, 11 meter/demo), plus the selftest.

Covered behavior includes strict >98%, +8/+4 hysteresis, bucket/model separation,
concurrent hold writers, separate-process latch persistence, reset changes,
stale/missing quotas, Retry-After, model changes while waiting, override during
a hold, and deadline denials. The demo HTTP handler tests assert that synthetic
routes do not call live quota readers and label their HTML as a demo.

The offline replay demonstrates both engines at +7, +8, +6, stale +6, fresh +6,
and +4 hours, followed by 98% and 98.1% hard-cutoff cases. The browser demo JSON
was checked, and a real localhost Codex demo endpoint returned the expected
synthetic response. Browser visual verification remains blocked by UI approval review; the native demo was manually inspected across all nine states, both selectors, and minimum/default/wide sizes. See [meter design](meter-design.md).
The native app bundle also built successfully with the existing Python 3.11 /
py2app environment after a sandboxed attempt aborted. Its bundled Claude helper
was verified present and importable using the frozen-app path resolver. The build
reported missing platform-conditional modules (Windows/JVM); no live account refresh was exercised. Build output was kept outside the repo.

No real account quotas, installed hooks, billing settings, or multi-day waits
were exercised by this verification.

## Changes from the installed sources

- Replaced developer-specific import paths with sibling repository paths.
- Shared Claude reader resolves the repo's hook (or the bundled helper) rather
  than requiring an unrelated installed `~/.claude/hooks/pace.py`.
- Added `CLAUDE_PACING_DIR`, `CLAUDE_PACING_CACHE_DIR`, and `CLAUDE_PACING_HOOK`
  overrides for isolation and portability; existing default state paths remain.
- Native setup bundles a generated copy of the Claude helper in app resources.
- Added synthetic CLI/browser demos and a state-isolated test runner.
- Updated three obsolete Claude test expectations to match the current installed
  stale-data policy; fixed test isolation to patch HOLD instead of wrapping an
  evolving function signature. See [research](research.md#regression-test-adaptation).
- Supplied redacted configuration templates instead of user settings, trust
  hashes, caches, credentials, or logs.

The quota math, trigger/release thresholds, provider failure policies, polling,
and live credential mechanisms are preserved. This repository does not install
or replace the user's currently running hooks automatically.
