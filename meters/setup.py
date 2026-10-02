"""Build: .venv/bin/python setup.py py2app"""
from pathlib import Path
import sys
from setuptools import setup
import shutil

shutil.copyfile(Path(__file__).resolve().parents[1] / "claude/pace.py",
                Path(__file__).resolve().parent / "claude_pace.py")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'codex'))
setup(
    name='LLM Pacing',
    version='1.2.2',
    app=['native_app.py'],
    data_files=[('', ['claude_pace.py'])],
    options={'py2app': {
        'argv_emulation': False,
        'iconfile': 'assets/LLMPacing.icns',
        'includes': ['pace', 'claude_usage', 'native_data'],
        'packages': ['AppKit', 'Foundation', 'objc'],
        'plist': {
            'CFBundleName': 'LLM Pacing',
            'CFBundleDisplayName': 'LLM Pacing',
            'CFBundleIdentifier': 'org.example.usage-auto-pause',
            'CFBundleShortVersionString': '1.2.2',
            'NSHighResolutionCapable': True,
            'NSAppSleepDisabled': True,
            'LSMinimumSystemVersion': '12.0',
        },
    }},
)
