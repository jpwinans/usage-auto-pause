#!/usr/bin/env python3
"""Run offline suites with state isolated from installed pacing hooks."""
import os
import ast
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for source in ROOT.rglob('*.py'):
    if not any(part in {'.venv', 'build', 'dist'} for part in source.parts):
        ast.parse(source.read_text(), filename=str(source))

with tempfile.TemporaryDirectory(prefix='usage-auto-pause-test-') as tmp:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               CLAUDE_PACING_DIR=str(Path(tmp)/'claude'),
               CLAUDE_PACING_CACHE_DIR=str(Path(tmp)/'cache'),
               CODEX_PACING_DIR=str(Path(tmp)/'codex'),
               PACING_TEST_HOOK=str(ROOT/'claude/pace.py'),
               CLAUDE_PACING_HOOK=str(ROOT/'claude/pace.py'))
    for folder in ('codex', 'claude', 'meters'):
        subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', '.', '-v'],
                       cwd=ROOT/folder, env=env, check=True)
    subprocess.run([sys.executable, '-B', str(ROOT/'claude/pace.py'), 'selftest'],
                   env=env, check=True)
