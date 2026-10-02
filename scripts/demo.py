#!/usr/bin/env python3
"""Offline replay of the real weekly evaluators; no accounts, network, or sleeps."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'codex'))
import pace
import claude_usage


def main():
    now = 1_000_000
    hook = claude_usage.helper()
    with tempfile.TemporaryDirectory(prefix='usage-auto-pause-demo-') as tmp:
        root = Path(tmp)
        print('Synthetic weekly usage: trigger +8h; fresh release <=+4h.\n')
        print(f'{"Scenario":34} {"Codex":10} Claude')
        for label, lead, stale in [('Below trigger', 7, False),
                                   ('Cross trigger', 8, False),
                                   ('Latched at +6h', 6, False),
                                   ('Refresh fails at +6h', 6, True),
                                   ('Fresh again at +6h', 6, False),
                                   ('Reach release threshold', 4, False)]:
            # Fixed reset halfway through a 168h window; usage controls lead.
            used = 50 + lead/168*100
            reset = now+84*3600
            cw = [{'used':used,'minutes':10080,'reset':reset}]
            aw = {'seven_day':{'used_percentage':used,'resets_at':reset}}
            c = pace.weekly_pause(root, 'codex', cw, now, unavailable=stale)
            a = hook.evaluate(aw, now, hold_path=str(root/'claude.json'),
                              unavailable=stale, persist=not stale)
            print(f'{label:34} {"HOLD" if c else "proceed":10} {"HOLD" if a else "proceed"}')
        print('\nHard cutoff (>98%, independent of lead):')
        for used in (98, 98.1):
            cw = [{'used':used,'minutes':300,'reset':now+60}]
            aw = {'five_hour':{'used_percentage':used,'resets_at':now+60}}
            c = pace.evaluate(cw,now)
            a = hook.evaluate(aw,now,hold_path=str(root/'hard.json'))
            assert bool(c) == bool(a) == (used > 98)
            print(f'  {used}% used: {"HOLD" if c else "proceed"}')
    print('\nAll state was temporary. No live usage or installed hooks were touched.')


if __name__ == '__main__':
    main()
