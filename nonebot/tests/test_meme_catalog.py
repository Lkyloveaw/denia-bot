import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as d
import meme_catalog
import meme_logic


def picture(color):
    data = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(data, format='PNG')
    return data.getvalue()


def large_picture(color='red', size=100):
    data = io.BytesIO()
    Image.new('RGB', (size, size), color).save(data, format='PNG')
    return data.getvalue()


def make_sticker_tree(root):
    folder = root / 'stickers' / '守岸人Q版表情包'
    folder.mkdir(parents=True)
    (folder / '开心.png').write_bytes(picture('yellow'))
    (folder / '开心副本.png').write_bytes(picture('yellow'))
    (folder / '难过.png').write_bytes(picture('blue'))


def test_local_import_creates_metadata_and_skips_duplicates(tmp_path, monkeypatch):
    make_sticker_tree(tmp_path)
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    library = meme_logic.Library(tmp_path / 'library')

    first = meme_catalog.import_tree(library)

    assert first['imported'] == 2
    assert first['skipped'] == 1
    assert first['categories'] == 1
    categories = library.categories()
    assert categories[0]['name'] == '守岸人Q版表情包'
    images = library.images(categories[0]['id'])
    assert len(images) == 2
    assert all('角色:守岸人' in image['tags'] for image in images)
    assert all('风格:Q版' in image['tags'] for image in images)
    assert all(image['weight'] == 1.1 for image in images)
    assert all(image['source'].endswith('.png') for image in images)

    second = meme_catalog.import_tree(library)
    assert second['imported'] == 0
    assert second['skipped'] == 3
    assert first['failed'] == 0 and first['more'] is False


def test_import_rejects_root_outside_the_fixed_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    outside = tmp_path / 'outside' / 'stickers'
    outside.mkdir(parents=True)
    (outside / 'x.png').write_bytes(picture('red'))
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', outside)
    with pytest.raises(ValueError, match='data/stickers'):
        meme_catalog.import_tree(meme_logic.Library(tmp_path / 'library'))


def test_import_skips_links_and_keeps_failure_count(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    folder = tmp_path / 'stickers' / '开心'
    folder.mkdir(parents=True)
    (folder / 'ok.png').write_bytes(picture('red'))
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'evil.png').write_bytes(picture('blue'))
    try:
        os.symlink(outside, folder / 'linked-dir', target_is_directory=True)
        os.symlink(outside / 'evil.png', folder / 'linked-file.png')
    except OSError as exc:
        pytest.skip(f'cannot create symlink: {exc}')
    library = meme_logic.Library(tmp_path / 'library')
    result = meme_catalog.import_tree(library)
    assert result['imported'] == 1
    assert result['failed'] >= 2
    images = library.images(library.categories()[0]['id'])
    assert [image for image in images if image['id']] and len(images) == 1
    assert any('linked' in error for error in result['errors'])


def test_import_reads_bounded_and_accepts_exact_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    monkeypatch.setattr(meme_logic, 'MAX_IMAGE_BYTES', 1000)
    folder = tmp_path / 'stickers' / '开心'
    folder.mkdir(parents=True)
    exact = picture('red')
    (folder / 'exact.png').write_bytes(exact + b'\0' * (1000 - len(exact)))
    library = meme_logic.Library(tmp_path / 'library')
    with patch.object(Path, 'read_bytes', side_effect=AssertionError('read_bytes used')):
        first = meme_catalog.import_tree(library)
    assert first['imported'] == 1
    (folder / 'over.png').write_bytes(exact + b'\0' * (1001 - len(exact)))
    second = meme_catalog.import_tree(library)
    assert second['imported'] == 0
    assert second['failed'] == 1
    assert '超过 5 MB' in second['errors'][0]


def test_import_continues_after_pillow_validation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    folder = tmp_path / 'stickers' / '素材'
    folder.mkdir(parents=True)
    (folder / 'bad.png').write_bytes(large_picture())
    (folder / 'ok.png').write_bytes(picture('blue'))
    library = meme_logic.Library(tmp_path / 'library')
    with patch.object(meme_logic.Image, 'MAX_IMAGE_PIXELS', 100):
        result = meme_catalog.import_tree(library)
    assert result['imported'] == 1
    assert result['failed'] == 1
    assert 'bad.png' in result['errors'][0]


def test_import_limit_counts_only_new_files(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    monkeypatch.setattr(meme_catalog, 'MAX_IMAGES', 1)
    folder = tmp_path / 'stickers' / '开心'
    folder.mkdir(parents=True)
    (folder / 'a.png').write_bytes(picture('red'))
    (folder / 'b.png').write_bytes(picture('blue'))
    library = meme_logic.Library(tmp_path / 'library')
    first = meme_catalog.import_tree(library)
    second = meme_catalog.import_tree(library)
    third = meme_catalog.import_tree(library)
    assert (first['imported'], first['more']) == (1, True)
    assert (second['imported'], second['more']) == (1, False)
    assert (third['imported'], third['skipped'], third['more']) == (0, 2, False)


def test_import_failure_details_are_bounded_but_total_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    folder = tmp_path / 'stickers' / '坏图'
    folder.mkdir(parents=True)
    for index in range(21):
        (folder / f'{index}.png').write_bytes(b'not an image')
    result = meme_catalog.import_tree(meme_logic.Library(tmp_path / 'library'))
    assert result['imported'] == 0
    assert result['failed'] == 21
    assert len(result['errors']) == 20


def test_concurrent_import_adds_the_image_once(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    folder = tmp_path / 'stickers' / '开心'
    folder.mkdir(parents=True)
    (folder / 'only.png').write_bytes(picture('red'))
    root = tmp_path / 'library'
    def run():
        return meme_catalog.import_tree(meme_logic.Library(root))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(run), pool.submit(run)]]
    assert sum(result['imported'] for result in results) == 1
    assert sum(result['skipped'] for result in results) == 1
    assert all(result['failed'] == 0 for result in results)
    assert meme_logic.Library(root).categories()[0]['count'] == 1


def test_auto_tag_library_updates_existing_images(tmp_path, monkeypatch):
    make_sticker_tree(tmp_path)
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    library = meme_logic.Library(tmp_path / 'library')
    category = library.put_category('日常', '普通聊天')
    image, _ = library.add_image(category, picture('red'))

    result = meme_catalog.auto_tag_library(library, category)

    assert result['updated'] == 1
    saved = library.images(category)[0]
    assert saved['id'] == image
    assert '情绪:' in saved['tags']
    assert saved['weight'] in (1.0, 1.05, 1.1, 0.95)


def test_choose_filters_by_tag_and_uses_metadata_weights(tmp_path):
    library = meme_logic.Library(tmp_path)
    category = library.put_category('开心', '庆祝')
    happy, _ = library.add_image(category, picture('yellow'), tags='情绪:开心', weight=0.2)
    library.add_image(category, picture('blue'), tags='情绪:难过', weight=5.0)

    seen = {}
    def choose(rows, weights, k):
        seen['ids'] = [row[0] for row in rows]
        seen['weights'] = list(weights)
        return [rows[0]]

    with patch.object(meme_logic.random, 'choices', choose):
        image, mime = library.choose(category, '情绪:开心')

    assert seen['ids'] == [happy]
    assert seen['weights'] == [0.2]
    assert image == picture('yellow')
    assert mime == 'image/png'


def test_metadata_rejects_bad_tags_and_weight(tmp_path):
    library = meme_logic.Library(tmp_path)
    category = library.put_category('开心', '庆祝')
    image, _ = library.add_image(category, picture('green'))

    with pytest.raises(ValueError):
        library.set_metadata(image, tags='x' * 25)
    with pytest.raises(ValueError):
        library.set_metadata(image, weight=0)
    with pytest.raises(ValueError):
        library.set_metadata('not-an-id', tags='情绪:开心')


def test_dashboard_bulk_import_and_metadata_api(tmp_path, monkeypatch):
    make_sticker_tree(tmp_path)
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    library = meme_logic.Library(tmp_path / 'library')
    with patch.object(d, 'AUTH_FILE', tmp_path / 'auth'), \
            patch.object(d, 'meme_library', library), \
            patch.object(d, 'session_store_path', return_value=tmp_path / 'sessions'):
        d.set_password('a-strong-test-password')
        with TestClient(d.app) as client:
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
            imported = client.post('/api/memes/import', json={}, headers=headers)
            assert imported.status_code == 200, imported.text
            assert imported.json()['imported'] == 2
            categories = client.get('/api/memes').json()['categories']
            image = categories[0]['images'][0]
            assert '角色:守岸人' in image['tags']
            saved = client.post('/api/memes/metadata', json={
                'id': image['id'], 'tags': '角色:守岸人, 情绪:开心', 'weight': 1.4,
            }, headers=headers)
            assert saved.status_code == 200
            assert saved.json()['weight'] == 1.4
            assert client.post('/api/memes/metadata', json={'id': image['id']}, headers=headers).status_code == 400


def test_upload_preserves_explicit_empty_tags_and_weight(tmp_path):
    library = meme_logic.Library(tmp_path / 'library')
    category = library.put_category('Q版', '庆祝')
    with patch.object(d, 'AUTH_FILE', tmp_path / 'auth'), \
            patch.object(d, 'meme_library', library), \
            patch.object(d, 'session_store_path', return_value=tmp_path / 'sessions'):
        d.set_password('a-strong-test-password')
        with TestClient(d.app) as client:
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            headers = {
                'Origin': 'http://testserver',
                'X-CSRF-Token': login.json()['csrf'],
                'Content-Type': 'application/octet-stream',
            }
            response = client.post(
                f'/api/memes/image?category={category}&tags=&weight=1.0',
                content=picture('red'), headers=headers,
            )
            assert response.status_code == 200, response.text
            assert response.json()['created'] is True
            saved = library.images(category)[0]
            assert saved['tags'] == ''
            assert saved['weight'] == 1.0


def test_upload_rejects_pillow_validation_failure_before_tagging(tmp_path):
    library = meme_logic.Library(tmp_path / 'library')
    category = library.put_category('Q版', '庆祝')
    with patch.object(d, 'AUTH_FILE', tmp_path / 'auth'), \
            patch.object(d, 'meme_library', library), \
            patch.object(d, 'session_store_path', return_value=tmp_path / 'sessions'):
        d.set_password('a-strong-test-password')
        with TestClient(d.app, raise_server_exceptions=False) as client:
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            headers = {
                'Origin': 'http://testserver',
                'X-CSRF-Token': login.json()['csrf'],
                'Content-Type': 'application/octet-stream',
            }
            content = large_picture()
            with patch.object(meme_logic.Image, 'MAX_IMAGE_PIXELS', 100):
                for suffix in ('', '&tags=foo&weight=1.0'):
                    response = client.post(
                        f'/api/memes/image?category={category}{suffix}',
                        content=content, headers=headers,
                    )
                    assert response.status_code == 400, (suffix, response.text)


def test_upload_without_tags_auto_tags_and_skips_duplicate(tmp_path):
    library = meme_logic.Library(tmp_path / 'library')
    category = library.put_category('Q版', '庆祝')
    with patch.object(d, 'AUTH_FILE', tmp_path / 'auth'), \
            patch.object(d, 'meme_library', library), \
            patch.object(d, 'session_store_path', return_value=tmp_path / 'sessions'):
        d.set_password('a-strong-test-password')
        with TestClient(d.app) as client:
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            headers = {
                'Origin': 'http://testserver',
                'X-CSRF-Token': login.json()['csrf'],
                'Content-Type': 'application/octet-stream',
            }
            content = picture('green')
            first = client.post(f'/api/memes/image?category={category}', content=content, headers=headers)
            assert first.status_code == 200, first.text
            assert first.json()['created'] is True
            saved = library.images(category)[0]
            assert '风格:Q版' in saved['tags']
            second = client.post(f'/api/memes/image?category={category}', content=content, headers=headers)
            assert second.status_code == 200, second.text
            assert second.json()['created'] is False
            assert library.categories()[0]['count'] == 1


def test_import_rejects_linked_root(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    target = tmp_path / 'outside'
    target.mkdir()
    link = data / 'stickers'
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'cannot create symlink: {exc}')
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', data)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', link)
    with pytest.raises(ValueError, match='data/stickers'):
        meme_catalog.import_tree(meme_logic.Library(tmp_path / 'library'))


def test_import_cap_does_not_create_the_next_category(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    monkeypatch.setattr(meme_catalog, 'MAX_IMAGES', 1)
    for name, color in (('a', 'red'), ('b', 'blue')):
        folder = tmp_path / 'stickers' / name
        folder.mkdir(parents=True)
        (folder / 'x.png').write_bytes(picture(color))
    library = meme_logic.Library(tmp_path / 'library')
    first = meme_catalog.import_tree(library)
    assert (first['imported'], first['more']) == (1, True)
    assert [category['name'] for category in library.categories()] == ['a']
