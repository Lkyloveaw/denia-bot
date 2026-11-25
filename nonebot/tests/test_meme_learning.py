import io
import math
import sys
import asyncio
import tracemalloc
import json
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meme_learning as learning
import meme_logic


def image(color):
    out = io.BytesIO()
    Image.new('RGB', (3, 3), color).save(out, format='PNG')
    return out.getvalue()


def test_learning_queue_persists_and_deduplicates(tmp_path):
    store = learning.Store(meme_logic.Library(tmp_path))
    assert not store.enabled('42')
    store.set_enabled('42', True)
    assert learning.Store(meme_logic.Library(tmp_path)).enabled('42')
    assert store.enqueue('42', image('red')) == 'queued'
    assert store.enqueue('42', image('red')) == 'duplicate'
    assert store.status('42')['pending'] == 1
    store.set_enabled('42', False)
    assert not store.enabled('42')
    assert store.enqueue('42', image('blue')) == 'disabled'
    assert store.next_job()['content'] == image('red')


def test_failure_retry_and_semantic_ranking(tmp_path):
    lib = meme_logic.Library(tmp_path)
    store = learning.Store(lib)
    store.set_enabled('42', True)
    store.enqueue('42', image('red'))
    red = store.next_job()
    store.fail(red['id'], 'vision error')
    assert store.status('42')['failed'] == 1
    assert store.failure_reasons('42') == ['vision error']
    assert store.retry('42') == 1
    store.complete(red['id'], '开心', '庆祝开心', '大笑庆祝', [1.0, 0.0])
    store.enqueue('42', image('blue'))
    blue = store.next_job()
    store.complete(blue['id'], '开心', '庆祝开心', '轻松打招呼', [0.0, 1.0])
    categories = lib.categories()
    assert len(categories) == 1 and categories[0]['count'] == 2
    assert store.choose(categories[0]['id'], [0.95, 0.05])[0] == image('red')
    assert store.choose(categories[0]['id'], [0.05, 0.95])[0] == image('blue')
    assert store.status('42')['done'] == 2


def test_owner_can_clear_failed_without_deleting_pending(tmp_path):
    store = learning.Store(meme_logic.Library(tmp_path))
    store.set_enabled('42', True)
    store.enqueue('42', image('red'))
    store.fail(store.next_job()['id'], 'TimeoutError')
    store.enqueue('42', image('blue'))
    assert store.clear_failed('other') == 0
    assert store.clear_failed('42') == 1
    assert store.status('42')['pending'] == 1


def test_invalid_vector_or_image_does_not_lose_queue(tmp_path):
    store = learning.Store(meme_logic.Library(tmp_path))
    store.set_enabled('42', True)
    with pytest.raises(ValueError):
        store.enqueue('42', b'bad')
    store.enqueue('42', image('red'))
    job = store.next_job()
    for vector in ([], [0, 0], [math.nan, 1]):
        with pytest.raises(ValueError):
            store.complete(job['id'], '开心', '场景', '表情描述', vector)
    assert store.next_job()['content'] == image('red')


def test_owner_permission_is_strict_private_and_explicit():
    assert learning.is_owner('private', '42', {'42'})
    assert not learning.is_owner('group', '42', {'42'})
    assert not learning.is_owner('private', '43', {'42'})
    assert not learning.is_owner('private', '42', set())


def test_worker_classifies_embeds_and_retains_failures(tmp_path):
    async def run():
        store = learning.Store(meme_logic.Library(tmp_path))
        store.set_enabled('42', True)
        store.enqueue('42', image('red'))
        job = store.next_job()
        async def describe(data, categories):
            assert data in (image('red'), image('blue'))
            return {'category': '开心', 'category_description': '开心庆祝', 'description': '欢呼庆祝'}
        async def embed(text):
            assert '欢呼庆祝' in text
            return [1, 0]
        await learning.process(store, job, describe, embed)
        assert store.status('42')['done'] == 1
        store.enqueue('42', image('blue'))
        async def fail(_text):
            raise TimeoutError('offline')
        await learning.process(store, store.next_job(), describe, fail)
        assert store.status('42')['failed'] == 1
        assert store.retry('42') == 1
        assert store.next_job()['content'] == image('blue')
    asyncio.run(run())


def test_qq_image_url_validation_and_mface_fallback():
    qq = SimpleNamespace(type='image', data={'url': 'https://gchat.qpic.cn/test'})
    assert learning.media_url(qq) == 'https://gchat.qpic.cn/test'
    mface = SimpleNamespace(type='mface', data={'emoji_id': 'aa' * 16})
    assert learning.media_url(mface).startswith('https://gxh.vip.qq.com/')
    mface_image = SimpleNamespace(type='image', data={'emoji_id': 'aa' * 16})
    assert learning.media_url(mface_image) == learning.media_url(mface)
    bad = SimpleNamespace(type='image', data={'url': 'http://127.0.0.1/private'})
    with pytest.raises(ValueError):
        learning.media_url(bad)


def test_semantic_selection_does_not_load_entire_image_library(tmp_path):
    lib = meme_logic.Library(tmp_path)
    store = learning.Store(lib)
    category = lib.put_category('开心', '庆祝')
    payload = b'x' * (1024 * 1024)
    with store.db() as db:
        for i in range(20):
            image_id = f'{i:012x}'
            db.execute('INSERT INTO images VALUES (?,?,?,?)', (image_id, category, payload, 'image/png'))
            db.execute('INSERT INTO meme_details VALUES (?,?,?,?)', (image_id, image_id, '描述', json.dumps([1, 0])))
    tracemalloc.start()
    try:
        assert store.choose(category, [1, 0])[0] == payload
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024


def test_manual_images_remain_eligible_after_learning(tmp_path):
    lib = meme_logic.Library(tmp_path)
    store = learning.Store(lib)
    category = lib.put_category('开心', '庆祝')
    lib.add_image(category, image('blue'))
    store.set_enabled('42', True)
    store.enqueue('42', image('red'))
    store.complete(store.next_job()['id'], '开心', '庆祝', '庆祝', [1, 0])
    with patch.object(learning.secrets, 'randbelow', return_value=0):
        assert store.choose(category, [1, 0])[0] == image('blue')
    with patch.object(learning.secrets, 'randbelow', return_value=1):
        assert store.choose(category, [1, 0])[0] == image('red')


def test_manual_branch_uses_library_weights(tmp_path):
    lib = meme_logic.Library(tmp_path)
    store = learning.Store(lib)
    category = lib.put_category('开心', '庆祝')
    blue, _ = lib.add_image(category, image('blue'), weight=0.2)
    green, _ = lib.add_image(category, image('green'), weight=5.0)
    store.set_enabled('42', True)
    store.enqueue('42', image('red'))
    store.complete(store.next_job()['id'], '开心', '庆祝', '红色', [1, 0])
    seen = {}
    def choose(rows, weights, k):
        seen['ids'] = [row[0] for row in rows]
        seen['weights'] = {row[0]: weight for row, weight in zip(rows, weights)}
        return [rows[0]]
    with patch.object(learning.secrets, 'randbelow', return_value=0), \
         patch.object(meme_logic.random, 'choices', choose):
        chosen = store.choose(category, [1, 0])
    assert set(seen['ids']) == {blue, green}
    assert seen['weights'][blue] == 0.2
    assert seen['weights'][green] == 5.0
    assert chosen[0] in (image('blue'), image('green'))


def test_complete_reuses_uploaded_image_and_attaches_details(tmp_path):
    lib = meme_logic.Library(tmp_path)
    store = learning.Store(lib)
    category = lib.put_category('开心', '庆祝')
    store.set_enabled('42', True)
    store.enqueue('42', image('red'))
    job = store.next_job()
    image_id, created = lib.add_image(category, image('red'))
    assert created is True
    store.complete(job['id'], '开心', '庆祝', '红色', [1, 0])
    assert lib.categories()[0]['count'] == 1
    with store.db() as db:
        assert db.execute('SELECT image_id FROM meme_details').fetchone()[0] == image_id
        assert db.execute("SELECT status FROM meme_learning_jobs WHERE id=?", (job['id'],)).fetchone()[0] == 'done'
    assert store.enqueue('42', image('red')) == 'duplicate'
