"""推特解析器。

数据来源有两层：

1. **fxtwitter**（`api.fxtwitter.com`）：给出完整元数据 —— 作者名、简介、头像、
   正文、原图（`?name=orig` 是全尺寸）、视频直链。优先用它。
2. **xdown**（原实现）：只返回一堆下载按钮的 HTML，**没有作者也没有正文**，
   所以早期版本只能写死「无用户名」，正文也出不来。保留它作为 fxtwitter
   不可用时的兜底，至少媒体还能发。

两者的图片都走 `create_image_contents`，下载器负责落地。
"""

import re
from itertools import chain
from typing import Any, ClassVar

from aiohttp import ClientError
from bs4 import BeautifulSoup, Tag

from nonebot import logger

from ..config import PluginConfig
from ..cookie import CookieJar
from ..data import ParseResult, Platform
from ..download import Downloader
from ..exception import ParseException
from .base import BaseParser, handle

FX_API = "https://api.fxtwitter.com"
"""fxtwitter 的公开 API，无 key。"""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _text_of(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


class TwitterParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="twitter", display_name="推特")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.twitter
        # 给 fxtwitter 用：它公开、不挑 header，但带上 UA 更稳
        self.fx_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        self.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://xdown.app",
                "Referer": "https://xdown.app/",
            }
        )
        self.xdown_url = "https://xdown.app/api/ajaxSearch"
        self.cookiejar = CookieJar(config, self.mycfg, domain="xdown.app")
        if self.cookiejar.cookies_str:
            self.headers["cookie"] = self.cookiejar.cookies_str

    # ------------------------------------------------------------ fxtwitter
    async def _req_fx_api(self, screen_name: str, tweet_id: str) -> dict[str, Any]:
        url = f"{FX_API}/{screen_name}/status/{tweet_id}"
        async with self.session.get(url, headers=self.fx_headers) as resp:
            if resp.status >= 400:
                raise ClientError(f"fxtwitter {resp.status} {resp.reason}")
            data = await resp.json(content_type=None)
        if not isinstance(data, dict) or data.get("code") != 200:
            raise ParseException("fxtwitter 未返回该推文")
        tweet = data.get("tweet")
        if not isinstance(tweet, dict):
            raise ParseException("fxtwitter 返回缺少 tweet")
        return tweet

    def _build_from_fx(self, tweet: dict[str, Any]) -> ParseResult:
        author = tweet.get("author") or {}
        name = _text_of(author.get("name")) or _text_of(author.get("screen_name"))
        avatar = _text_of(author.get("avatar_url"))
        text = _text_of(tweet.get("text"))

        # 头像换成原始尺寸，`_200x200` 那种缩略图做卡片头像太糊
        if avatar:
            avatar = re.sub(r"_(normal|bigger|mini|\d+x\d+)\.(\w+)$", r".\2", avatar)

        contents: list = []
        media = tweet.get("media") or {}
        videos = [m for m in (media.get("videos") or []) if isinstance(m, dict)]
        photos = [m for m in (media.get("photos") or []) if isinstance(m, dict)]

        if videos:
            best = max(videos, key=lambda m: (int(m.get("width") or 0), int(m.get("height") or 0)))
            video_url = _text_of(best.get("url"))
            cover = _text_of(best.get("thumbnail_url"))
            if video_url:
                contents.append(self.create_video_content(video_url, cover or None, headers=self.fx_headers))

        # 有视频时也把配图带上（推文常是「视频 + 图」组合）
        image_urls = [_text_of(m.get("url")) for m in photos]
        image_urls = [u for u in image_urls if u]
        if image_urls:
            # 媒体都在 pbs.twimg.com，用推特 header
            contents.extend(self.create_image_contents(image_urls, headers=self.fx_headers))

        # 纯文字推文也照样出卡片：作者、头像、正文都是有效信息，
        # 早期版本在这里直接抛错，导致一条纯文字推文完全解析失败。
        return self.result(
            # 不给 title：头部已经显示头像、昵称、时间，正文里也有全部内容，
            # 再放一行作者名就是重复。推文没有「标题」这个概念。
            title=None,
            text=text,
            # 头像在 pbs.twimg.com，用推特自己的 header；传 xdown 那套会被拒
            author=self.create_author(name or "未知用户", avatar or None, headers=self.fx_headers),
            contents=contents,
            timestamp=int(tweet.get("created_timestamp") or 0),
        )

    # ------------------------------------------------------------- xdown
    async def _req_xdown_api(self, url: str) -> dict[str, Any]:
        async with self.session.post(
            url=self.xdown_url,
            data={"q": url, "lang": "zh-cn"},
            headers=self.headers,
        ) as resp:
            if resp.status >= 400:
                raise ClientError(f"xdown API {resp.status} {resp.reason}")
            return await resp.json()

    @handle(
        "twitter.com",
        (
            r"(?<![A-Za-z0-9.-])(?:(?:www|mobile)\.)?twitter\.com/"
            r"(?P<user>[A-Za-z0-9_]+)/status/(?P<tid>\d+)"
        ),
    )
    @handle(
        "x.com",
        (
            r"(?<![A-Za-z0-9.-])(?:www\.)?x\.com/"
            r"(?:[A-Za-z0-9_]+/)*status/(?P<tid2>\d+)"
        ),
    )
    async def _parse(self, searched: re.Match[str]) -> ParseResult:
        url = f"https://{searched.group(0)}"
        tweet_id = searched.groupdict().get("tid") or searched.groupdict().get("tid2") or ""
        user = searched.groupdict().get("user") or ""

        if tweet_id:
            try:
                tweet = await self._req_fx_api(user or "i", tweet_id)
                return self._build_from_fx(tweet)
            except Exception as exc:  # noqa: BLE001 - 退回 xdown，至少把媒体发出去
                logger.warning(f"[twitter] fxtwitter 失败（{type(exc).__name__}: {exc}），改用 xdown")

        resp = await self._req_xdown_api(url)
        if resp.get("status") != "ok":
            raise ParseException("解析失败")
        html_content = resp.get("data")
        if html_content is None:
            raise ParseException("解析失败, 数据为空")
        return self.parse_twitter_html(html_content)

    def parse_twitter_html(self, html_content: str) -> ParseResult:
        """解析 xdown 返回的 HTML（兜底路径）。

        这个 HTML 里只有下载按钮，**没有作者和正文**，所以 title 取 h3、
        作者留空而不是编一个假名字 —— 面板和卡片上不显示比显示错误信息好。
        """
        soup = BeautifulSoup(html_content, "html.parser")

        title = None
        cover_url = None
        video_url = None
        images_urls: list[str] = []
        dynamic_urls: list[str] = []

        thumb_tag = soup.find("img")
        if isinstance(thumb_tag, Tag):
            if cover := thumb_tag.get("src"):
                cover_url = str(cover)

        tw_button_tags = soup.find_all("a", class_="tw-button-dl")
        abutton_tags = soup.find_all("a", class_="abutton")
        for tag in chain(tw_button_tags, abutton_tags):
            if not isinstance(tag, Tag):
                continue
            href = tag.get("href")
            if href is None:
                continue
            href = str(href)
            text = tag.get_text(strip=True)
            if "下载 MP4" in text:
                video_url = href
                break
            elif "下载图片" in text:
                images_urls.append(href)
            elif "下载 gif" in text:
                dynamic_urls.append(href)

        title_tag = soup.find("h3")
        if title_tag:
            title = title_tag.get_text(strip=True)

        contents = []
        if video_url:
            contents.append(self.create_video_content(video_url, cover_url))
        if images_urls:
            contents.extend(self.create_image_contents(images_urls))
        if dynamic_urls:
            contents.extend(self.create_dynamic_contents(dynamic_urls))

        return self.result(
            title=title,
            author=self.create_author("推特用户"),
            contents=contents,
        )
