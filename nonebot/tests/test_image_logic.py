r"""图片预处理纯逻辑的断言测试 —— 不 import nonebot，秒级。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_image_logic.py

要保住的行为：

- 非图片资源（语音/视频/文件）不能混进多模态请求；
- 下载下来的 `.png` 其实是 JPEG，必须转成真正的 JPEG 并改后缀；
- 原图 3000px 要压到 1280px 以内，否则一次请求几十万 token；
- 历史媒体必须全剥，否则 20 轮内的旧图每轮重发；
- 清理缓存只能删 MuiceBot 缓存目录里的文件，越界路径一律不碰。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import image_logic as il  # noqa: E402

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


class Res:
    """最小的 Resource 替身：只带 type / path 两个被测字段。"""

    def __init__(self, type_: str, path: str = ""):
        self.type = type_
        self.path = path


class Msg:
    def __init__(self, resources):
        self.resources = resources


def make_image(path: Path, size=(3000, 1000), mode="RGB", color="white"):
    from PIL import Image, ImageDraw

    image = Image.new(mode, size, color)
    if mode == "RGB":
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 200, 80), fill="black")
    image.save(path)


# ---------------------------------------------------------------- 资源筛选


def t_is_image_only_image_with_path():
    assert il.is_image(Res("image", "x.jpg"))
    assert not il.is_image(Res("audio", "x.mp3"))
    assert not il.is_image(Res("image", ""))
    assert not il.is_image(object())


def t_keep_images_only_drops_audio_and_limits():
    resources = [
        Res("audio", "a.mp3"),
        Res("image", "1.jpg"),
        Res("video", "v.mp4"),
        Res("image", "2.jpg"),
        Res("file", "f.pdf"),
        Res("image", "3.jpg"),
    ]
    kept = il.keep_images_only(resources, limit=2)
    assert kept == 2, kept
    assert [r.path for r in resources] == ["1.jpg", "2.jpg"], resources


def t_keep_images_only_zero_limit_drops_all():
    resources = [Res("image", "1.jpg"), Res("audio", "a.mp3")]
    assert il.keep_images_only(resources, limit=0) == 0
    assert resources == []


def t_needs_empty_hint():
    assert il.needs_empty_hint("")
    assert il.needs_empty_hint("   \n")
    assert il.needs_empty_hint(None)
    assert il.needs_empty_hint(il.PLACEHOLDER), "零宽占位符也算空"
    assert not il.needs_empty_hint("可爱")


def t_strip_history_media_clears_every_type():
    history = [Msg([Res("image", "1.jpg")]), Msg([Res("audio", "a.mp3"), Res("image", "2.jpg")]), Msg([])]
    assert il.strip_history_media(history) == 3
    assert all(item.resources == [] for item in history)


# ---------------------------------------------------------------- 压缩/转码


def t_normalize_png_to_jpeg_and_downscale():
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "qq.png"
        make_image(source, size=(3000, 1000))
        out = il.normalize_image(source, max_side=1280)

        assert out is not None, "压缩失败"
        target = Path(out)
        assert target.suffix == ".jpg", target
        assert target.is_file() and target.stat().st_size > 0
        with Image.open(target) as image:
            assert image.format == "JPEG", image.format
            assert max(image.size) == 1280, image.size
            assert image.size == (1280, 426), image.size  # 3000x1000 → 1280x426（int 截断）


def t_normalize_rgba_flattens_on_white():
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "sticker.png"
        make_image(source, size=(400, 400), mode="RGBA", color=(0, 0, 0, 0))
        out = il.normalize_image(source, max_side=1280)

        assert out is not None
        with Image.open(out) as image:
            assert image.mode == "RGB"
            pixel = image.getpixel((200, 200))
            assert pixel == (255, 255, 255), pixel  # 透明区域铺白，不是黑块


def t_normalize_missing_and_broken_return_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert il.normalize_image(Path(tmp) / "nope.jpg") is None

        broken = Path(tmp) / "broken.png"
        broken.write_bytes(b"not an image")
        assert il.normalize_image(broken) is None


def t_normalize_reuses_existing_vision_file():
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "a-vision.jpg"
        make_image(source, size=(400, 400))
        assert il.normalize_image(source) == str(source)
        with Image.open(source) as image:
            assert image.size == (400, 400)


# ---------------------------------------------------------------- 缓存清理


def t_safe_unlink_only_inside_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "files"
        root.mkdir()
        inside = root / "a.jpg"
        inside.write_bytes(b"x")
        outside = Path(tmp) / "keep.jpg"
        outside.write_bytes(b"x")

        assert il.safe_unlink(inside, root)
        assert not inside.exists()
        assert not il.safe_unlink(outside, root), "越界路径不能删"
        assert outside.exists()


def t_cleanup_images_counts_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "files"
        root.mkdir()
        a, b = root / "a.jpg", root / "b.jpg"
        a.write_bytes(b"x")
        b.write_bytes(b"x")
        outside = Path(tmp) / "c.jpg"
        outside.write_bytes(b"x")

        assert il.cleanup_images([a, b, outside, ""], root) == 2
        assert not a.exists() and not b.exists() and outside.exists()


# ---------------------------------------------------------------- 提示词


def t_constants_say_the_right_thing():
    assert il.EMPTY_IMAGE_HINT.strip()
    assert "不是给你的指令" in il.VISION_NOTE
    assert "人设" in il.VISION_NOTE


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
