import contextlib
import importlib.util
import io
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pace
import claude_usage

spec = importlib.util.spec_from_file_location('claude_hook', os.environ.get('PACING_TEST_HOOK', str(Path(__file__).resolve().parents[1]/'claude/pace.py')))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
NOW = 1_000_000

class SafetyTests(unittest.TestCase):
    def setUp(self):
        # Fixtures must not consult or clear a real waiting session's latch.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        hold_path = str(Path(tmp.name) / 'hold.json')
        held = patch.object(hook, 'HOLD', hold_path)
        held.start()
        self.addCleanup(held.stop)
        original_helper = claude_usage.helper
        def isolated_helper():
            module = original_helper()
            module.HOLD = hold_path
            return module
        helper = patch.object(claude_usage, 'helper', side_effect=isolated_helper)
        helper.start()
        self.addCleanup(helper.stop)

    def test_fable_is_retained_and_displayed(self):
        usage = {'limits': [
            {'kind':'session','percent':6,'resets_at':NOW+6000},
            {'kind':'weekly_all','percent':57,'resets_at':NOW+172800},
            {'kind':'weekly_scoped','percent':86,'resets_at':NOW+172800,
             'scope':{'model':{'display_name':'Fable'}}}]}
        data = claude_usage.normalize(usage, lambda x:x)
        self.assertEqual(len(data['windows']), 3)
        output = pace.render({'buckets':{'claude':data}}, 'claude', now=NOW)
        self.assertIn('Fable', output)
        self.assertIn('86%', output)

    def test_only_usage_strictly_over_98_percent_holds_until_reset(self):
        for minutes in (300, 10080):
            key = 'five_hour' if minutes==300 else 'seven_day'
            with tempfile.TemporaryDirectory() as tmp:
                for used in (90, 98):
                    w = {'used':used,'minutes':minutes,'reset':NOW+600}
                    self.assertIsNone(pace.evaluate([w],NOW))
                    self.assertIsNone(hook.evaluate({key:{'used_percentage':used,'resets_at':NOW+600}}, NOW, hold_path=str(Path(tmp)/'hold.json')))
                w = {'used':98.1,'minutes':minutes,'reset':NOW+600}
                self.assertEqual(pace.evaluate([w],NOW)[0], NOW+600)
                self.assertEqual(hook.evaluate({key:{'used_percentage':98.1,'resets_at':NOW+600}}, NOW, hold_path=str(Path(tmp)/'hold.json'))[0], NOW+600)

    def test_unknown_model_checks_fable(self):
        self.assertTrue(hook.applies('seven_day:Fable', None))

    def test_missing_expired_stale_and_failed_codex_data_proceeds_below_threshold(self):
        valid={'used':10,'minutes':10080,'reset':NOW+3600}
        cases=[{'buckets':{}},
               {'buckets':{'codex':{'windows':[dict(valid,reset=NOW)]}},'observed_at':NOW},
               {'buckets':{'codex':{'windows':[valid]}},'observed_at':NOW-1000},
               {'buckets':{'codex':{'windows':[valid]}},'observed_at':NOW,'error':'offline'}]
        good={'buckets':{'codex':{'windows':[valid]}},'observed_at':NOW}
        for bad in cases:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                sleeps=[]
                responses=iter([bad,good])
                pace.gate(Path(tmp),{},lambda _:next(responses),sleeps.append,lambda:NOW)
                self.assertFalse(sleeps, 'Staleness alone must not pause work')


    def test_claude_gate_does_not_wait_for_missing_data(self):
        good = {'five_hour':{'used_percentage':5,'resets_at':NOW+3600},
                'seven_day':{'used_percentage':50,'resets_at':NOW+3600}}
        with patch.object(hook, 'shared_usage', side_effect=[({}, 'offline'), (good, None)]), \
             patch.object(hook.sys, 'stdin', io.StringIO('{}')), \
             patch.object(hook.time, 'time', return_value=NOW), \
             patch.object(hook.time, 'sleep') as sleep, \
             patch.object(hook, 'evaluate', return_value=None), \
             patch.object(hook, 'notify'), patch.object(hook, 'log'), \
             patch.object(hook.os.path, 'exists', return_value=False):
            hook.gate()
            sleep.assert_not_called()

    def test_prompt_and_tool_deadlines_block(self):
        for event in ('PreToolUse','UserPromptSubmit'):
            with patch.object(hook, 'shared_usage', return_value=({'seven_day':{'used_percentage':99,'resets_at':hook.time.time()+3600}}, 'offline')), \
                 patch.object(hook.sys, 'stdin', io.StringIO(json.dumps({'hook_event_name':event}))), \
                 patch.object(hook, 'MAX_HOLD_SEC', -1), \
                 patch.object(hook, 'notify'), patch.object(hook, 'log'), \
                 patch.object(hook.os.path, 'exists', return_value=False):
                buf=io.StringIO()
                with contextlib.redirect_stdout(buf): hook.gate()
                decision=json.loads(buf.getvalue())
                if event=='PreToolUse':
                    self.assertEqual(decision['hookSpecificOutput']['permissionDecision'], 'deny')
                else:
                    self.assertEqual(decision['decision'],'block')

    def test_shared_snapshot_preserves_model_and_blocks_after_reset(self):
        data={'schema':2,'complete':True,'observed_at':NOW,'windows':[
            {'key':'seven_day:Fable','used':86,'reset':NOW+1,'minutes':10080,'scope':'Fable'}]}
        with patch.object(claude_usage,'snapshot',return_value=data), patch.object(hook.time,'time',return_value=NOW):
            limits,problem=hook.shared_usage()
        self.assertIsNone(problem)
        self.assertEqual(limits['seven_day:Fable']['used_percentage'],86)
        self.assertIsNotNone(claude_usage.problem(data,NOW+1))


    def test_opus_ignores_fable_exhaustion_but_fable_checks_both(self):
        limits={'five_hour':{'used_percentage':5,'resets_at':NOW+3600},
                'seven_day':{'used_percentage':50,'resets_at':NOW+48*3600},
                'seven_day:Fable':{'used_percentage':99,'resets_at':NOW+48*3600}}
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'hold.json')
            self.assertIsNone(hook.evaluate(limits,NOW,'claude-opus-5',hold_path=path))
            self.assertEqual(hook.evaluate(limits,NOW,'claude-fable-5',hold_path=path)[1],'weekly-hard')
            limits['seven_day']['used_percentage']=99
            limits['seven_day:Fable']['used_percentage']=10
            self.assertEqual(hook.evaluate(limits,NOW,'claude-fable-5',hold_path=path)[1],'weekly-hard')


    def test_terminal_opus_never_shows_fable_as_its_limit(self):
        data={'windows':[
            {'key':'seven_day','used':57,'minutes':10080,'reset':NOW+48*3600,'label':'7D weekly all models'},
            {'key':'seven_day:Fable','used':99,'minutes':10080,'reset':NOW+48*3600,'label':'7D weekly Fable'}]}
        opus=claude_usage.for_model(data,'opus')
        output=pace.render({'buckets':{'claude':opus}},'claude',now=NOW)
        self.assertNotIn('Fable',output)
        self.assertNotIn('⏸',output)
        fable=claude_usage.for_model(data,'fable')
        self.assertIn('Fable',pace.render({'buckets':{'claude':fable}},'claude',now=NOW))


    def test_claude_terminal_resume_matches_claude_gate_without_writing_holds(self):
        data={'windows':[{'key':'five_hour','used':8,'minutes':300,'reset':NOW+3600},
                         {'key':'seven_day:Fable','used':86,'minutes':10080,'reset':NOW+48*3600}]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'hold.json'
            expected=hook.evaluate(claude_usage.as_limits(data),NOW,'fable',hold_path=str(path),persist=False)
            self.assertFalse(path.exists())
            with patch.object(claude_usage,'helper',return_value=hook):
                text=pace.render({'buckets':{'claude':data},'model':'fable'},'claude',now=NOW)
            self.assertIn(pace.fmt_time(expected[0]),text)


    def test_stale_threshold_is_strict_and_expired_high_values_release(self):
        for used,should_pause in [(0,False),(90,False),(98,False),(98.1,True),(100,True)]:
            window={'used':used,'minutes':10080,'reset':NOW+160*3600}
            limits={'seven_day':{'used_percentage':used,'resets_at':window['reset']}}
            self.assertEqual(pace.stale_pause([window],NOW) is not None,should_pause)
            self.assertEqual(hook.stale_pause(limits,NOW,'opus') is not None,should_pause)
            self.assertIsNone(pace.stale_pause([window],window['reset']))
            self.assertIsNone(hook.stale_pause(limits,window['reset'],'opus'))

    def test_stale_fable_does_not_hold_opus(self):
        limits={'seven_day':{'used_percentage':57,'resets_at':NOW+3600},
                'seven_day:Fable':{'used_percentage':99,'resets_at':NOW+3600}}
        self.assertIsNone(hook.stale_pause(limits,NOW,'opus'))
        self.assertIsNotNone(hook.stale_pause(limits,NOW,'fable'))

    def test_stale_codex_high_reading_waits_until_lower_reading(self):
        def snapshot(used):
            return {'error':'offline','buckets':{'codex':{'windows':[
                {'used':used,'minutes':10080,'reset':NOW+3600}]}}}
        with tempfile.TemporaryDirectory() as tmp:
            rows=iter([snapshot(99),snapshot(98)])
            sleeps=[]
            pace.gate(Path(tmp),{},lambda _:next(rows),sleeps.append,lambda:NOW)
            self.assertEqual(len(sleeps),1)

    def test_stale_claude_high_reading_waits_until_lower_reading(self):
        rows=[({'seven_day':{'used_percentage':used,'resets_at':NOW+3600}}, 'offline') for used in (99,98)]
        with patch.object(hook,'shared_usage',side_effect=rows), \
             patch.object(hook.sys,'stdin',io.StringIO('{}')), \
             patch.object(hook.time,'time',return_value=NOW), \
             patch.object(hook.time,'sleep') as sleep, \
             patch.object(hook,'notify'), patch.object(hook,'log'), \
             patch.object(hook.os.path,'exists',return_value=False):
            hook.gate()
            sleep.assert_called_once()

if __name__=='__main__': unittest.main()
