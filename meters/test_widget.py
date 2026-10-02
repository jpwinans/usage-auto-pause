"""Exercise the offline HTTP handler without opening ports or using credentials."""
import io
import json
import unittest
from unittest.mock import patch
import widget


class DemoTests(unittest.TestCase):
    def request(self, path):
        handler = object.__new__(widget.WidgetHandler)
        handler.demo = True
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


if __name__ == '__main__':
    unittest.main()
