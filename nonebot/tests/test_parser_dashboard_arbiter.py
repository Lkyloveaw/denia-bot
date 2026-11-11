import tempfile
from pathlib import Path
from unittest.mock import patch
import parser_settings
from media_parser.core.config import PluginConfig


def test_arbiter_dashboard_setting_is_persisted():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'config.json'
        with patch.object(parser_settings, 'PATH', path):
            current = parser_settings.load()
            parser_settings.save({'version': parser_settings.version(current), 'general': {'arbiter': False}})
            assert PluginConfig(path).arbiter is False
