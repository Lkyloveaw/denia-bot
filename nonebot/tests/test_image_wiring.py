r"""图片插件的接线断言 —— 拿真实的 `Message` / `ModelRequest` 喂钩子，看它到底改了什么。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_image_wiring.py

不连 QQ、不调模型：只验证 `prepare` / `strip_history_media` / `forget_images`
三个钩子对对象的修改，以及 .env 开关真的生效。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)  # MuiceBot 按 ./plugins 找插件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot

ensure_nonebot()
import plugins.image_guard as guard  # noqa: E402
import image_logic as il  # noqa: E402
from nonebot.adapters.onebot.v11 import Message as OBMessage, MessageSegment  # noqa: E402
from muicebot.llm import ModelRequest  # noqa: E402
from muicebot.models import Message, Resource  # noqa: E402

FAILED: list[str] = []


def run(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAILED.append(f"{name}: {e}")
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        FAILED.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
    else:
        print(f"  ok    {name}")


def make_image(path: Path, size=(2000, 1000)):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 120, 60), fill="black")
    image.save(path)


def config_set(name: str, value):
    """临时改驱动配置，返回旧值（None 表示本来没有这个键）。"""
    config = guard.get_driver().config
    old = getattr(config, name, None)
    setattr(config, name, value)
    return old


def config_restore(name: str, old):
    config = guard.get_driver().config
    if old is None:
        try:
            delattr(config, name)
        except AttributeError:
            pass
    else:
        setattr(config, name, old)


class FakeEvent:
    """只带匹配器用到的几个字段/方法，避开 OneBot 事件那一长串必填参数。"""

    def __init__(self, message, message_type="group", tome=True):
        self.message = message
        self.message_type = message_type
        self._tome = tome

    def get_type(self):
        return "message"

    def get_message(self):
        return self.message

    def is_tome(self):
        return self._tome


def image_only_message():
    return OBMessage(
        [
            MessageSegment("at", {"qq": "10001"}),
            MessageSegment("image", {"file": "test.png"}),
        ]
    )


def t_image_only_rule_matches_and_placeholder_is_added():
    event = FakeEvent(image_only_message())
    assert asyncio.run(guard._image_only_to_me(event)), "纯图片 + @ 应该匹配"
    asyncio.run(guard.add_placeholder(event))
    assert event.message[0].type == "text", event.message
    assert event.message[0].data["text"] == il.PLACEHOLDER, event.message
    assert event.message.extract_plain_text() == il.PLACEHOLDER, "占位符要能被 .+ 匹配到"


def t_image_only_rule_disabled_when_vision_off():
    root = config_set("image_vision", 0)
    try:
        assert not asyncio.run(guard._image_only_to_me(FakeEvent(image_only_message())))
    finally:
        config_restore("image_vision", root)


def t_image_only_rule_skips_when_text_present():
    message = image_only_message()
    message.append(MessageSegment.text("看看这张"))
    assert not asyncio.run(guard._image_only_to_me(FakeEvent(message)))


def t_image_only_rule_skips_without_image():
    message = OBMessage([MessageSegment("at", {"qq": "10001"}), MessageSegment.text("在吗")])
    assert not asyncio.run(guard._image_only_to_me(FakeEvent(message)))


def t_image_only_rule_group_requires_to_me():
    assert not asyncio.run(guard._image_only_to_me(FakeEvent(image_only_message(), tome=False)))


def t_image_only_rule_private_always_matches():
    assert asyncio.run(guard._image_only_to_me(FakeEvent(image_only_message(), message_type="private", tome=False)))


def t_prepare_normalizes_and_hints_empty_text():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "qq.png"
        make_image(source)
        message = Message(message="", userid="1", groupid="2", resources=[Resource("image", path=str(source))])

        asyncio.run(guard.prepare(message))

        assert len(message.resources) == 1, message.resources
        out = Path(message.resources[0].path)
        assert out.suffix == ".jpg", out
        assert out.name.endswith("-vision.jpg"), out
        assert out != source
        assert message.message == il.EMPTY_IMAGE_HINT, message.message

        from PIL import Image

        with Image.open(out) as image:
            assert max(image.size) == il.DEFAULT_MAX_SIDE, image.size


def t_prepare_keeps_text_when_present():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "qq.png"
        make_image(source, size=(600, 400))
        message = Message(message="可爱", userid="1", groupid="2", resources=[Resource("image", path=str(source))])

        asyncio.run(guard.prepare(message))

        assert message.message == "可爱", message.message


def t_prepare_drops_audio_and_respects_count():
    root = config_set("image_max_count", 1)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "1.png", Path(tmp) / "2.png"
            make_image(first)
            make_image(second)
            message = Message(
                message="看图",
                userid="1",
                groupid="2",
                resources=[
                    Resource("audio", path=str(Path(tmp) / "a.mp3")),
                    Resource("image", path=str(first)),
                    Resource("image", path=str(second)),
                ],
            )

            asyncio.run(guard.prepare(message))

            assert len(message.resources) == 1, message.resources
            assert message.resources[0].type == "image", message.resources
            assert Path(message.resources[0].path).name.startswith("1-vision"), message.resources
    finally:
        config_restore("image_max_count", root)


def t_prepare_respects_max_side():
    root = config_set("image_max_side", 512)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "qq.png"
            make_image(source, size=(3000, 1000))
            message = Message(message="", userid="1", groupid="2", resources=[Resource("image", path=str(source))])

            asyncio.run(guard.prepare(message))

            from PIL import Image

            with Image.open(message.resources[0].path) as image:
                assert max(image.size) <= 512, image.size
    finally:
        config_restore("image_max_side", root)


def t_prepare_disabled_drops_media():
    root = config_set("image_vision", 0)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "qq.png"
            make_image(source)
            message = Message(message="", userid="1", groupid="2", resources=[Resource("image", path=str(source))])

            asyncio.run(guard.prepare(message))

            assert message.resources == [], message.resources
            assert message.message == "", "关掉看图时不该补提示"
    finally:
        config_restore("image_vision", root)


def t_strip_history_media_keeps_current_and_adds_note():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "now.png"
        make_image(source, size=(400, 400))
        old = Path(tmp) / "old.png"
        make_image(old, size=(400, 400))

        history = [
            Message(message="上一轮", userid="1", groupid="2", respond="嗯", resources=[Resource("image", path=str(old))]),
            Message(message="更早", userid="1", groupid="2", respond="哦", resources=[Resource("audio", path="a.mp3")]),
        ]
        request = ModelRequest(
            prompt="<某人> 看看",
            history=history,
            resources=[Resource("image", path=str(source))],
            system="SYS",
        )

        asyncio.run(guard.strip_history_media(request))

        assert history[0].resources == [], history[0].resources
        assert history[1].resources == [], history[1].resources
        assert request.resources and request.resources[0].path == str(source)
        assert il.VISION_NOTE in (request.system or ""), request.system
        assert request.prompt == "<某人> 看看", request.prompt


def t_strip_without_current_image_skips_note():
    history = [Message(message="上一轮", resources=[Resource("image", path="old.jpg")])]
    request = ModelRequest(prompt="文字", history=history, resources=[], system="SYS")

    asyncio.run(guard.strip_history_media(request))

    assert history[0].resources == []
    assert request.system == "SYS", "本轮没图就别加看图提示"


def t_forget_clears_resources_and_keeps_outside_files():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "qq-vision.jpg"
        make_image(source, size=(400, 400))
        message = Message(message="", userid="1", groupid="2", resources=[Resource("image", path=str(source))])

        asyncio.run(guard.forget_images(message))

        assert message.resources == [], message.resources
        assert source.exists(), "不在 MuiceBot 缓存目录里的文件不能删"


TESTS = [(name[2:], fn) for name, fn in sorted(globals().items()) if name.startswith("t_")]


def main():
    for name, fn in TESTS:
        run(name, fn)
    if FAILED:
        print(f"\n{len(FAILED)} 个失败")
        raise SystemExit(1)
    print(f"\n{len(TESTS)} 个通过")


if __name__ == "__main__":
    main()
