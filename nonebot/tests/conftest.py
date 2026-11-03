"""Real image tests opt into a running renderer; wiring tests remain offline."""
import os
from pathlib import Path
import pytest


def pytest_collection_modifyitems(items):
    if os.environ.get('QQBOT_RENDER_URL'):
        return
    real_images = {'test_status_image.py', 'test_weather_image.py', 'test_epic_free_image.py'}
    for item in items:
        if Path(str(item.fspath)).name in real_images:
            item.add_marker(pytest.mark.skip(reason='set QQBOT_RENDER_URL for real container image tests'))
