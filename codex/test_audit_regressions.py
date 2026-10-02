"""Offline regressions from the paired code and documentation audit."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import pace
import claude_usage

NOW = 1_000_000


def window(used, minutes=300, reset=NOW+600):
    return dict(used=used, minutes=minutes, reset=reset)


def response(windows, bucket='codex'):
    return {'rateLimitsByLimitId': {bucket: {
        slot: dict(usedPercent=w['used'], windowDurationMins=w['minutes'], resetsAt=w['reset'])
        for slot, w in zip(('primary', 'secondary'), windows)}}}


class SnapshotRegressions(unittest.TestCase):
    def refresh(self, old, raw):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pace.atomic_json(directory/'limits.json', dict(buckets=old, observed_at=NOW-61, checked_at=NOW-61))
            with patch.object(pace, 'rpc', return_value=raw), patch.object(pace.time, 'time', return_value=NOW):
                return pace.snapshot(directory)

    def test_missing_or_invalid_hard_slot_survives_partial_refresh(self):
        hard, weekly = window(99), window(10, 10080)
        old = {'codex': {'windows': [hard, weekly], 'complete': True}}
        for raw in (response([weekly]), response([window(float('nan')), weekly])):
            data = self.refresh(old, raw)
            bucket = data['buckets']['codex']
            self.assertIn(hard, bucket['windows'])
            self.assertFalse(bucket['complete'])
            self.assertIsNone(data['error'])
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(pace.weekly_pause(Path(tmp), 'codex', bucket['windows'], NOW,
                                                  unavailable=True)[1], 'session-hard')

    def test_missing_hard_bucket_does_not_make_other_bucket_stale(self):
        old = {'codex': {'windows': [window(99)], 'complete': True}}
        data = self.refresh(old, response([window(10)], 'codex_bengalfox'))
        self.assertFalse(data['buckets']['codex']['complete'])
        self.assertTrue(data['buckets']['codex_bengalfox']['complete'])
        self.assertIsNone(data['error'])

    def test_missing_low_slot_does_not_suppress_fresh_weekly_hold(self):
        weekly = window(56, 10080, NOW+84*3600)
        data = self.refresh({'codex': {'windows': [window(10)]}}, response([weekly]))
        bucket = data['buckets']['codex']
        self.assertTrue(bucket['complete'])
        self.assertEqual(pace.evaluate(bucket['windows'], NOW)[1], 'weekly-lead')
        self.assertEqual(len(bucket['windows']), 1)

    def test_fresh_lower_or_new_reset_replaces_old_hard_window(self):
        old = {'codex': {'windows': [window(99)]}}
        for fresh in (window(10), window(0, reset=NOW+18000)):
            bucket = self.refresh(old, response([fresh]))['buckets']['codex']
            self.assertEqual(bucket['windows'], [fresh])
            self.assertTrue(bucket['complete'])
            self.assertIsNone(pace.evaluate(bucket['windows'], NOW))

    def test_expired_and_implausibly_future_windows_are_not_retained(self):
        for reset in (NOW, NOW+180001):
            old = {'codex': {'windows': [window(99, reset=reset)]}}
            bucket = self.refresh(old, response([window(10, 10080)]))['buckets']['codex']
            self.assertTrue(bucket['complete'])
            self.assertEqual(len(bucket['windows']), 1)

    def test_healthy_refresh_replaces_malformed_prior_cache(self):
        malformed = [[], {'windows': None}, {'windows': [{}]}, {'windows': ['bad']},
                     {'windows': [dict(window(99), used='99')]},
                     {'windows': [dict(window(99), used=float('nan'))]},
                     {'windows': [dict(window(99), minutes=True)]}]
        fresh = window(10, 10080)
        for old in malformed:
            with self.subTest(old=old):
                data = self.refresh({'codex': old}, response([fresh]))
                self.assertEqual(data['buckets']['codex']['windows'], [fresh])
                self.assertTrue(data['buckets']['codex']['complete'])
                self.assertIsNone(data['error'])

    def test_future_checked_at_does_not_suppress_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pace.atomic_json(directory/'limits.json', dict(buckets={}, checked_at=NOW+10000))
            with patch.object(pace, 'rpc', return_value=response([window(10)])) as rpc, \
                 patch.object(pace.time, 'time', return_value=NOW):
                pace.snapshot(directory)
            rpc.assert_called_once()

    def test_invalid_payload_blocks_but_override_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for payload in ([], {'model': None}, {'model': 42}):
                with self.assertRaisesRegex(ValueError, 'Invalid hook payload'):
                    pace.gate(directory, payload)
                (directory/'override').touch()
                pace.gate(directory, payload)
                (directory/'override').unlink()


class ClaudeStateRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hook = claude_usage.helper()
        self.hook.HOLD = str(self.root/'hold.json')
        self.hook.SESSION_PACE = str(self.root/'session-pace.json')

    def test_malformed_configuration_and_nonfinite_thresholds_use_defaults(self):
        for raw in ([], {'test': {'lead_hours': float('inf'), 'resume_hours': 0}}):
            Path(self.hook.SESSION_PACE).write_text(json.dumps(raw))
            self.assertEqual(self.hook.session_pace({'session_id':'test'})[:2], (480,240))

    def test_invalid_activity_files_do_not_break_gate_bookkeeping(self):
        sessions = self.root/'sessions'; sessions.mkdir()
        filename = sessions/(hashlib.sha256(b'test').hexdigest()+'.json')
        for raw in ([1], {'model':'opus','active_at':'bad'}, {'model':'opus','active_at':float('nan')}):
            filename.write_text(json.dumps(raw))
            (self.root/'active-session.json').write_text(json.dumps(raw))
            claude_usage.record_session({'session_id':'test'}, 'opus', self.root)
            self.assertEqual(claude_usage.active_session(self.root)['model'], 'opus')

    def test_missing_since_does_not_release_fresh_hold(self):
        reset = NOW+84*3600
        Path(self.hook.HOLD).write_text(json.dumps({'seven_day':{'resets_at':reset}}))
        result = self.hook.evaluate({'seven_day':{'used_percentage':56,'resets_at':reset}}, NOW)
        self.assertEqual(result[1], 'weekly-lead')
        self.assertEqual(json.loads(Path(self.hook.HOLD).read_text())['seven_day']['since'], NOW)

    def test_notification_text_is_data_not_applescript(self):
        self.hook.NOTIFIED = str(self.root/'notified')
        text = 'X" & (do shell script "id") & "'
        with patch.object(self.hook.subprocess, 'run') as run:
            self.hook.notify('test', text)
        args = run.call_args.args[0]
        self.assertEqual(args[-2:], ['--', text])
        self.assertFalse(any(text in part for part in args[:-1]))

    def test_usage_redirect_is_refused_without_network(self):
        request = urllib.request.Request('https://api.anthropic.com/api/oauth/usage',
                                         headers={'Authorization':'Bearer test-token'})
        handler = claude_usage.NoUsageRedirect()
        self.assertIsNone(handler.redirect_request(request, None, 302, 'Found', {}, 'https://example.invalid'))
        # Exercise the active shared reader's seam with the real helper, no network.
        with patch.object(claude_usage.urllib.request, 'build_opener') as build:
            claude_usage.open_usage(request, timeout=10)
            self.assertIsInstance(build.call_args.args[0], claude_usage.NoUsageRedirect)
            build.return_value.open.assert_called_once_with(request, timeout=10)
        with patch.object(claude_usage, 'open_usage') as shared:
            self.hook.open_usage(request, timeout=3)
            shared.assert_called_once_with(request, 3)

    def test_terminal_freshness_uses_display_budget_and_never_writes_latch(self):
        reset = NOW+84*3600
        path = Path(self.hook.HOLD)
        path.write_text(json.dumps({'seven_day':{'resets_at':reset,'since':NOW-10}}))
        original = path.read_bytes()
        row = dict(window(50+6/168*100, 10080, reset), key='seven_day')
        for age, held in ((200, True), (10000, False)):
            bucket = dict(windows=[row], schema=2, complete=True, observed_at=NOW-age)
            with patch.object(claude_usage, 'helper', return_value=self.hook):
                output = pace.render({'buckets':{'claude':bucket}}, 'claude', now=NOW)
            self.assertEqual('⏸' in output, held)
            self.assertIn('hold estimate' if held else 'STALE', output)
            self.assertEqual(path.read_bytes(), original)

    def test_documented_claude_stale_release_policy_is_preserved(self):
        reset = NOW+84*3600
        Path(self.hook.HOLD).write_text(json.dumps({'seven_day':{'resets_at':reset,'since':NOW-10}}))
        windows = [dict(window(50+6/168*100,10080,reset),key='seven_day'),
                   dict(window(10,reset=NOW-1),key='five_hour')]
        for sample in (dict(schema=2,complete=True,observed_at=NOW,windows=windows),
                       dict(schema=2,complete=True,observed_at=NOW,windows=windows[:1],
                            error='HTTP 429',retry_after=NOW+3600)):
            self.assertIsNotNone(claude_usage.problem(sample,NOW))
            self.assertIsNone(self.hook.stale_pause(claude_usage.as_limits(sample),NOW))


if __name__ == '__main__':
    unittest.main()
