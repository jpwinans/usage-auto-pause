"""Exercise the offline HTTP handler without opening ports or using credentials."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import widget


class DemoTests(unittest.TestCase):
    def request(self, path):
        handler = object.__new__(widget.WidgetHandler)
        handler.demo = True
        handler.headers = {"Host": "127.0.0.1:8765"}
        handler.server = SimpleNamespace(server_address=("127.0.0.1",8765))
        handler.path = path
        handler.wfile = io.BytesIO()
        with patch.object(handler, 'send_response'), patch.object(handler, 'send_header'), \
             patch.object(handler, 'end_headers'), \
             patch.object(widget, 'weekly_reading', side_effect=AssertionError('Live quota called')), \
             patch.object(widget, 'claude_reading', side_effect=AssertionError('Live quota called')):
            handler.do_GET()
        return handler.wfile.getvalue()

    def test_demo_http_routes_never_read_accounts(self):
        self.assertEqual(json.loads(self.request('/api/pace'))['used'], 54)
        self.assertEqual(json.loads(self.request('/api/claude'))['weekly']['used'], 48)
        self.assertIn(b'DEMO - synthetic readings', self.request('/'))


class FreshnessAndPrivacyTests(unittest.TestCase):
    def test_codex_age_skew_and_completeness_are_not_shown_fresh(self):
        now = 1_000_000
        for age, complete, stale in ((0,True,False),(1000,True,True),(-1,True,True),(0,False,True)):
            data = dict(buckets={'codex': dict(complete=complete, windows=[
                dict(used=54, minutes=10080, reset=now+500000)])}, observed_at=now-age)
            with patch.object(widget.pace, 'snapshot', return_value=data), patch.object(widget.time,'time',return_value=now):
                self.assertEqual(widget.weekly_reading()['stale'], stale)

    def test_native_reading_honors_incomplete_bucket(self):
        from native_data import reading
        self.assertTrue(reading(dict(complete=False,observed_at=1000,windows=[
            dict(used=54,minutes=10080,reset=2000)]),10080,1000)['stale'])

    def test_host_check_rejects_missing_or_nonlocal_authorities(self):
        for host, allowed in (('',False),('evil.example:8765',False),('127.0.0.1:80',False),
                              ('127.0.0.1:8765',True),('LOCALHOST:8765',True)):
            handler = object.__new__(widget.WidgetHandler)
            handler.demo = True; handler.path = '/api/pace'; handler.wfile = io.BytesIO()
            handler.headers = {'Host':host}
            handler.server = SimpleNamespace(server_address=('127.0.0.1',8765))
            with patch.object(handler,'send_error') as error, patch.object(handler,'send_response'), \
                 patch.object(handler,'send_header'), patch.object(handler,'end_headers'):
                handler.do_GET()
            self.assertEqual(error.called, not allowed)
            if allowed:
                self.assertEqual(json.loads(handler.wfile.getvalue())['used'],54)
            else:
                error.assert_called_once_with(403,'Local host required')

    def test_claude_http_reading_omits_private_session_key(self):
        with patch.object(widget.claude_usage,'snapshot',return_value={}), \
             patch.object(widget.claude_usage,'active_session',return_value={'model':'opus','session_key':'private'}):
            self.assertNotIn('session_key',widget.claude_reading())

    def test_demo_labels_identify_selected_period(self):
        data = widget.demo_readings()['claude']
        self.assertEqual(data['session']['label'],'5H session')
        self.assertEqual(data['weekly']['label'],'7D weekly')


if __name__ == '__main__':
    unittest.main()
