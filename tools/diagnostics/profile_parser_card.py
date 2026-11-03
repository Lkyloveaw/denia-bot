"""Profile a previously received XHS link without sending any QQ messages.

Run on the bot server, from the NoneBot working directory. URLs and credentials
stay in memory; output contains only timing, file sizes and counts.
"""

import argparse
import asyncio
import importlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


async def profile(args):
    sys.path.insert(0, str(Path.cwd()))
    sys.path.append(str(Path.cwd() / 'parser_deps'))
    from nonebot import logger
    logger.remove()
    if args.override:
        for module, filename in (
            ('media_parser.core.data', 'data.py'),
            ('media_parser.core.render', 'render.py'),
            ('media_parser.core.parsers.xhs', 'xhs.py'),
        ):
            importlib.import_module(module.rsplit('.', 1)[0])
            spec = importlib.util.spec_from_file_location(module, Path(args.override) / filename)
            loaded = importlib.util.module_from_spec(spec)
            sys.modules[module] = loaded
            spec.loader.exec_module(loaded)
    from media_parser.core.config import PluginConfig
    from media_parser.core.download import Downloader
    from media_parser.core.parsers.xhs import XHSParser
    from media_parser.core.render import Renderer
    from tqdm import tqdm
    if args.override:
        Renderer.RESOURCES_DIR = Path.cwd() / 'media_parser/core/resources'
        Renderer.LOGOS_DIR = Renderer.RESOURCES_DIR / Renderer._LOGOS
        Renderer.DEFAULT_FONT_PATH = Renderer.RESOURCES_DIR / Renderer._FONT_FILENAME
        Renderer.DEFAULT_VIDEO_BUTTON_PATH = Renderer.RESOURCES_DIR / Renderer._BUTTON_FILENAME

    rows = subprocess.check_output(
        ['journalctl', '-u', 'qqbot', '--since', args.since, '-o', 'json', '--no-pager'], text=True
    ).splitlines()
    url = None
    pattern = re.compile(r'https://www\.xiaohongshu\.com/(?:explore|discovery/item)/' + re.escape(args.note_id) + r'[^\s\x27\x22<>\]]*')
    for row in rows:
        message = json.loads(row).get('MESSAGE', '')
        if isinstance(message, list):
            message = bytes(message).decode('utf-8', 'replace')
        if found := pattern.search(message):
            url = found.group(0)
    if url is None:
        raise RuntimeError('No matching received link')

    with tempfile.TemporaryDirectory(prefix='qqbot-card-profile-') as temp:
        cfg = PluginConfig()
        if args.cold:
            cfg.cache_dir = Path(temp)
        downloader = Downloader(cfg)
        downloader.get_progress_bar = lambda *a, **k: tqdm(disable=True)
        parser = XHSParser(cfg, downloader)
        renderer = Renderer(cfg)
        metrics = {}
        for name in ('_calculate_header_section', '_calculate_image_grid_section', '_draw_sections'):
            original = getattr(renderer, name)
            async def timed(*a, _name=name, _fn=original, **kw):
                started = time.perf_counter()
                try:
                    return await _fn(*a, **kw)
                finally:
                    metrics[_name] = round(time.perf_counter() - started, 3)
            setattr(renderer, name, timed)
        try:
            started = time.perf_counter()
            match = next(p.search(url) for k, p in parser._key_patterns if k == 'xiaohongshu.com')
            result = await parser.parse('xiaohongshu.com', match)
            metrics['page_parse_s'] = round(time.perf_counter() - started, 3)
            card = await renderer.render_card(result)
            metrics['card_ready_s'] = round(time.perf_counter() - started, 3)
            metrics['card_kib'] = round(card.stat().st_size / 1024, 1) if card else None
            metrics['originals_pending_at_card'] = sum(
                isinstance(c.path_task, asyncio.Task) and not c.path_task.done() for c in result.img_contents
            )
            originals = await asyncio.gather(*(c.get_path() for c in result.img_contents))
            metrics['all_originals_ready_s'] = round(time.perf_counter() - started, 3)
            metrics['original_count'] = len(originals)
            metrics['original_mib'] = round(sum(p.stat().st_size for p in originals) / 1024**2, 1)
            if card:
                card.unlink(missing_ok=True)
            print(json.dumps(metrics, sort_keys=True), flush=True)
        finally:
            await downloader.close()
            await parser.close_session()


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--note-id', required=True)
    cli.add_argument('--since', required=True)
    cli.add_argument('--override')
    cli.add_argument('--cold', action='store_true')
    args = cli.parse_args()
    try:
        asyncio.run(profile(args))
    except Exception as exc:
        print(json.dumps({'error_type': type(exc).__name__}), flush=True)
        raise SystemExit(1)
