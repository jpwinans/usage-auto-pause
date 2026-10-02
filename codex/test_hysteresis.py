import contextlib
import io
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pace
spec = importlib.util.spec_from_file_location('claude_hysteresis_hook', os.environ.get(
    'PACING_TEST_HOOK', str(Path(__file__).resolve().parents[1] / 'claude/pace.py')))
claude = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude)

NOW = 1_000_000.0
HOUR = 3600


def weekly(lead, reset=NOW + 84 * HOUR):
    return {'used': 50 + lead / 168 * 100, 'minutes': 10080, 'reset': reset}


@contextlib.contextmanager
def claude_state(tmp):
    with patch.object(claude, 'HOLD', str(Path(tmp) / 'hold.json')):
        yield


class HysteresisTests(unittest.TestCase):
    def test_concurrent_bucket_updates_preserve_all_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            def codex_update(index):
                pace.weekly_pause(d, str(index), [weekly(9)], NOW)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(codex_update, range(16)))
            self.assertEqual(len(json.loads((d / 'hold.json').read_text())), 16)
            hp = str(d / 'claude-hold.json')
            def claude_update(index):
                key = 'seven_day:model' + str(index)
                claude.evaluate({key: {'used_percentage': weekly(9)['used'],
                                      'resets_at': weekly(9)['reset']}},
                                NOW, 'model' + str(index), hold_path=hp)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(claude_update, range(16)))
            self.assertEqual(len(json.loads(Path(hp).read_text())), 16)

    def test_codex_real_gate_stale_and_missing_readings_cannot_release(self):
        def snapshot(lead):
            return {'observed_at': NOW, 'buckets': {'codex': {'windows': [weekly(lead)]}}}
        rows = [snapshot(9), snapshot(6), {'buckets': {}},
                dict(snapshot(3), error='offline'), snapshot(4)]
        with tempfile.TemporaryDirectory() as tmp:
            sleeps = []
            it = iter(rows)
            pace.gate(Path(tmp), {}, lambda _: next(it), sleeps.append, lambda: NOW)
            self.assertEqual(len(sleeps), 4)

    def test_claude_missing_window_and_new_week(self):
        with tempfile.TemporaryDirectory() as tmp, claude_state(tmp):
            w = weekly(9)
            self.assertIsNotNone(claude.evaluate({'seven_day': {
                'used_percentage': w['used'], 'resets_at': w['reset']}}, NOW))
            saved = Path(claude.HOLD).read_bytes()
            self.assertIsNone(claude.evaluate({}, NOW))
            self.assertIsNone(claude.stale_pause({}, w['reset'] + 1))
            self.assertEqual(Path(claude.HOLD).read_bytes(), saved)
            self.assertIsNone(claude.evaluate({'seven_day': {
                'used_percentage': 0, 'resets_at': NOW + 168 * HOUR}}, NOW))

    def test_codex_real_gate_waits_through_eight_to_four(self):
        clock = [NOW]
        w = weekly(8.4)
        def snapshot(_):
            return {'observed_at': clock[0], 'buckets': {'codex': {'windows': [w]}}}
        def sleep(seconds):
            clock[0] += seconds
        with tempfile.TemporaryDirectory() as tmp:
            pace.gate(Path(tmp), {}, snapshot, sleep, lambda: clock[0], lambda: clock[0])
        self.assertAlmostEqual((clock[0] - NOW) / HOUR, 4.4, places=5)

    def test_codex_shared_hold_new_call_stale_data_and_bucket_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertIsNotNone(pace.weekly_pause(d, 'codex', [weekly(9)], NOW))
            self.assertIsNotNone(pace.weekly_pause(d, 'codex', [weekly(6)], NOW))
            self.assertIsNone(pace.weekly_pause(d, 'codex_bengalfox', [weekly(6)], NOW))
            self.assertIsNotNone(pace.weekly_pause(d, 'codex', [], NOW, unavailable=True))
            self.assertIsNotNone(pace.weekly_pause(d, 'codex', [weekly(3)], NOW, unavailable=True))
            self.assertIsNotNone(pace.weekly_pause(d, 'codex', [weekly(4.001)], NOW))
            self.assertIsNone(pace.weekly_pause(d, 'codex', [weekly(4)], NOW))
            self.assertIsNone(pace.weekly_pause(d, 'codex', [weekly(6)], NOW))

    def test_codex_no_session_reset_escape_and_live_usage_moves_eta(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rows = [weekly(9), {'used': 10, 'minutes': 300, 'reset': NOW + HOUR}]
            self.assertEqual(pace.weekly_pause(d, 'codex', rows, NOW)[0], NOW + 5 * HOUR)
            self.assertEqual(pace.weekly_pause(d, 'codex', [weekly(10)], NOW)[0], NOW + 6 * HOUR)
            self.assertIsNone(pace.weekly_pause(d, 'codex', [{'used': 0, 'minutes': 10080, 'reset': NOW + 168 * HOUR}], NOW))

    def test_codex_meter_reads_same_hold_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pace.weekly_pause(d, 'codex', [weekly(9)], NOW)
            before = (d / 'hold.json').read_bytes()
            data = {'buckets': {'codex': {'windows': [weekly(6)]}}}
            with patch.object(pace, 'state_dir', return_value=d):
                self.assertIn('⏸', pace.render(data, now=NOW))
            self.assertEqual((d / 'hold.json').read_bytes(), before)

    def test_claude_stale_hold_requires_last_known_trigger_lead(self):
        def limits(lead):
            w = weekly(lead)
            return {'seven_day:Fable': {'used_percentage': w['used'], 'resets_at': w['reset']}}
        with tempfile.TemporaryDirectory() as tmp, claude_state(tmp):
            self.assertIsNotNone(claude.evaluate(limits(9), NOW, 'fable'))
            self.assertIsNotNone(claude.evaluate(limits(6), NOW, 'fable'))
            self.assertIsNone(claude.evaluate(limits(6), NOW, 'opus'))
            saved = Path(claude.HOLD).read_bytes()
            self.assertIsNotNone(claude.stale_pause(limits(9), NOW, 'fable'))
            self.assertIsNotNone(claude.stale_pause(limits(8), NOW, 'fable'))
            self.assertIsNone(claude.stale_pause(limits(6), NOW, 'fable'))
            self.assertIsNone(claude.stale_pause({}, NOW, 'fable'))
            self.assertIsNone(claude.stale_pause(limits(3), NOW, 'fable'))
            self.assertEqual(Path(claude.HOLD).read_bytes(), saved)
            self.assertIsNotNone(claude.evaluate(limits(4.001), NOW, 'fable'))
            self.assertIsNone(claude.evaluate(limits(4), NOW, 'fable'))
            self.assertIsNone(claude.evaluate(limits(6), NOW, 'fable'))

    def test_claude_real_gate_releases_when_stale_lead_drops_below_trigger(self):
        def limits(lead):
            w = weekly(lead)
            return {'seven_day': {'used_percentage': w['used'], 'resets_at': w['reset']}}
        rows = [(limits(9), None), (limits(6), None), (limits(9), 'offline'), (limits(6), 'stale')]
        with tempfile.TemporaryDirectory() as tmp, claude_state(tmp), \
             patch.object(claude, 'shared_usage', side_effect=rows), \
             patch.object(claude.sys, 'stdin', io.StringIO('{}')), \
             patch.object(claude.time, 'time', return_value=NOW), \
             patch.object(claude.time, 'sleep') as sleep, \
             patch.object(claude, 'record_active_session'), patch.object(claude, 'notify'), \
             patch.object(claude, 'log'), patch.object(claude.os.path, 'exists', return_value=False):
            claude.gate()
            self.assertEqual(sleep.call_count, 3)
            self.assertIn('seven_day', json.loads(Path(claude.HOLD).read_text()))


if __name__ == '__main__':
    unittest.main()
