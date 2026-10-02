#!/usr/bin/env python3
"""Print the real terminal meter using synthetic usage and temporary hold state."""
from pathlib import Path
import os
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'codex'))


def main():
    with tempfile.TemporaryDirectory(prefix='pacing-meter-demo-') as tmp:
        # Set before importing the helper; no installed latch or cache is read.
        os.environ['CLAUDE_PACING_DIR'] = tmp
        import pace
        now = time.time()
        def window(used, minutes, label, key):
            return dict(used=used, minutes=minutes, reset=now+minutes*30,
                        label=label, key=key)
        fixtures = [
            ('codex', [window(54, 10080, '7D weekly', 'weekly')]),
            ('claude', [window(32, 300, '5H session', 'five_hour'),
                        window(48, 10080, '7D weekly', 'seven_day')]),
        ]
        print('USAGE PACING  /  CONSOLE DEMO')
        print('Synthetic readings - no account access\n')
        for bucket, windows in fixtures:
            data = {'buckets': {bucket: {'windows': windows}},
                    'observed_at': now, 'model': 'opus'}
            print(pace.render(data, bucket, now=now, color=sys.stdout.isatty(), directory=Path(tmp)))
            print()
        print('Positive hours = ahead of pace; negative hours = behind.')
        print('Weekly pause: +8h trigger / +4h resume. Hard cutoff: >98%.')


if __name__ == '__main__':
    main()
