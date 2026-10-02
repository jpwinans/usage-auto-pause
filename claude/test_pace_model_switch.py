"""Isolated deployed-hook regressions: no quota calls or live state access."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('claude_pace_hook', str(Path(__file__).with_name('pace.py')))
pace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pace)
NOW = 1_000_000
FABLE, OPUS = 'claude-fable-5-1', 'claude-opus-5'


class ModelSwitchTests(unittest.TestCase):
    def test_sleeping_gate_rechecks_own_transcript_model(self):
        self.run_switch(None)

    def test_stale_quota_does_not_apply_unrelated_model_hold(self):
        self.run_switch('quota refresh unavailable')

    def run_switch(self, problem):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / 'session.jsonl'
            def append(model):
                with transcript.open('a') as stream:
                    stream.write(json.dumps({'type':'assistant', 'message':{'model':model}}) + '\n')
            append(FABLE)
            limits = {'seven_day': {'used_percentage':69, 'resets_at':NOW+3600},
                      'seven_day:Fable': {'used_percentage':100, 'resets_at':NOW+3600}}
            sleeps = []
            def sleep(seconds):
                sleeps.append(seconds)
                if len(sleeps) > 1:
                    self.fail('Gate still applies Fable hold after its transcript switched to Opus')
                append(OPUS)
            with patch.object(pace, 'HOLD', str(Path(tmp)/'hold.json')), \
                 patch.object(pace, 'OVERRIDE', str(Path(tmp)/'override')), \
                 patch.object(pace, 'shared_usage', return_value=(limits, problem)), \
                 patch.object(pace, 'record_active_session'), patch.object(pace, 'log'), \
                 patch.object(pace, 'notify'), patch.object(pace.time, 'sleep', side_effect=sleep), \
                 patch.object(pace.time, 'time', return_value=NOW), \
                 patch.object(pace.sys, 'stdin', io.StringIO(json.dumps({'transcript_path':str(transcript)}))):
                pace.gate()
            self.assertEqual(len(sleeps), 1)

    def test_model_isolation_and_unknown_model_are_conservative(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp)/'session.jsonl'
            other = Path(tmp)/'other.jsonl'
            child = Path(tmp)/'session'/'subagents'/'agent-child.jsonl'
            child.parent.mkdir(parents=True)
            for path, model in ((main, OPUS), (other, FABLE), (child, FABLE)):
                path.write_text(json.dumps({'type':'assistant', 'message':{'model':model}})+'\n')
            self.assertEqual(pace.agent_model({'transcript_path':str(main)}), OPUS)
            self.assertEqual(pace.agent_model({'transcript_path':str(other)}), FABLE)
            self.assertEqual(pace.agent_model({'transcript_path':str(main), 'agent_id':'child'}), FABLE)
            self.assertIsNone(pace.agent_model({'transcript_path':str(main), 'agent_id':'missing'}))
            hp = str(Path(tmp)/'hold.json')
            limits = {'seven_day:Fable':{'used_percentage':100, 'resets_at':NOW+3600}}
            for model in (FABLE, None):
                self.assertEqual(pace.evaluate(limits, NOW, model, hold_path=hp, unavailable=True)[1], 'stale-hard')
            self.assertIsNone(pace.evaluate(limits, NOW, OPUS, hold_path=hp, unavailable=True))

    def test_applicable_stale_hold_remains_and_four_hour_release_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            hp = str(Path(tmp)/'hold.json')
            resets = NOW+84*3600
            def limits(lead):
                return {'seven_day':{'used_percentage':50+lead/168*100, 'resets_at':resets}}
            self.assertEqual(pace.evaluate(limits(8), NOW, OPUS, hold_path=hp)[1], 'weekly-lead')
            self.assertEqual(pace.evaluate(limits(6), NOW, OPUS, hold_path=hp)[1], 'weekly-lead')
            # Stale data holds only while the last known reading still shows the +8h trigger lead.
            self.assertEqual(pace.evaluate(limits(9), NOW, OPUS, hold_path=hp, unavailable=True)[1], 'weekly-lead-unverified')
            self.assertIsNone(pace.evaluate(limits(6), NOW, OPUS, hold_path=hp, unavailable=True))
            self.assertIsNone(pace.evaluate({}, NOW, OPUS, hold_path=hp, unavailable=True))
            self.assertIsNone(pace.evaluate(limits(4), NOW, OPUS, hold_path=hp))


if __name__ == '__main__':
    unittest.main()
