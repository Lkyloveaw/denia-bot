import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from media_parser.core.data import Author, ImageContent, ParseResult, Platform
from media_parser.core.parsers.xhs import XHSParser
from media_parser.core.render import Renderer
from media_parser.core.utils import generate_file_name


class XhsMediaTests(unittest.IsolatedAsyncioTestCase):
    def make_parser(self, root, downloader):
        parser = object.__new__(XHSParser)
        parser.cfg = SimpleNamespace(cache_dir=Path(root), single_heavy_render_card=True)
        parser.headers = {}
        parser.downloader = downloader
        return parser

    def make_image(self):
        return SimpleNamespace(urlDefault='https://sns-webpic.example/20261031/abcdef/notes_pre_post/example-image!preview', width=20, height=20)

    async def test_png_original_is_reused_after_suffix_correction(self):
        with TemporaryDirectory() as root:
            async def download(url, **kwargs):
                path = Path(root) / generate_file_name(url, '.jpg')
                Image.new('RGB', (20, 20), 'red').save(path, format='PNG')
                return path
            downloader = SimpleNamespace(download_img=AsyncMock(side_effect=download))
            parser = self.make_parser(root, downloader)
            first = await parser._download_image(self.make_image())
            second = await parser._download_image(self.make_image())
            self.assertEqual(first.suffix, '.png')
            self.assertEqual(second, first)
            downloader.download_img.assert_awaited_once()

    async def test_card_grid_is_ready_while_original_download_is_pending(self):
        with TemporaryDirectory() as root:
            release_original = asyncio.Event()
            preview = Path(root) / 'preview.png'
            original = Path(root) / 'original.png'
            Image.new('RGB', (20, 20), 'red').save(preview)
            Image.new('RGB', (200, 200), 'blue').save(original)
            async def download(url, **kwargs):
                if url.startswith('https://ci.xiaohongshu.com/'):
                    await release_original.wait()
                    return original
                return preview
            parser = self.make_parser(root, SimpleNamespace(download_img=AsyncMock(side_effect=download)))
            contents = await parser._image_contents([self.make_image()])
            renderer = object.__new__(Renderer)
            result = ParseResult(platform=Platform('xhs', '小红书'), contents=contents)
            render = asyncio.create_task(renderer.build_html(result))
            try:
                try:
                    section = await asyncio.wait_for(asyncio.shield(render), timeout=2)
                except TimeoutError:
                    self.fail('Preview rendering waits for the full original download')
                self.assertIn('data:image/jpeg;base64,', section)
                self.assertFalse(contents[0].path_task.done())
                release_original.set()
                self.assertEqual(await contents[0].get_path(), original)
            finally:
                release_original.set()
                await asyncio.gather(render, *(c.get_path() for c in contents), return_exceptions=True)

    async def test_preview_timeout_removes_partial_file_without_waiting_for_original(self):
        with TemporaryDirectory() as root:
            cancelled = asyncio.Event()
            async def download(url, **kwargs):
                path = Path(root) / kwargs['img_name']
                path.write_bytes(b'incomplete')
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            parser = self.make_parser(root, SimpleNamespace(download_img=AsyncMock(side_effect=download)))
            with patch('media_parser.core.parsers.xhs.PREVIEW_TIMEOUT', 0.02):
                self.assertIsNone(await parser._download_preview(self.make_image().urlDefault))
            self.assertTrue(cancelled.is_set())
            self.assertEqual(list(Path(root).iterdir()), [])

    async def test_non_preview_image_keeps_original_rendering(self):
        with TemporaryDirectory() as root:
            path = Path(root) / 'original.png'
            Image.new('RGB', (20, 20), 'blue').save(path)
            content = ImageContent(path)
            self.assertEqual(await content.get_preview_path(), path)

    async def test_preview_is_not_downloaded_when_card_is_disabled(self):
        with TemporaryDirectory() as root:
            original = Path(root) / 'original.png'
            Image.new('RGB', (20, 20), 'blue').save(original)
            downloader = SimpleNamespace(download_img=AsyncMock(return_value=original))
            parser = self.make_parser(root, downloader)
            parser.cfg.single_heavy_render_card = False
            contents = await parser._image_contents([self.make_image()])
            self.assertIsNone(contents[0].preview)
            await contents[0].get_path()
            downloader.download_img.assert_awaited_once()

    async def test_slow_avatar_uses_placeholder_without_cancelling_download(self):
        release_avatar = asyncio.Event()
        async def download_avatar():
            await release_avatar.wait()
            return None
        avatar = asyncio.create_task(download_avatar())
        result = ParseResult(platform=Platform('xhs', '小红书'), author=Author('Author', avatar))
        renderer = object.__new__(Renderer)
        renderer.AVATAR_WAIT_TIMEOUT = 0.02
        render = asyncio.create_task(renderer.build_html(result))
        try:
            try:
                section = await asyncio.wait_for(asyncio.shield(render), timeout=2)
            except TimeoutError:
                self.fail('A slow avatar blocks the entire card')
            self.assertIn('<div class="avatar"></div>', section)
            self.assertIn('Author', section)
            self.assertFalse(avatar.cancelled())
            self.assertFalse(avatar.done())
        finally:
            release_avatar.set()
            await asyncio.gather(avatar, render, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
