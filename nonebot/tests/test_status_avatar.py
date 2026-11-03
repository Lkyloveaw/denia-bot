# 移植自 SX0YYYY/astrbot_plugin_statuscard；按 AGPL-3.0 分发。
from io import BytesIO

from PIL import Image

import status_image
import status_logic


def test_snapshot_avatar_reaches_card_html():
    avatar = BytesIO()
    Image.new('RGB', (16, 16), 'red').save(avatar, format='PNG')
    view = status_logic.build_view({'bot': {'nick': '达妮娅', 'avatar': avatar.getvalue()}})
    html = status_image.build_status_html(view)
    assert '<img class="avatar" src="data:image/jpeg;base64,' in html
    assert '<div class="avatar">达</div>' not in html


def test_missing_or_invalid_avatar_uses_initial():
    for avatar in (None, b'not an image'):
        view = status_logic.build_view({'bot': {'nick': '达妮娅', 'avatar': avatar}})
        assert '<div class="avatar">达</div>' in status_image.build_status_html(view)
