import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import pace
NOW = 1_000_000.
RESET = NOW + 84 * 3600

def weekly(lead, reset=RESET):
    return {'used': pace.ideal({'reset': reset, 'minutes': 10080}, NOW) + lead / 168 * 100,
            'minutes': 10080, 'reset': reset}

def snapshot(windows):
    return {'observed_at': NOW, 'buckets': {'codex': {'windows': windows}}}

class DurableCodexLatch(unittest.TestCase):
    def test_reset_changes_do_not_release_between_eight_and_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for lead, shift in [(8.4, 0), (7.9, 60), (6, -60), (4.001, 120)]:
                self.assertIsNotNone(pace.weekly_pause(d, 'codex', [weekly(lead, RESET+shift)], NOW))
            self.assertIsNone(pace.weekly_pause(d, 'codex', [weekly(4, RESET+180)], NOW))
            self.assertIsNone(pace.weekly_pause(d, 'codex', [weekly(6)], NOW))

    def test_gate_does_not_forget_when_another_writer_removes_shared_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            readings = iter([8.4, 7.9, 6, 4])
            slept = []
            def get(_):
                return snapshot([weekly(next(readings))])
            def sleep(seconds):
                slept.append(seconds)
                (d/'hold.json').write_text('{}')
            pace.gate(d, {}, get, sleep, lambda: NOW, lambda: NOW)
            self.assertEqual(len(slept), 3)

    def test_all_weekly_windows_must_reach_release_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertIsNotNone(pace.weekly_pause(d, 'codex', [weekly(9), weekly(1, RESET+30)], NOW))
            self.assertIsNotNone(pace.weekly_pause(d, 'codex', [weekly(6), weekly(1, RESET+30)], NOW))
            self.assertIsNone(pace.weekly_pause(d, 'codex', [weekly(4), weekly(1, RESET+30)], NOW))

    def test_separate_processes_share_hold_despite_reset_change(self):
        code = """import json,sys; from pathlib import Path; import pace
print(json.dumps(pace.weekly_pause(Path(sys.argv[1]), 'codex', json.loads(sys.argv[2]), float(sys.argv[3]))))
"""
        with tempfile.TemporaryDirectory() as tmp:
            def run(lead, shift):
                p = subprocess.run([sys.executable, '-B', '-c', code, tmp,
                    json.dumps([weekly(lead, RESET+shift)]), str(NOW)],
                    cwd=Path(pace.__file__).parent, capture_output=True, text=True, check=True)
                return json.loads(p.stdout)
            self.assertIsNotNone(run(8.4, 0))
            self.assertIsNotNone(run(7.9, 60))
            self.assertIsNotNone(run(4.001, -60))
            self.assertIsNone(run(4, 90))
            self.assertIsNone(run(6, 90))

if __name__ == '__main__': unittest.main()
