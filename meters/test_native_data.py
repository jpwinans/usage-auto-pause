import unittest
from unittest.mock import patch
import native_data


class NativeDataTests(unittest.TestCase):
    def test_expired_or_missing_window_is_unavailable(self):
        self.assertIsNone(native_data.reading({}, 300, 1000))
        self.assertIsNone(native_data.reading({'windows': [
            {'minutes': 300, 'reset': 1000, 'used': 99}]}, 300, 1000))

    def test_session_and_week_use_their_own_duration(self):
        now = 1000000
        for minutes in (300, 10080):
            with self.subTest(minutes=minutes):
                bucket = {'windows': [{'minutes': minutes, 'used': 60,
                                       'reset': now+minutes*30}]}
                reading = native_data.reading(bucket, minutes, now)
                self.assertAlmostEqual(reading['leadHours'], minutes/600)

    def test_claude_uses_shared_refresh_cadence(self):
        with patch.object(native_data.claude_usage, 'snapshot', return_value={}) as fetch:
            result = native_data.fetch_provider('Claude')
        fetch.assert_called_once_with(refresh_sec=300)
        self.assertIsNone(result['windows']['weekly'])
        self.assertIsNone(result['windows']['session'])

    def test_one_provider_failure_is_contained(self):
        with patch.object(native_data.pace, 'snapshot', side_effect=OSError('failed')):
            result = native_data.fetch_provider('Codex')
        self.assertTrue(result['failed'])
        self.assertEqual(result['provider'], 'Codex')
        self.assertEqual(result['windows'], {})


if __name__ == '__main__':
    unittest.main()

class ModelViewTests(unittest.TestCase):
    def test_opus_and_fable_select_different_weekly_quotas(self):
        def row(used,lead,label):
            return {'used':used,'leadHours':lead,'label':label,'stale':False}
        result={'windows':{'session':row(7,-3,'5H session'),
                'weekly':row(57,-24,'7D weekly all models'),
                'seven_day:Fable':row(86,25,'7D weekly Fable')}}
        opus=native_data.model_view(result,'claude-opus-5')
        fable=native_data.model_view(result,'claude-fable-5')
        self.assertEqual(opus['windows']['weekly']['used'],57)
        self.assertNotIn('HOLD',opus['summary'])
        self.assertEqual(fable['windows']['weekly']['used'],86)
        self.assertIn('Fable',fable['summary'])
        result['windows']['weekly']=row(95,30,'7D weekly all models')
        self.assertEqual(native_data.model_view(result,'fable')['windows']['weekly']['used'],95)

    def test_unknown_model_has_no_misleading_weekly_meter(self):
        self.assertIsNone(native_data.model_view({'windows':{}},None)['windows']['weekly'])

    def test_hard_hold_threshold_is_strictly_over_98_percent(self):
        def row(used):
            return {'used':used,'leadHours':0,'label':'5H session','stale':False}
        self.assertNotIn('HOLD', native_data.summary({'session':row(98)}))
        self.assertIn('HOLD', native_data.summary({'session':row(98.1)}))
