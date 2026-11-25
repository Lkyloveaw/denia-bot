import io
import base64
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meme_logic as m


def picture(fmt='PNG'):
    data = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(data, format=fmt)
    return data.getvalue()


def test_library_upload_scope_and_delete(tmp_path):
    lib = m.Library(tmp_path)
    category = lib.put_category('开心', '庆祝、快乐')
    image, _ = lib.add_image(category, picture())
    assert lib.categories()[0]['count'] == 1
    assert lib.image(image) == picture()
    assert lib.enabled('group:123')
    lib.set_rules(False, ['group:123'])
    assert not lib.enabled('group:123')
    assert not lib.enabled('private:123')
    lib.set_rules(True, ['group:123'])
    assert not lib.enabled('group:123')
    assert lib.enabled('private:123')
    lib.delete_image(image)
    assert lib.categories()[0]['count'] == 0


def test_add_image_deduplicates_and_returns_created(tmp_path):
    lib = m.Library(tmp_path)
    category = lib.put_category('开心', '庆祝')
    first, created = lib.add_image(category, picture())
    second, duplicate = lib.add_image(category, picture())
    assert created is True
    assert duplicate is False
    assert second == first
    assert lib.categories()[0]['count'] == 1
    with lib._connect() as db:
        digest = db.execute('SELECT digest FROM image_meta WHERE image_id=?', (first,)).fetchone()[0]
    assert digest == hashlib.sha256(picture()).hexdigest()


def test_concurrent_add_image_creates_one_row(tmp_path):
    category = m.Library(tmp_path).put_category('开心', '庆祝')
    def add():
        return m.Library(tmp_path).add_image(category, picture())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(add), pool.submit(add)]]
    assert sum(1 for _, created in results if created) == 1
    assert m.Library(tmp_path).categories()[0]['count'] == 1


def test_backfill_digests_preserves_metadata(tmp_path):
    lib = m.Library(tmp_path)
    category = lib.put_category('开心', '庆祝')
    content = picture()
    image_id = 'a' * 12
    with lib._connect() as db:
        db.execute('INSERT INTO images(id,category,content,mime) VALUES (?,?,?,?)',
                   (image_id, category, content, 'image/png'))
        db.execute('INSERT INTO image_meta(image_id,tags,weight,digest,source) VALUES (?,?,?,?,?)',
                   (image_id, '情绪:开心', 2.5, '', '旧来源'))
    with lib._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        assert m.backfill_digests(db) == 1
    with lib._connect() as db:
        row = db.execute('SELECT digest,tags,weight,source FROM image_meta WHERE image_id=?',
                         (image_id,)).fetchone()
    assert row[0] == hashlib.sha256(content).hexdigest()
    assert tuple(row[1:]) == ('情绪:开心', 2.5, '旧来源')
    with lib._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        assert m.backfill_digests(db) == 0


def test_choose_can_limit_to_image_ids(tmp_path):
    lib = m.Library(tmp_path)
    category = lib.put_category('开心', '庆祝')
    first, _ = lib.add_image(category, picture())
    lib.add_image(category, picture('JPEG'))
    seen = {}
    def choose(rows, weights, k):
        seen['ids'] = [row[0] for row in rows]
        seen['weights'] = list(weights)
        return [rows[0]]
    with patch.object(m.random, 'choices', choose):
        image, mime = lib.choose(category, image_ids={first})
    assert seen['ids'] == [first]
    assert seen['weights'] == [1.0]
    assert image == picture()
    assert mime == 'image/png'


def test_reject_invalid_names_and_uploads(tmp_path):
    lib = m.Library(tmp_path)
    for name in ('', '../x', 'a' * 41):
        with pytest.raises(ValueError):
            lib.put_category(name, '描述')
    category = lib.put_category('开心', '描述')
    with pytest.raises(ValueError):
        lib.add_image(category, b'not an image')
    corrupt = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII=')
    with pytest.raises(ValueError):
        lib.add_image(category, corrupt)
    with pytest.raises(ValueError):
        lib.add_image('../outside', picture())


def test_stream_parser_handles_split_markers_and_unknown_ids():
    parser = m.MarkerParser({'abc123'})
    chunks = ['太好', '了！[[me', 'me:abc', '123]] 今', '天真棒', '[[meme:wrong]]']
    result = ''.join(parser.feed(chunk) for chunk in chunks) + parser.finish()
    assert result == '太好了！ 今天真棒'
    assert parser.selected == 'abc123'


def test_stream_parser_keeps_plain_text_and_hides_incomplete_marker():
    parser = m.MarkerParser({'abc123'})
    assert parser.feed('正常[文字') + parser.finish() == '正常[文字'
    parser = m.MarkerParser({'abc123'})
    assert parser.feed('回复[[meme:abc') + parser.finish() == '回复'


def test_connections_close_after_read_and_write(tmp_path):
    lib = m.Library(tmp_path)
    lib.put_category('开心', '庆祝')
    lib.categories()
    db = tmp_path / 'library.sqlite3'
    db.rename(tmp_path / 'renamed.sqlite3')


def test_backfill_library_fills_legacy_images_without_outer_transaction(tmp_path):
    lib = m.Library(tmp_path)
    category = lib.put_category('开心', '庆祝')
    content = picture()
    image_id = 'b' * 12
    with lib._connect() as db:
        db.execute('INSERT INTO images(id,category,content,mime) VALUES (?,?,?,?)',
                   (image_id, category, content, 'image/png'))
        db.execute('INSERT INTO image_meta(image_id,tags,weight,digest,source) VALUES (?,?,?,?,?)',
                   (image_id, '旧标签', 2.5, '', '旧来源'))
    assert m.backfill_library(tmp_path) == 1
    with lib._connect() as db:
        row = db.execute('SELECT digest,tags,weight,source FROM image_meta WHERE image_id=?',
                         (image_id,)).fetchone()
    assert row[0] == hashlib.sha256(content).hexdigest()
    assert tuple(row[1:]) == ('旧标签', 2.5, '旧来源')
    assert m.backfill_library(tmp_path) == 0


def test_metadata_accepts_twenty_tags_and_rejects_twenty_one(tmp_path):
    lib = m.Library(tmp_path)
    category = lib.put_category('开心', '庆祝')
    image, _ = lib.add_image(category, picture())
    tags = ', '.join(f'tag{i}' for i in range(20))
    assert lib.set_metadata(image, tags=tags)['tags'] == tags
    with pytest.raises(ValueError, match='最多 20'):
        lib.set_metadata(image, tags=tags + ', tag20')
