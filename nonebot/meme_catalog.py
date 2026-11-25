"""Bulk-import local sticker folders with deterministic, editable metadata.

The importer deliberately owns one fixed directory (`nonebot/data/stickers`).
It never accepts an arbitrary filesystem path from the dashboard.
"""
from __future__ import annotations

import colorsys
import io
import os
import stat
from collections.abc import Iterator
from pathlib import Path

from PIL import Image, ImageStat

import meme_logic

ROOT = Path(__file__).resolve().parent
DEFAULT_IMPORT_ROOT = ROOT / 'data' / 'stickers'
DATA_ROOT = ROOT / 'data'
MAX_IMAGES = 2000
MAX_CATEGORY_CHARS = 40
_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

_ROLE_TAGS = (
    ('守岸人', '角色:守岸人'),
    ('达妮娅', '角色:达妮娅'),
)
_SCENE_TAGS = (
    ('新年', '场景:新年'),
    ('演唱会', '场景:演唱会'),
    ('联动', '场景:联动'),
    ('Q版', '风格:Q版'),
    ('q版', '风格:Q版'),
    ('Pixiv', '来源:Pixiv'),
    ('pixiv', '来源:Pixiv'),
    ('小红书', '来源:小红书'),
)
_EMOTION_HINTS = (
    (('开心', '笑', '乐', '庆祝', '好耶'), '情绪:开心'),
    (('哭', '泪', '难过', '悲'), '情绪:难过'),
    (('生气', '怒', '恼'), '情绪:生气'),
    (('惊', '吓', '震惊'), '情绪:惊讶'),
    (('害羞', '脸红'), '情绪:害羞'),
    (('困', '睡', '倦'), '情绪:困倦'),
    (('问', '?', '？'), '情绪:疑惑'),
    (('爱', '心', '喜欢'), '情绪:喜欢'),
    (('无语', '汗'), '情绪:无语'),
    (('得意', '酷', '帅'), '情绪:得意'),
)


def _palette_emotion(content: bytes) -> str:
    """A coarse, local palette hint; the user can always edit the tag."""
    try:
        with Image.open(io.BytesIO(content)) as opened:
            image = opened.convert('RGB').resize((32, 32))
        red, green, blue = (value / 255 for value in ImageStat.Stat(image).mean)
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        if value < 0.24:
            return '情绪:困倦'
        if saturation < 0.12:
            return '情绪:日常'
        if hue < 0.045 or hue >= 0.95:
            return '情绪:喜欢' if value > 0.62 else '情绪:生气'
        if hue < 0.16:
            return '情绪:开心'
        if hue < 0.42:
            return '情绪:日常'
        if hue < 0.58:
            return '情绪:难过'
        if hue < 0.72:
            return '情绪:害羞'
        if hue < 0.88:
            return '情绪:惊讶'
        return '情绪:喜欢'
    except (OSError, ValueError, Image.DecompressionBombError):
        return '情绪:日常'


def auto_tags(folder: str, filename: str = '', content: bytes | None = None) -> str:
    """Return a small set of deterministic tags for one local image."""
    text = f'{folder} {filename}'
    tags: list[str] = []
    for needle, tag in _ROLE_TAGS + _SCENE_TAGS:
        if needle in text and tag not in tags:
            tags.append(tag)
    emotion = next((tag for needles, tag in _EMOTION_HINTS if any(n in text for n in needles)), '')
    if not emotion and content:
        emotion = _palette_emotion(content)
    tags.append(emotion or '情绪:日常')
    return ', '.join(dict.fromkeys(tags))


def auto_weight(folder: str, filename: str = '') -> float:
    text = f'{folder} {filename}'
    if 'Q版' in text or 'q版' in text or '日常' in text:
        return 1.1
    if '新年' in text or '联动' in text:
        return 1.05
    if '演唱会' in text:
        return 0.95
    return 1.0


def _category_name(folder: str) -> str:
    name = ''.join(ch for ch in str(folder) if ch.isprintable() and ch not in '/\\\r\n').strip()
    return (name or '未分类')[:MAX_CATEGORY_CHARS]


def _category_description(folder: str) -> str:
    return f'本机导入 · {str(folder).strip()[:280]}'


def _relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def _safe_source(path: Path) -> str:
    return _relative(path, ROOT)[:500]


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    reparse = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & reparse
    )


def _read_limited(path: Path, limit: int) -> bytes:
    with path.open('rb') as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError('超过 5 MB')
    return content


def _iter_image_files(folder: Path, base: Path, fail) -> Iterator[tuple[Path, Path]]:
    try:
        with os.scandir(folder) as scanned:
            entries = sorted(scanned, key=lambda entry: entry.name.lower())
    except OSError as exc:
        fail(f'{_relative(folder, base)}: {exc}')
        return
    for entry in entries:
        path = Path(entry.path)
        rel = _relative(path, base)
        try:
            if _is_reparse(path):
                fail(f'{rel}: 跳过链接')
                continue
            if entry.is_dir(follow_symlinks=False):
                try:
                    path.resolve().relative_to(base)
                except (OSError, ValueError):
                    fail(f'{rel}: 超出导入根')
                    continue
                yield from _iter_image_files(path, base, fail)
            elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in _IMAGE_SUFFIXES:
                try:
                    real = path.resolve(strict=True)
                    real.relative_to(base)
                    if not real.is_file():
                        raise ValueError('不是普通文件')
                except (OSError, ValueError) as exc:
                    fail(f'{rel}: {exc}')
                    continue
                yield path, real
        except OSError as exc:
            fail(f'{rel}: {exc}')


def auto_tag_library(library, category_id: str | None = None) -> dict:
    """Recompute tags and the folder-level weight for existing images."""
    categories = library.categories()
    if category_id:
        categories = [category for category in categories if category['id'] == category_id]
    updated = 0
    for category in categories:
        for image in library.images(category['id']):
            try:
                content, _ = library.image_info(image['id'])
                filename = Path(str(image.get('source') or '')).name
                tags = auto_tags(category['name'], filename, content)
                weight = auto_weight(category['name'], filename)
                library.set_metadata(image['id'], tags=tags, weight=weight)
                updated += 1
            except (OSError, ValueError):
                continue
    return {'updated': updated, 'categories': len(categories)}


def import_tree(library=None) -> dict:
    """Import every supported image under the fixed sticker directory.

    Re-running is safe: the storage layer records each image digest and returns
    the existing row for duplicates. Categories are created from immediate
    folder names.
    """
    library = library or meme_logic.Library()
    root_path = DEFAULT_IMPORT_ROOT
    if not root_path.is_dir():
        raise ValueError('本机表情包目录不存在')
    if _is_reparse(root_path):
        raise ValueError('只能导入 data/stickers 目录')
    target = root_path.resolve()
    if target != (DATA_ROOT / 'stickers').resolve():
        raise ValueError('只能导入 data/stickers 目录')
    if not target.is_dir():
        raise ValueError('本机表情包目录不存在')

    existing = {category['name']: category for category in library.categories()}
    imported = skipped = 0
    failed = 0
    more = False
    errors: list[str] = []

    def fail(message):
        nonlocal failed
        failed += 1
        if len(errors) < 20:
            errors.append(str(message)[:240])

    try:
        with os.scandir(target) as scanned:
            folders = sorted(scanned, key=lambda entry: entry.name.lower())
    except OSError as exc:
        raise ValueError('本机表情包目录不存在') from exc

    categories = 0
    for entry in folders:
        if more:
            break
        if entry.name.startswith('.'):
            continue
        folder = Path(entry.path)
        rel_folder = _relative(folder, target)
        if _is_reparse(folder):
            fail(f'{rel_folder}: 跳过链接目录')
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            folder.resolve().relative_to(target)
        except (OSError, ValueError) as exc:
            fail(f'{rel_folder}: {exc}')
            continue

        category_name = _category_name(folder.name)
        files = list(_iter_image_files(folder, target, fail))
        if not files:
            continue
        if imported >= MAX_IMAGES:
            more = True
            break
        category = existing.get(category_name)
        if category is None:
            try:
                category_id = library.put_category(category_name, _category_description(folder.name))
                category = {'id': category_id, 'name': category_name}
                existing[category_name] = category
                categories += 1
            except ValueError as exc:
                category = next(
                    (item for item in library.categories() if item['name'] == category_name),
                    None,
                )
                if category is None:
                    fail(f'{folder.name}: {exc}')
                    continue
                existing[category_name] = category

        for logical, real in files:
            if imported >= MAX_IMAGES:
                more = True
                break
            rel = _relative(logical, target)
            try:
                content = _read_limited(real, meme_logic.MAX_IMAGE_BYTES)
                mime = meme_logic.image_mime(content)
                _image_id, created = library.add_image(
                    category['id'], content,
                    tags=auto_tags(folder.name, logical.name, content),
                    weight=auto_weight(folder.name, logical.name),
                    source=_safe_source(real),
                    mime=mime,
                )
            except (OSError, ValueError) as exc:
                fail(f'{rel}: {exc}')
                continue
            if created:
                imported += 1
            else:
                skipped += 1
        if more:
            break

    return {
        'imported': imported,
        'skipped': skipped,
        'failed': failed,
        'categories': categories,
        'errors': errors[:20],
        'more': more,
    }
