"""图片理解接线 —— 让「看得到图」真正落到模型请求里，同时别把成本拖飞。

MuiceBot 1.1.5 本身已经会下载 QQ 图片（`onebot.py:510` `_extract_multi_resources`），
也会在模型条目 `multimodal: true` 时把图编成 `image_url` 内容块
（`llm/providers/openai.py:62-69`）。缺的是四件「默认不会替你做」的事：

1. **历史图会每轮重发**：`_prepare_history()` 只按文件还在不在过滤资源，
   而 `Msg.resources` 是落库的。开多模态后，20 轮内的旧图会在后续每轮重新 base64。
   这里在 `BEFORE_MODEL_COMPLETION` 把历史媒体全部剥掉，只让当前这条进模型。
2. **下载后缀是错的**：OneBot11 的 `uniseg.Image.name` 默认 `image.png`，
   JPEG 也存成 `.png`，provider 按后缀拼 `data:image/png`。这里统一转 JPEG、改后缀。
3. **纯图片消息根本进不了 handler**：MuiceBot 的 `at_event` 命令是 `re.compile(".+")`
   （`onebot.py:248`），只发图、不打字时 `extract_plain_text()` 为空，`.+` 匹配不上。
   这里在它前面（priority 1、`block=False`）补一个 `[图片]` 占位符，让 `.+` 匹配；
   随后 `prepare` 再把它换成正式的占位说明。占位符必须插在图片**前面** ——
   图片在前、文字在后时 Alconna 仍可能匹配不到。
4. **只发图不打字时正文为空**：补一句「对方发来一张图片，没有配文字」，
   模型才知道该看图，而不是收到一个空句子。

另外把原图压到最长边 `IMAGE_MAX_SIDE`，并在回复结束、存库前删掉缓存图：
既然历史图不参与对话，留着只会占磁盘。

开关在 `nonebot/.env`（改了要重启，插件不热加载）：

    IMAGE_VISION=1        # 0 = 不看图：本轮媒体在进模型前直接丢掉（回滚用）
    IMAGE_MAX_SIDE=1280   # 最长边像素；越大越清楚，token 越多
    IMAGE_MAX_COUNT=4     # 一条消息最多带几张图，多的丢掉

出问题时看日志里的 `[image]` 前缀。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from muicebot.llm import ModelRequest
from muicebot.models import Message as ChatMessage  # noqa: F401  (钩子靠类型注解注入)
from muicebot.plugin import PluginMetadata
from muicebot.plugin.hook import on_before_completion, on_before_pretreatment, on_finish_chat

try:
    import image_logic as il
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import image_logic as il

__plugin_meta__ = PluginMetadata(
    name="image_guard",
    description="让模型真的看到 QQ 图片：压缩/纠正格式、剥离历史图、纯图片补提示",
    usage="无需指令；开关在 nonebot/.env 的 IMAGE_VISION / IMAGE_MAX_SIDE / IMAGE_MAX_COUNT",
)

_FILES_ROOT: Optional[Path] = None


def _flag(name: str, default: bool = True) -> bool:
    """读 .env 里的开关。NoneBot 的 DriverConfig 是 extra=allow，自定义键直接落在上面。"""
    try:
        raw = getattr(get_driver().config, name, None)
    except Exception:  # noqa: BLE001 - 驱动还没起来（测试里直接调钩子函数）
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "关")


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(getattr(get_driver().config, name, default) or default)
    except Exception:  # noqa: BLE001
        return default
    return max(low, min(high, value))


def _muice_files_root() -> Optional[Path]:
    """MuiceBot 的图片缓存目录（`nonebot_plugin_localstore.get_plugin_data_dir()/files`）。

    MuiceBot 用 `get_plugin_data_dir()`（调用方插件名），我们这边调会落到自己的插件目录，
    所以显式传 `muicebot`。拿不到就返回 None —— 宁可少删文件，也不能删错目录。
    """
    global _FILES_ROOT
    if _FILES_ROOT is not None:
        return _FILES_ROOT
    try:
        from nonebot_plugin_localstore import get_data_dir

        _FILES_ROOT = (get_data_dir("muicebot") / "files").resolve()
        return _FILES_ROOT
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[image] 定位 MuiceBot 缓存目录失败（本轮不删缓存）: {e}")
        return None


async def _image_only_to_me(event: MessageEvent) -> bool:
    """纯图片、没文字、而且是在叫她 / 私聊 —— 这种消息 MuiceBot 的 `.+` 匹配不上。"""
    try:
        if not _flag("image_vision"):
            return False  # 回滚状态：保持 MuiceBot 原样，纯图片消息照旧不触发
        if event.get_type() != "message":
            return False
        message = event.get_message()
        if not any(segment.type == "image" for segment in message):
            return False
        if message.extract_plain_text().strip():
            return False
        if getattr(event, "message_type", "") == "private":
            return True
        is_tome = getattr(event, "is_tome", None)
        return bool(is_tome and is_tome())
    except Exception:  # noqa: BLE001 - 判定出错时不动消息，交给原来的匹配器
        return False


_placeholder = on_message(rule=_image_only_to_me, priority=1, block=False)


@_placeholder.handle()
async def add_placeholder(event: MessageEvent) -> None:
    """在最前面补一个 `[图片]`，让 MuiceBot 的 `Alconna(re.compile(".+"))` 能匹配上。

    为什么不用空格：Alconna 匹配前会把空白 normalize 掉，空格等于没补。
    为什么必须插在图片前面：实测「图片在前、文字在后」时 Alconna 仍可能匹配不到，
    `[图片] + 图片` 的顺序才稳。真正进提示词前，`prepare` 会把它换成
    `EMPTY_IMAGE_HINT`。
    """
    event.message.insert(0, MessageSegment.text(il.PLACEHOLDER))
    original = getattr(event, "original_message", None)
    if original is not None and hasattr(original, "insert") and original is not event.message:
        # 原始消息开头通常是 @，插在它后面，别影响 gate 的 @ 判定
        at = 1 if original and original[0].type == "at" else 0
        original.insert(at, MessageSegment.text(il.PLACEHOLDER))
    logger.info(
        f"[image] 纯图片消息补占位符：segments={[segment.type for segment in event.message]} "
        f"text={event.get_message().extract_plain_text()!r} to_me={event.is_tome()}"
    )


@on_before_pretreatment(priority=5)
async def prepare(message: ChatMessage) -> None:
    """在 MuiceBot 拼提示词之前：只留图片、压小、纠正格式、纯图片补说明。"""
    if not message.resources:
        return

    if not _flag("image_vision"):
        message.resources = []
        logger.info("[image] IMAGE_VISION=0，本轮媒体已丢弃（模型不会看到图）")
        return

    max_count = _int("image_max_count", il.DEFAULT_MAX_COUNT, 0, 10)
    max_side = _int("image_max_side", il.DEFAULT_MAX_SIDE, 256, 4096)

    count = il.keep_images_only(message.resources, max_count)
    if count == 0:
        return

    files_root = _muice_files_root()
    normalized = 0
    for resource in message.resources:
        old_path = resource.path
        new_path = il.normalize_image(old_path, max_side=max_side)
        if not new_path:
            continue
        resource.path = new_path
        normalized += 1
        # 原图已不再被引用（历史图一会儿也会剥掉），当场删掉，别等清理任务。
        if files_root and old_path and Path(old_path).resolve() != Path(new_path).resolve():
            il.safe_unlink(old_path, files_root)

    hinted = il.needs_empty_hint(message.message)
    if hinted:
        message.message = il.EMPTY_IMAGE_HINT

    logger.info(
        f"[image] 本轮 {count} 张图（压缩 {normalized} 张，最长边 {max_side}px"
        f"{'，纯图片已补说明' if hinted else ''}）"
    )


@on_before_completion(priority=2)
async def strip_history_media(request: ModelRequest) -> None:
    """在发给模型前：历史媒体全剥，只保留当前消息的图；并补一句「怎么读图」。"""
    removed = il.strip_history_media(request.history)

    if request.resources:
        if il.VISION_NOTE not in (request.system or ""):
            request.system = (request.system or "") + il.VISION_NOTE
        logger.info(f"[image] 本轮 {len(request.resources)} 张图进模型，历史媒体剥离 {removed} 个")
    elif removed:
        logger.debug(f"[image] 本轮无图，历史媒体剥离 {removed} 个")
    else:
        logger.debug("[image] 本轮无图；若你刚发了图却没这行，检查 models.yml 的 multimodal 是否为 true")


@on_finish_chat(priority=5)
async def forget_images(message: ChatMessage) -> None:
    """回复已发出、即将存库：缓存图用完就删，资源列表清空（历史不再带图）。"""
    if not message.resources:
        return

    files_root = _muice_files_root()
    paths = [resource.path for resource in message.resources if resource.path]
    removed = il.cleanup_images(paths, files_root) if files_root else 0
    message.resources = []
    if removed:
        logger.debug(f"[image] 已删除本轮 {removed} 个图片缓存")
