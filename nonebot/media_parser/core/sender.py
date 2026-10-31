import asyncio
from itertools import chain
from pathlib import Path

from nonebot import logger
from ..transport import (
    BaseMessageComponent,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Video,
)
from ..transport import Transport as AstrMessageEvent

from .config import PluginConfig
from .data import (
    AudioContent,
    DynamicContent,
    FileContent,
    GraphicsContent,
    ImageContent,
    ParseResult,
    SendGroup,
    TextContent,
    VideoContent,
)
from .exception import (
    DownloadException,
    DownloadLimitException,
    DurationLimitException,
    SizeLimitException,
    ZeroSizeException,
)
from .render import Renderer
from .utils import transcode_video_audio_to_lc


class MessageSender:
    """
    消息发送器

    职责：
    - 根据解析结果（ParseResult）规划发送策略
    - 控制是否渲染卡片、是否强制合并转发
    - 将不同类型的内容转换为 AstrBot 消息组件并发送

    重要原则：
    - 不在此处做解析
    - 不在此处决定“内容是什么”
    - 只负责“怎么发”
    """

    def __init__(self, config: PluginConfig, renderer: Renderer):
        self.cfg = config
        self.renderer = renderer

    def _to_file_uri(self, path: Path) -> str:
        if not path.is_absolute():
            path = path.resolve()
        return path.as_uri()

    @staticmethod
    def _image_from_path(path: Path) -> Image:
        return Image.fromFileSystem(str(path))

    @staticmethod
    def _video_from_path(path: Path) -> Video:
        return Video.fromFileSystem(str(path))

    @staticmethod
    def _record_from_path(path: Path) -> Record:
        return Record.fromFileSystem(str(path))

    @staticmethod
    def _iter_contents(result: ParseResult):
        return chain(result.contents, result.repost.contents if result.repost else ())

    def _build_send_plan(
        self,
        result: ParseResult,
        contents: list | tuple | None = None,
        *,
        force_merge_override: bool | None = None,
        render_card_override: bool | None = None,
    ) -> dict:
        """
        根据解析结果生成发送计划（plan）

        plan 只做“策略决策”，不做任何 IO 或发送动作。
        后续发送流程严格按 plan 执行，避免逻辑分散。
        """
        light, heavy = [], []

        # 合并主内容 + 转发内容，统一参与发送策略计算
        iterable = contents if contents is not None else self._iter_contents(result)
        for cont in iterable:
            match cont:
                case ImageContent() | GraphicsContent() | TextContent():
                    light.append(cont)
                case VideoContent() | AudioContent() | FileContent() | DynamicContent():
                    heavy.append(cont)
                case _:
                    light.append(cont)

        # 卡片渲染条件：
        #   - 恰好一个重媒体、没有轻媒体（单视频/单音频，最经典的情形）
        #   - 或者设置了 single_heavy_render_card 且内容全部是轻媒体（图文笔记）
        # 纯图文原来完全不渲染卡片：小红书笔记 9 张图命中这条，卡片永远不出。
        is_single_heavy = len(heavy) == 1 and not light
        only_light = bool(light) and not heavy
        render_card = (is_single_heavy or only_light) and self.cfg.single_heavy_render_card
        if render_card_override is not None:
            render_card = render_card_override

        # 实际媒体段数量
        media_count = len(light) + len(heavy)

        # 达到阈值后，强制合并转发，避免刷屏。
        # 卡片不计入媒体数量：它始终独立发送，不进合并转发。
        force_merge = media_count >= self.cfg.forward_threshold
        if force_merge_override is not None:
            force_merge = force_merge_override

        return {
            "light": light,
            "heavy": heavy,
            "render_card": render_card,
            # 卡片始终独立成一条消息，与合并转发无关
            "preview_card": render_card,
            "force_merge": force_merge,
        }

    async def _send_preview_card(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        plan: dict,
    ):
        """
        发送预览卡片（始终独立于媒体消息）

        只要 plan 要求渲染卡片，就先单独发一条卡片，再发媒体本身。
        卡片不再参与合并转发：内容多的时候只是媒体进转发，卡片仍然独立。
        """
        if not plan["preview_card"]:
            return

        if image_path := await self.renderer.render_card(result):
            await event.send(event.chain_result([self._image_from_path(image_path)]))

    async def _build_segments(
        self,
        result: ParseResult,
        plan: dict,
    ) -> list[BaseMessageComponent]:
        """
        根据发送计划构建消息段列表

        这里负责：
        - 下载媒体
        - 转换为 AstrBot 消息组件

        下载是**并发**的：内容一多，逐张 await 会把等待时间累加起来。这里先把
        所有下载一起发出去，再按原顺序取结果 —— 顺序不变，总耗时从「各张之和」
        降到「最慢的那一张」。
        """
        segs: list[BaseMessageComponent] = []

        # 卡片统一由 _send_preview_card 独立发送，这里不再插入，
        # 否则卡片会既单独发一条、又出现在合并转发里出现两次。

        # 先把所有媒体的下载一起触发，后面只负责收结果
        ordered: list[tuple[str, BaseMessageComponent, asyncio.Task | None]] = []
        for kind in ("light", "heavy"):
            for cont in plan[kind]:
                if isinstance(cont, TextContent):
                    ordered.append((kind, cont, None))
                else:
                    ordered.append((kind, cont, asyncio.ensure_future(cont.get_path())))

        pending = [task for _, _, task in ordered if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for kind, cont, task in ordered:
            if task is None:
                if isinstance(cont, TextContent) and cont.text:
                    segs.append(Plain(cont.text))
                continue

            try:
                path: Path = task.result()  # 已完成，异常在这里重新抛出
            except (SizeLimitException, DurationLimitException) as exc:
                if self.cfg.show_download_fail_tip:
                    segs.append(Plain(
                        "此项媒体超过时长限制"
                        if isinstance(exc, DurationLimitException)
                        else "此项媒体超过大小限制"
                    ))
                continue
            except (DownloadLimitException, ZeroSizeException):
                continue
            except DownloadException:
                if self.cfg.show_download_fail_tip:
                    segs.append(Plain("此项媒体下载失败"))
                continue

            match cont:
                case ImageContent():
                    segs.append(self._image_from_path(path))
                case GraphicsContent() as g:
                    segs.append(self._image_from_path(path))
                    # GraphicsContent 允许携带补充文本
                    if g.text:
                        segs.append(Plain(g.text))
                    if g.alt:
                        segs.append(Plain(g.alt))
                case VideoContent() | DynamicContent():
                    path = await transcode_video_audio_to_lc(path)
                    segs.append(self._video_from_path(path))
                case AudioContent():
                    segs.append(
                        File(name=path.name, file=self._to_file_uri(path))
                        if self.cfg.audio_to_file
                        else self._record_from_path(path)
                    )
                case FileContent():
                    segs.append(File(name=path.name, file=self._to_file_uri(path)))

        return segs

    def _merge_segments_if_needed(
        self,
        event: AstrMessageEvent,
        segs: list[BaseMessageComponent],
        force_merge: bool,
    ) -> list[BaseMessageComponent]:
        """
        是否把消息段打包成合并转发。

        这里**不再**合并：实测 7 张原图 ≈ 20 MB，打包成一个
        `send_group_forward_msg` 协议端要 45 秒才回，最后直接超时，
        一条都发不出去（日志里是 `send_group_forward_msg timeout`）。
        逐张发送的总耗时反而更短，也不会整批失败。

        保留参数是为了不改调用方签名；真要恢复合并，把 `force_merge`
        的判断加回来即可。
        """
        return segs

    @staticmethod
    def _build_text_fallback(result: ParseResult) -> list[BaseMessageComponent]:
        lines: list[str] = []
        if result.header:
            lines.append(result.header)
        if result.text:
            lines.append(result.text)
        elif result.extra.get("info"):
            lines.append(str(result.extra["info"]))

        text = "\n".join(line for line in lines if line).strip()
        return [Plain(text)] if text else []

    def _resolve_groups(self, result: ParseResult) -> list[SendGroup]:
        if result.send_groups:
            return result.send_groups
        return [SendGroup(contents=list(MessageSender._iter_contents(result)))]

    async def _send_group(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        group: SendGroup,
    ) -> bool:
        plan = self._build_send_plan(
            result,
            group.contents,
            force_merge_override=group.force_merge,
            render_card_override=group.render_card,
        )

        await self._send_preview_card(event, result, plan)

        segs = await self._build_segments(result, plan)
        segs = self._merge_segments_if_needed(event, segs, plan["force_merge"])

        if not segs:
            return False

        try:
            await event.send(event.chain_result(segs))
            return True
        except Exception as e:
            seg_meta = self._collect_seg_meta(segs)
            logger.error(f"发送解析结果失败： error={e}, segments={seg_meta}")
            # 整条发不出去时，图片单独再试一次：一次发一堆大图容易被协议端拒，
            # 逐张发成功率高得多，也好过整批丢掉
            recovered = await self._resend_images_one_by_one(event, segs)
            return recovered

    async def _resend_images_one_by_one(
        self, event: AstrMessageEvent, segs: list[BaseMessageComponent]
    ) -> bool:
        """整条发送失败后的补救：把图片段一张一张发出去。

        逐张发虽然会拆成多条消息，但至少图片能到群里；一次性发一堆大图
        被协议端拒掉时，用户看到的就是「什么都没收到」。
        """
        images = [seg for seg in segs if isinstance(seg, Image)]
        if not images:
            return False

        sent = 0
        for seg in images:
            try:
                await event.send(event.chain_result([seg]))
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"单张补发失败：{type(exc).__name__}: {exc}")
        if sent:
            logger.info(f"整条发送失败，已逐张补发 {sent}/{len(images)} 张图片")
        return sent > 0

    @staticmethod
    def _collect_seg_meta(segs: list[BaseMessageComponent]) -> list[dict[str, str]]:
        """提取消息段元信息，用于失败日志定位。"""
        meta: list[dict[str, str]] = []

        for seg in segs:
            item = {"type": seg.__class__.__name__}
            for attr in ("file", "path", "url"):
                value = getattr(seg, attr, None)
                if value:
                    item["media"] = str(value)
                    break
            meta.append(item)

        return meta

    async def send_parse_result(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
    ):
        """
        发送解析结果的统一入口

        执行顺序固定：
        1. 构建发送计划
        2. 发送预览卡片（如有）
        3. 构建消息段
        4. 必要时合并转发
        5. 最终发送
        """
        groups = self._resolve_groups(result)

        sent = False
        for group in groups:
            sent = await self._send_group(event, result, group) or sent

        if not sent:
            segs = self._build_text_fallback(result)
            if not segs:
                logger.warning("发送结果为空，不执行发送")
                return

            try:
                await event.send(event.chain_result(segs))
            except Exception as e:
                seg_meta = self._collect_seg_meta(segs)
                logger.error(f"发送解析结果失败： error={e}, segments={seg_meta}")
            return
