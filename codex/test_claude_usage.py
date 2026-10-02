import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import claude_usage
import pace


class ClaudeUsageTests(unittest.TestCase):
    def test_both_api_shapes_include_model_specific_weekly(self):
        iso = lambda _: 2000000
        legacy = {'five_hour': {'utilization': 3, 'resets_at': 'date'},
                  'seven_day': {'utilization': 56, 'resets_at': 'date'},
                  'seven_day:Fable': {'used_percentage':86,'resets_at':'date'}}
        modern = {'limits': [
            {'kind': 'session', 'percent': 3, 'resets_at': 'date', 'scope': None},
            {'kind': 'weekly_all', 'percent': 56, 'resets_at': 'date', 'scope': None},
            {'kind': 'weekly_scoped', 'percent': 86, 'resets_at': 'date',
             'scope': {'model': {'display_name': 'Fable'}}}]}
        self.assertEqual(claude_usage.normalize(legacy, iso),
                         claude_usage.normalize(modern, iso))
        self.assertEqual([w['used'] for w in claude_usage.normalize(modern, iso)['windows']], [3, 56, 86])

    def test_breakdown_metadata_is_not_a_quota_window(self):
        source={'five_hour':{'utilization':7,'resets_at':2000000},
                'seven_day':{'utilization':57,'resets_at':2000000},
                'seven_day_breakdown':{'other': {}},
                'seven_day_opus':{'utilization':None,'resets_at':None}}
        self.assertTrue(claude_usage.normalize(source, lambda x:x)['complete'])

    def test_missing_and_invalid_windows_are_not_zero(self):
        self.assertEqual(claude_usage.normalize({}, lambda _: 0)['windows'], [])
        invalid = {'five_hour': {'utilization': float('nan'), 'resets_at': 20}}
        self.assertEqual(claude_usage.normalize(invalid, lambda _: 0)['windows'], [])

    def test_login_failure_preserves_hook_cache_and_throttles(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            limits = directory / 'limits.json'
            limits.write_text('{}')
            source = SimpleNamespace(LIMITS=str(limits),
                read_limits=lambda: {'five_hour': {'used_percentage': 3, 'resets_at': 2000000}},
                iso_epoch=lambda _: 2000000, keychain_token=lambda _: None)
            with patch.object(claude_usage, 'helper', return_value=source) as helper:
                first = claude_usage.snapshot(directory)
                second = claude_usage.snapshot(directory)
            self.assertEqual(first, second)
            self.assertEqual(helper.call_count, 1)
            self.assertEqual(first['windows'][0]['used'], 3)
            self.assertIn('login', first['error'])
            self.assertEqual(json.loads(limits.read_text()), {})

    def test_claude_terminal_has_both_windows_and_signed_pace(self):
        now = 1000000
        bucket = {'windows': [{'used': 40, 'minutes': 300, 'reset': now+7200},
                              {'used': 56, 'minutes': 10080, 'reset': now+84*3600}]}
        result = pace.render({'buckets': {'claude': bucket}}, 'claude', now=now)
        self.assertIn('5H session', result)
        self.assertIn('7D weekly', result)
        self.assertIn('-1.0h', result)
        self.assertIn('+10.1h', result)


if __name__ == '__main__':
    unittest.main()

class ActiveSessionTests(unittest.TestCase):
    def test_redraw_does_not_steal_activity_and_model_switch_updates_it(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); first=directory/'one.jsonl'; second=directory/'two.jsonl'
            first.touch();second.touch()
            os.utime(first,(100,100));os.utime(second,(200,200))
            one={'session_id':'one','transcript_path':str(first)}
            two={'session_id':'two','transcript_path':str(second)}
            with patch.object(claude_usage.time,'time',return_value=300):
                claude_usage.record_session(one,'opus',directory)
                claude_usage.record_session(two,'fable',directory)
                claude_usage.record_session(one,'opus',directory)
                self.assertEqual(claude_usage.active_session(directory)['session_key'],'two')
                claude_usage.record_session(one,'sonnet',directory)
                self.assertEqual(claude_usage.active_session(directory)['model'],'sonnet')
                claude_usage.record_session(dict(two,agent_id='child'),'fable',directory)
                self.assertEqual(claude_usage.active_session(directory)['model'],'sonnet')

class SharedSnapshotTests(unittest.TestCase):
    def test_successful_refresh_contains_model_windows_and_shared_cache(self):
        import io,time
        now=time.time()
        raw={'limits':[{'kind':'session','percent':7,'resets_at':now+3600},
                       {'kind':'weekly_all','percent':57,'resets_at':now+86400},
                       {'kind':'weekly_scoped','percent':86,'resets_at':now+86400,
                        'scope':{'model':{'display_name':'Fable'}}}]}
        source=SimpleNamespace(read_limits=lambda:{},iso_epoch=lambda x:x,
                               keychain_token=lambda _: 'test-token',USAGE_API='https://example.invalid/usage')
        with tempfile.TemporaryDirectory() as tmp, patch.object(claude_usage,'helper',return_value=source), \
             patch.object(claude_usage.urllib.request,'urlopen',return_value=io.StringIO(json.dumps(raw))) as fetch:
            first=claude_usage.snapshot(Path(tmp))
            second=claude_usage.snapshot(Path(tmp))
        self.assertEqual(first,second)
        self.assertEqual(fetch.call_count,1)
        self.assertIsNone(claude_usage.problem(first,now+1))
        self.assertEqual(claude_usage.as_limits(first)['seven_day:Fable']['used_percentage'],86)

    def test_rate_limit_backs_off_even_for_forced_refresh(self):
        from urllib.error import HTTPError
        source=SimpleNamespace(read_limits=lambda:{},iso_epoch=lambda x:x,
                               keychain_token=lambda _: 'test-token',USAGE_API='https://example.invalid/usage')
        with tempfile.TemporaryDirectory() as tmp, patch.object(claude_usage,'helper',return_value=source), \
             patch.object(claude_usage.urllib.request,'urlopen',side_effect=HTTPError('https://example.invalid',429,'rate limited',{},None)) as fetch:
            first=claude_usage.snapshot(Path(tmp))
            second=claude_usage.snapshot(Path(tmp),force=True)
        self.assertEqual(first,second)
        self.assertEqual(fetch.call_count,1)
        self.assertGreater(first['retry_after'],first['checked_at']+299)

    def test_rate_limit_honors_retry_after_seconds(self):
        from urllib.error import HTTPError
        source=SimpleNamespace(read_limits=lambda:{},iso_epoch=lambda x:x,
                               keychain_token=lambda _: 'test-token',USAGE_API='https://example.invalid/usage')
        with tempfile.TemporaryDirectory() as tmp, patch.object(claude_usage,'helper',return_value=source), \
             patch.object(claude_usage.urllib.request,'urlopen',side_effect=HTTPError('https://example.invalid',429,'rate limited',{'Retry-After':'120'},None)):
            data=claude_usage.snapshot(Path(tmp))
        self.assertAlmostEqual(data['retry_after']-data['checked_at'],120,delta=2)
        self.assertEqual(claude_usage.problem(data,data['checked_at']),'HTTP 429, retry 2m')


class RetryAfterTests(unittest.TestCase):
    def test_parses_seconds_dates_and_garbage(self):
        import email.utils
        now=1_800_000_000
        self.assertEqual(claude_usage.retry_after_sec('120',now),120)
        self.assertEqual(claude_usage.retry_after_sec(email.utils.formatdate(now+900,usegmt=True),now),900)
        self.assertEqual(claude_usage.retry_after_sec(None,now),300)
        self.assertEqual(claude_usage.retry_after_sec('soon',now),300)
        self.assertEqual(claude_usage.retry_after_sec('1',now),claude_usage.REFRESH_SEC)
        self.assertEqual(claude_usage.retry_after_sec('99999',now),3600)
        self.assertEqual(claude_usage.retry_after_sec('nan',now),300)

    def test_problem_is_terse(self):
        now=1_800_000_000
        ok={'schema':2,'complete':True,'observed_at':now,'windows':[{'reset':now+60}]}
        self.assertIsNone(claude_usage.problem(ok,now))
        self.assertEqual(claude_usage.problem(dict(ok,error='Claude usage refresh unavailable: URLError'),now),'URLError')
        self.assertEqual(claude_usage.problem(dict(ok,error='Claude usage refresh unavailable: Claude login unavailable or expired'),now),'login unavailable or expired')
        self.assertEqual(claude_usage.problem(dict(ok,complete=False),now),'incomplete')
        self.assertEqual(claude_usage.problem(ok,now+300),'5m old')
        self.assertEqual(claude_usage.problem(dict(ok,observed_at=now+5000),now+5050),'reset passed')
