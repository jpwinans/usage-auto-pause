# Paired repository audit — 2026-10-02

Codex and Claude Opus 5.5 independently reviewed baseline `5658d88`, then reconciled
reproductions and the correction design. Claude used the structured native CLI
transport at medium effort; the served model was verified. Reports used isolated
test state. No live quota requests or installed hook changes were made.

## Corrections

- Missing Codex quota windows over 98% are retained within their duration/reset
  bounds. Valid replacements clear them; unrelated buckets remain independent.
  Missing low windows cannot suppress fresh weekly pacing. Future cache timestamps
  no longer prevent refresh.
- Browser/native/terminal readings honor freshness and completeness. Terminal
  hold estimates retain the five-minute display cadence and disclose older samples.
- Malformed Claude state and nonfinite thresholds fall back to defaults; incomplete
  hold metadata cannot accidentally release a fresh hold. Invalid Codex payloads
  deliberately block, while the override remains effective.
- Notification text is passed as data, usage redirects are refused, local HTTP
  hosts are validated, and unused session identifiers are removed from responses.
- Demo period labels, test direct execution, screenshot format/extension, setup
  order, and stale-policy explanations were corrected.

## Preserved policy and unresolved compatibility

Claude intentionally releases in-band weekly holds on stale data; one refresh
failure, HTTP backoff, or **any** quota window's reset can trigger that behavior.
This audit documents and tests it rather than silently replacing the policy.
The normal fresh trigger/release points remain +8h/+4h for both providers.

Provider behavior for null resets, values above 100%, changing model display names,
and disappearing scoped allowances remains unverified. The parser was not loosened
based on hypothetical responses. Model discovery's 1 MiB transcript tail is a
bounded, conservative compatibility assumption. Live account access, multi-day
holds, Linux operation, and full assistive-technology support were not certified.
Browser visual verification and the console screenshot remain blocked by the
previous UI approval review; the native app was visually inspected earlier.

## Verification

The baseline's 64 tests passed on Python 3.9 and 3.11. The corrected suite adds
regressions for partial responses, local-state corruption, stale displays,
redirect/notification boundaries, and HTTP privacy. See [verification](verification.md)
for the final test count and [meter design](meter-design.md) for contrast scope.
All tests use fixtures or isolated state; none prove live provider compatibility.

Setup semantics were checked against the official [Codex hook documentation](https://learn.chatgpt.com/docs/hooks),
[Claude hooks](https://code.claude.com/docs/en/hooks), and
[Claude statusline documentation](https://code.claude.com/docs/en/statusline).
The source manifest and public-content scan are rechecked after corrections.

Final result: **84 tests plus the selftest pass on Python 3.9 and 3.11**.
Claude's focused counterpart review recommended acceptance; its final malformed-cache
finding was fixed with regression coverage. Codex verified the native bundle build.
Privacy scans found no secrets or local-path references in the public tree; bundled
hashes and relative documentation links match. This is evidence from a bounded
audit, not a guarantee of compatibility with future provider/client changes.
