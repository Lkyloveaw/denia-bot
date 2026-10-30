"""小红书解析器。

与上游版本相比，这里整合了网页端下载脚本的三个关键做法：

1. **取无水印原图**：`imageList` 里的 `urlDefault` / `url` 是带水印的压缩图，
   真正的原图要从 URL 里抽出 token，再拼成 ``https://ci.xiaohongshu.com/<token>``。
2. **HEIC 处理**：苹果手机拍的图原图是 HEIF，扩展名仍叫 .jpg，QQ 显示不了。
   优先用 pillow-heif 本地转成 JPEG（保留原分辨率）；转不动才退回 CDN 压缩图。
3. **兼容新版视频流**：新版接口 ``media.stream`` 的键是 ``EF4/EF5/EF6/EF7``，
   老版本只认 h264/h265，遇到新视频会直接解析失败。

注意：本文件**不能**加 ``from __future__ import annotations``。下面几个 msgspec
Struct 定义在函数体内，注解被延迟成字符串后，msgspec 解析类型时会找不到这些
局部名字（先报 `name 'User' is not defined`，再回退到另一个分支一起失败）。
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any, ClassVar

from msgspec import Struct, convert, field

from nonebot import logger

from ..config import PluginConfig
from ..cookie import CookieJar
from ..data import ImageContent
from ..download import Downloader
from .base import BaseParser, ParseException, Platform, handle

CI_HOST = "https://ci.xiaohongshu.com"
"""无水印原图前缀：ci.xiaohongshu.com/<token>"""

_TOKEN_IN_PATH = re.compile(r"/\d+/[a-f0-9]+/([^!]+)")
"""原图 token 在 CDN 路径里的位置：/<数字>/<hash>/<token>"""

IMAGE_SUFFIX = {"image/jpeg": ".jpg", "image/png": ".png",
                "image/webp": ".webp", "image/gif": ".gif"}
"""按响应 MIME 修正扩展名；HEIC 不在其中 —— 那是苹果私有格式，QQ 显示不了。"""

HEIF_BRANDS = (b"heic", b"heix", b"mif1", b"heim", b"heis", b"hevc", b"hevx")
"""HEIF/HEIC 的 ftyp brand。苹果手机传的原图常是这种格式，QQ 显示不了。"""


def extract_image_token(url: str | None) -> str | None:
    """从 CDN 图片 URL 里取出原图 token。

    形如 ``sns-webpic-qc.xhscdn.com/202610301929/<hash>/notes_pre_post/1040g3...``，
    取 ``notes_pre_post/1040g3...`` 这一段。
    """
    if not url:
        return None
    try:
        path = url.split("?", 1)[0]
        matched = _TOKEN_IN_PATH.search(path)
        if matched and len(matched.group(1)) >= 8:
            return matched.group(1)
        parts = url.split("/")
        if len(parts) >= 6:
            raw = "/".join(parts[5:]).split("!")[0]
            if len(raw) >= 8:
                return raw
    except (ValueError, IndexError):
        return None
    return None


def build_origin_url(token: str) -> str:
    """原图地址。"""
    return f"{CI_HOST}/{token}"


def pick_stream_url(stream: dict[str, Any] | None) -> str | None:
    """从视频流里挑一条：优先分辨率高的，同分辨率优先码率高的。

    键名不固定（老版 h264/h265，新版 EF4~EF7），因此不看键、只看数组内容。
    """
    if not isinstance(stream, dict):
        return None
    best: tuple[int, int, str] | None = None
    for value in stream.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            url = item.get("masterUrl") or next(
                (u for u in item.get("backupUrls") or [] if u), None)
            if not url:
                continue
            score = (int(item.get("height") or 0), int(item.get("videoBitrate") or 0))
            if best is None or score > best[:2]:
                best = (score[0], score[1], url.replace("http://", "https://", 1))
    return best[2] if best else None


def image_candidates(image: Struct) -> list[str]:
    """一张图的所有可尝试地址：原图优先，压缩图兜底。"""
    raw = (
        getattr(image, "urlDefault", None)
        or getattr(image, "url", None)
        or getattr(image, "urlSizeLarge", None)
        or ""
    )
    token = extract_image_token(raw)
    urls: list[str] = []
    if token:
        urls.append(build_origin_url(token))
    if raw and raw not in urls:
        urls.append(raw.replace("http://", "https://", 1))
    return urls


def declared_size(image: Struct) -> tuple[int, int] | None:
    """笔记里声明的图片原始尺寸（`imageList[i].width/height`）。"""
    try:
        w = int(getattr(image, "width", 0) or 0)
        h = int(getattr(image, "height", 0) or 0)
    except (TypeError, ValueError):
        return None
    return (w, h) if w > 0 and h > 0 else None


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.size
    except Exception:  # noqa: BLE001 - 只为诊断，量不到就算了
        return None


def is_heif(path: Path) -> bool:
    """按文件头判断是不是 HEIF/HEIC（ISO BMFF：第 4~12 字节含 ftyp + brand）。"""
    try:
        with path.open("rb") as fp:
            head = fp.read(16)
    except OSError:
        return False
    return len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS


def fix_image_suffix(path: Path) -> Path:
    """按文件头把扩展名改成真实格式。

    下载器按 URL 后缀命名，CDN 的图片一律落成 ``.jpg``，实际可能是 WebP/PNG。
    文件名和内容对不上时，有些客户端会按扩展名强行解码然后失败。
    """
    try:
        with path.open("rb") as fp:
            head = fp.read(16)
    except OSError:
        return path

    if head[:3] == b"\xff\xd8\xff":
        suffix = ".jpg"
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        suffix = ".png"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        suffix = ".webp"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        suffix = ".gif"
    else:
        return path

    if path.suffix.lower() == suffix:
        return path
    target = path.with_suffix(suffix)
    try:
        path.rename(target)
    except OSError:
        return path
    return target


def convert_heif_to_jpeg(path: Path) -> Path | None:
    """把 HEIF/HEIC 转成同目录的 JPEG，返回新路径；转不了返回 None。

    QQ 不认 HEIC，而苹果手机传的原图大量是这种格式。转码保留原始分辨率，
    比退回 CDN 压缩图（WebP，尺寸被压过）清晰得多。
    """
    if not is_heif(path):
        return None
    try:
        import pillow_heif
        from PIL import Image
    except ImportError:
        return None
    try:
        pillow_heif.register_heif_opener()
        # 落到临时文件再替换：HEIC 原图的扩展名本来就是 .jpg，
        # with_suffix(".jpg") 会指回源文件本身，直接保存等于边读边写。
        target = path.with_suffix(".jpg")
        temp = target.with_name(f"{target.stem}.heic-converting.jpg")
        with Image.open(path) as img:
            img.convert("RGB").save(temp, "JPEG", quality=95)
        temp.replace(target)
        if path != target:
            path.unlink(missing_ok=True)
        return fix_image_suffix(target)
    except Exception as e:  # noqa: BLE001 - 转码失败就交回调用方走回退
        logger.warning(f"HEIC 转 JPEG 失败（{path.name}）：{e}")
        return None


class XHSParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="xhs", display_name="小红书")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.mycfg = config.parser.xhs
        self.cookies = self.mycfg.cookies
        self.headers.update(
            {
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                    "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
                )
            }
        )
        self.ios_headers.update(
            {
                "origin": "https://www.xiaohongshu.com",
                "x-requested-with": "XMLHttpRequest",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }
        )
        self.cookiejar = CookieJar(config, self.mycfg, domain="xiaohongshu.com")
        if self.cookiejar.cookies_str:
            self.headers["cookie"] = self.cookiejar.cookies_str
            self.ios_headers["cookie"] = self.cookiejar.cookies_str

    def _pick_image_urls(self, images: list[Any]) -> list[str]:
        """每张图取原图地址，拿不到就取压缩图。"""
        urls: list[str] = []
        for image in images:
            candidates = image_candidates(image)
            if candidates:
                urls.append(candidates[0])
        return urls

    async def _download_image(self, image: Any):
        """下载一张图，返回可用的文件路径。

        原图常是苹果手机拍的 HEIC —— QQ 显示不了，别看扩展名是 .jpg。
        处理顺序：原图下载 → 是 HEIF 就本地转 JPEG（保留原分辨率）
                  → 转不动才退回 CDN 压缩图 → 最后纠正扩展名。
        下完记一笔「拿到多大 / 笔记声明多大」，用于判断是不是平台侧压过了。
        """
        candidates = image_candidates(image)
        if not candidates:
            raise ParseException("图片地址为空")

        path = await self.downloader.download_img(
            candidates[0], headers=self.headers, proxy=self.proxy
        )

        if is_heif(path):
            converted = await asyncio.to_thread(convert_heif_to_jpeg, path)
            if converted is not None:
                path = converted
            elif len(candidates) > 1:
                logger.info(f"HEIC 无法转换（{path.name}），改用压缩图")
                try:
                    path = await self.downloader.download_img(
                        candidates[1], headers=self.headers, proxy=self.proxy
                    )
                except Exception as e:  # noqa: BLE001 - 压缩图也失败就交回原图
                    logger.warning(f"HEIC 回退压缩图失败，仍用原图：{e}")

        path = fix_image_suffix(path)
        self._log_resolution(image, path)
        return path

    @staticmethod
    def _log_resolution(image: Any, path: Path) -> None:
        """对比「拿到的尺寸」与「笔记声明的尺寸」。

        小红书的 `1040g2sg…` 这类新 token 在 CDN 上只存了一份压缩版（1440 宽），
        无论怎么换路径都拿不到更大 —— 这不是解析器的问题，是平台侧只存了这份。
        `notes_pre_post/…` 那种则能拿到几千万像素的原始 JPEG。
        记一行日志，下次觉得「不像原图」时能直接确认是哪一种。
        """
        got = image_size(path)
        declared = declared_size(image)
        if not got:
            return
        if declared and declared[0] > got[0] + 8:
            logger.info(
                f"小红书图片尺寸 {got[0]}x{got[1]}，笔记声明 {declared[0]}x{declared[1]}"
                f"（平台只提供压缩版，非解析器问题）：{path.name}"
            )
        else:
            logger.debug(f"小红书图片尺寸 {got[0]}x{got[1]}：{path.name}")

    async def _image_contents(self, images: list[Any]) -> list[Any]:
        """把图片列表转成图片内容，每张走「原图优先 + HEIC 回退」。"""
        contents = []
        for image in images:
            task = self._download_image(image)
            contents.append(ImageContent(asyncio.ensure_future(task)))
        return contents

    async def _image_contents(self, images: list[Any]) -> list[Any]:
        """把图片列表转成图片内容，每张走「原图优先 + HEIC 回退」。"""
        contents = []
        for image in images:
            task = self._download_image(image)
            contents.append(ImageContent(asyncio.ensure_future(task)))
        return contents

    @handle("xhslink.com", r"xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+")
    @handle("xhslink.cn", r"xhslink\.cn/[A-Za-z0-9._?%&+=/#@-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url, self.ios_headers)

    # https://www.xiaohongshu.com/discovery/item/68e8e3fa00000000030342ec?app_platform=android&ignoreEngage=true&app_version=9.6.0&share_from_user_hidden=true&xsec_source=app_share&type=normal&xsec_token=CBW9rwIV2qhcCD-JsQAOSHd2tTW9jXAtzqlgVXp6c52Sw%3D&author_share=1&xhsshare=QQ&shareRedId=ODs3RUk5ND42NzUyOTgwNjY3OTo8S0tK&apptime=1761372823&share_id=3b61945239ac403db86bea84a4f15124&share_channel=qq
    @handle(
        "xiaohongshu.com",
        r"(explore|discovery/item|note)/(?P<query>(?P<xhs_id>[0-9a-zA-Z]+)(?:\?[A-Za-z0-9._%&+=/#@-]+)?)",
    )
    async def _parse_common(self, searched: re.Match[str]):
        xhs_domain = "https://www.xiaohongshu.com"
        query, xhs_id = searched.group("query", "xhs_id")

        try:
            return await self.parse_explore(f"{xhs_domain}/explore/{query}", xhs_id)
        except Exception as e:
            logger.warning(
                f"parse_explore failed, error: {e}, fallback to parse_discovery"
            )
            return await self.parse_discovery(f"{xhs_domain}/discovery/item/{query}")

    async def parse_explore(self, url: str, xhs_id: str):
        async with self.session.get(url, headers=self.headers) as resp:
            html = await resp.text()
            logger.debug(f"url: {resp.url} | status: {resp.status}")

        json_obj = self._extract_initial_state_json(html)

        # ["note"]["noteDetailMap"][xhs_id]["note"]
        note_data = (
            json_obj.get("note", {})
            .get("noteDetailMap", {})
            .get(xhs_id, {})
            .get("note", {})
        )
        if not note_data:
            raise ParseException("can't find note detail in json_obj")

        class Image(Struct):
            urlDefault: str
            url: str | None = None
            urlSizeLarge: str | None = None
            stream: dict[str, Any] | None = None
            width: int | None = None
            height: int | None = None
        class User(Struct):
            nickname: str
            avatar: str

        class NoteDetail(Struct):
            type: str
            title: str
            desc: str
            user: User
            imageList: list[Image] = field(default_factory=list)
            video: Video | None = None

            @property
            def nickname(self) -> str:
                return self.user.nickname

            @property
            def avatar_url(self) -> str:
                return self.user.avatar

            @property
            def image_urls(self) -> list[str]:
                return [item.urlDefault for item in self.imageList]

            @property
            def video_url(self) -> str | None:
                if self.type != "video" or not self.video:
                    return None
                return self.video.video_url

        note_detail = convert(note_data, type=NoteDetail)

        contents = []
        # 添加视频内容
        if video_url := note_detail.video_url:
            # 使用第一张图片的**原图**作为封面（压缩图做封面糊得厉害）
            cover_url = self._pick_image_urls(note_detail.imageList[:1])
            contents.append(
                self.create_video_content(video_url, cover_url[0] if cover_url else None)
            )

        # 添加图片内容
        elif note_detail.imageList:
            contents.extend(await self._image_contents(note_detail.imageList))

        # 构建作者
        author = self.create_author(note_detail.nickname, note_detail.avatar_url)

        return self.result(
            title=note_detail.title,
            text=note_detail.desc,
            author=author,
            contents=contents,
        )

    async def parse_discovery(self, url: str):
        async with self.session.get(
            url,
            headers=self.ios_headers,
            allow_redirects=True,
        ) as resp:
            html = await resp.text()

        json_obj = self._extract_initial_state_json(html)
        note_data = json_obj.get("noteData")
        if not note_data:
            raise ParseException("can't find noteData in json_obj")
        preload_data = note_data.get("normalNotePreloadData", {})
        note_data = note_data.get("data", {}).get("noteData", {})
        if not note_data:
            raise ParseException("can't find noteData in noteData.data")

        class Image(Struct):
            url: str
            urlSizeLarge: str | None = None
            stream: dict[str, Any] | None = None
            width: int | None = None
            height: int | None = None

        class User(Struct):
            nickName: str
            avatar: str

        class NoteData(Struct):
            type: str | None = None
            title: str | None = None
            desc: str | None = None
            user: User
            time: int = 0
            lastUpdateTime: int = 0
            imageList: list[Image] = field(default_factory=list)  # 有水印
            video: Video | None = None

            @property
            def image_urls(self) -> list[str]:
                return [item.url for item in self.imageList]

            @property
            def video_url(self) -> str | None:
                if self.type != "video" or not self.video:
                    return None
                return self.video.video_url

        class NormalNotePreloadData(Struct):
            title: str
            desc: str
            imagesList: list[Image] = field(default_factory=list)

            @property
            def image_urls(self) -> list[str]:
                return [item.urlSizeLarge or item.url for item in self.imagesList]

        note_data = convert(note_data, type=NoteData)

        contents = []
        if video_url := note_data.video_url:
            if preload_data:
                preload_data = convert(preload_data, type=NormalNotePreloadData)
                img_urls = self._pick_image_urls(preload_data.imagesList)
            else:
                img_urls = self._pick_image_urls(note_data.imageList)
            cover = img_urls[0] if img_urls else None
            contents.append(self.create_video_content(video_url, cover))
        elif note_data.imageList:
            contents.extend(await self._image_contents(note_data.imageList))

        return self.result(
            title=note_data.title or "",
            author=self.create_author(note_data.user.nickName, note_data.user.avatar),
            contents=contents,
            text=note_data.desc or "",
            timestamp=(note_data.time or 0) // 1000,
        )

    def _extract_initial_state_json(self, html: str) -> dict[str, Any]:
        pattern = r"window\.__INITIAL_STATE__=(.*?)</script>"
        matched = re.search(pattern, html, re.S)
        if not matched:
            raise ParseException("小红书分享链接失效或内容已删除")

        json_str = matched.group(1).replace("undefined", "null")
        return json.loads(json_str)


class Stream(Struct):
    """视频流。

    老接口用 h264/h265/av1/h266，新接口用 EF4~EF7，这里全都收，
    否则新版视频会解析失败。
    """

    h264: list[dict[str, Any]] | None = None
    h265: list[dict[str, Any]] | None = None
    av1: list[dict[str, Any]] | None = None
    h266: list[dict[str, Any]] | None = None


class Media(Struct):
    stream: dict[str, Any]


class Video(Struct):
    media: Media

    @property
    def video_url(self) -> str | None:
        # 键名因版本而异，pick_stream_url 不看键、只按分辨率和码率挑最好的一条
        return pick_stream_url(self.media.stream)
