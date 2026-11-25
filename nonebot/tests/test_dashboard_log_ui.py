"""Run the dashboard log behavior tests when Node.js is available."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_dashboard_log_ui():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the dashboard log behavior tests')

    script = Path(__file__).with_suffix('.cjs')
    result = subprocess.run(
        [node, '--test', str(script)], capture_output=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
