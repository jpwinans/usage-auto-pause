# Codex setup

Use Python 3.9+ on macOS/Linux and a signed-in Codex CLI providing
`codex app-server --stdio`, `account/rateLimits/read`, and lifecycle hooks.
The reader uses the CLI's existing login without opening or printing auth tokens.
It initializes an app-server process, reads limits, then closes that process;
no model turn is started. See [the official RPC documentation](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).

## Register the gates

1. Keep the full repository in a stable location.
2. Replace `/ABSOLUTE/PATH/usage-auto-pause` in
   [hooks.example.json](hooks.example.json) with the absolute checkout path.
   Use an absolute Python executable if needed.
3. Merge the two entries into `${CODEX_HOME:-~/.codex}/hooks.json`, preserving
   unrelated hooks. Replace older pacing entries instead of adding duplicates.
4. Enable hooks in the existing `[features]` table of `config.toml` if needed:
   `hooks = true`. Do not create a second `[features]` table.
5. Restart/resume Codex, review the definitions in `/hooks`, and trust them.
   New or modified non-managed hook definitions need review before execution.
   See [official hook registration and trust behavior](https://learn.chatgpt.com/docs/hooks).

No Rust patch or precomputed trust hashes are needed. The source installation's
`install.patch` edited user configuration; it was not a Codex binary patch.

Hooks must remain synchronous. Their timeout is eight days (`691200` seconds).
The script denies/stops the pending operation after seven days plus one minute,
before that timeout; this exceptional denial requires a later retry.

## Inspect and control

From the repository root:

```sh
python3 codex/pace.py hooks
python3 codex/pace.py query
python3 codex/pace.py status
python3 codex/pace.py watch
python3 codex/pace.py status --json
python3 codex/pace.py status --bucket codex_bengalfox
python3 codex/pace.py status --bucket base_model_inference
```

`hooks` queries discovery/trust status. `query` fetches Codex limits live without
writing the Codex cache; it also refreshes the **Claude shared cache**. It is not
an entirely write-free probe. `status` and `watch` also read both providers. Missing
Claude credentials should report unavailable Claude data while Codex remains usable.

The gate maps model names containing `spark` to `codex_bengalfox`;
other models use `codex`. The separate `base_model_inference` reserve can be
inspected but is not substituted for the ordinary quota. These are implementation
mappings, not a promise that future provider plans use the same bucket names.

From an external terminal, bypass or re-arm:

```sh
python3 codex/pace.py override on
python3 codex/pace.py override off
```

Waiting hooks normally notice within five seconds, plus an in-progress quota
refresh. The override does not clear saved holds. For isolated state, append
`--state-dir /your/state/directory` consistently to gate and control commands.

Codex's built-in footer can show raw quotas and context. The custom weekly lead
and batteries run in `watch` in another terminal; Claude-style arbitrary
statusline commands are not used here.

## Uninstall

Bypass waiting hooks first, remove only these two pacing hook entries, then
restart/resume affected sessions. Keep other hooks and footer preferences.
Remove the checkout only after no installed command points at it. State lives
under `${CODEX_HOME:-~/.codex}/usage-pacing` unless overridden; preserve it if
another pacing installation uses it.
