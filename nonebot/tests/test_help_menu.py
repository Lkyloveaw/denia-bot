import os
from html import escape
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

# Load every command-producing plugin, as the running bot does.
from tests import test_help_coverage  # noqa: F401
import gate_logic as gl
import help_image


def menu():
    return gl.grouped_help_rows(gl.all_help_rows())


def options():
    return dict(title='达妮娅 · 指令菜单', subtitle=gl.HELP_PREFIX_GUIDE,
                footer=gl.help_footer(gl.new_state(gl.today())))


def test_complete_menu_keeps_every_command_and_group():
    html = help_image.build_html(menu(), **options())
    assert len(gl.all_help_rows()) >= 70
    assert html.count('<tr>') == len(gl.all_help_rows())
    assert html.count('<section ') == len(menu())
    for command, description in gl.all_help_rows():
        assert escape(gl.display_help_command(command), quote=True) in html
        assert escape(description, quote=True) in html
    assert '群内无需 @' in html and '每 60 秒最多 5 次' in html
    assert gl.HELP_PREFIX_GUIDE in html
    assert '私聊直接打词也行' not in html and '写成 .指令 或 /指令' not in html
    assert '.help' in html and '.敲 / .抱 / .贴 @用户' in html


def test_help_card_and_text_share_prefix_guide():
    from plugins import gate

    with patch('help_image.render_help_images', return_value=[]) as render:
        gate.help_image()
    assert render.call_args.kwargs['subtitle'] == gl.HELP_PREFIX_GUIDE
    assert gl.HELP_PREFIX_GUIDE in gate.help_text()


def test_menu_returns_one_file_and_uses_complete_card_endpoint(tmp_path):
    target = tmp_path / 'help.png'
    with patch.object(help_image, '_output_path', return_value=target), \
            patch.object(help_image, 'render_card', return_value=b'PNG') as render:
        files = help_image.render_help_images(menu(), **options())
    assert files == [target] and target.read_bytes() == b'PNG'
    assert render.call_args.kwargs['width'] > 1600
    assert render.call_args.kwargs['source'] == 'help'


@pytest.mark.skipif(not os.environ.get('QQBOT_RENDER_URL'), reason='needs real renderer')
def test_complete_menu_real_container_returns_one_uncropped_image(tmp_path):
    with patch.object(help_image, '_output_path', return_value=tmp_path / 'help.png'):
        files = help_image.render_help_images(menu(), **options())
    assert len(files) == 1
    with Image.open(BytesIO(files[0].read_bytes())) as image:
        assert image.width == help_image._layout(menu())[1] and image.width > 1600
        assert image.height > 1000
        assert image.width * image.height <= 20_000_000
