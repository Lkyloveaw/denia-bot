from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from media_parser.core.parsers.twitter import TwitterParser, USER_AGENT


def test_fx_video_and_cover_do_not_use_xdown_headers():
    parser = object.__new__(TwitterParser)
    parser.cfg = SimpleNamespace(parser=SimpleNamespace(twitter=SimpleNamespace(use_proxy=True)),
                                 proxy='http://proxy.example:8080')
    parser.headers = {'Origin': 'https://xdown.app', 'Referer': 'https://xdown.app/'}
    parser.fx_headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    parser.downloader = SimpleNamespace(
        download_video=Mock(return_value=Path('video.mp4')),
        download_img=Mock(return_value=Path('cover.jpg')),
    )
    parser._build_from_fx({'text': 'example', 'media': {'videos': [{
        'url': 'https://video.twimg.com/video.mp4',
        'thumbnail_url': 'https://pbs.twimg.com/cover.jpg',
        'width': 1280, 'height': 720,
    }]}})

    for download in (parser.downloader.download_video, parser.downloader.download_img):
        kwargs = download.call_args.kwargs
        assert kwargs['headers'] == parser.fx_headers
        assert 'Origin' not in kwargs['headers']
        assert 'Referer' not in kwargs['headers']
        assert kwargs['proxy'] == parser.cfg.proxy
