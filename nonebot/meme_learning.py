"""Durable, owner-scoped meme learning jobs and semantic selection."""
from __future__ import annotations

import hashlib
import json
import math
import asyncio
import re
import secrets
from contextlib import contextmanager
from urllib.parse import urlsplit

import meme_logic
from rag_client import embed as _rag_embed

MAX_PENDING = 200


def is_owner(message_type, user_id, owners):
    return message_type == 'private' and str(user_id) in owners


def validate_media_url(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower().rstrip('.')
        domains = ('qq.com', 'qq.com.cn', 'qpic.cn', 'gtimg.cn')
        if (parts.scheme not in ('https', 'http') or parts.username or parts.password
                or parts.port not in (None, 80, 443)
                or not any(host == d or host.endswith('.' + d) for d in domains)):
            raise ValueError('图片地址不受支持')
        return url
    except (TypeError, ValueError) as e:
        raise ValueError('图片地址不受支持') from e


def media_url(segment):
    data = segment.data
    url = data.get('url')
    if url:
        return validate_media_url(url)
    emoji_id = str(data.get('emoji_id') or '')
    if segment.type in ('image', 'mface') and re.fullmatch(r'[a-fA-F0-9]{32}', emoji_id):
        emoji_id = emoji_id.lower()
        return f'https://gxh.vip.qq.com/club/item/parcel/item/{emoji_id[:2]}/{emoji_id}/raw300.gif'
    return ''


async def process(store, job, describe, embed, *, pre_send_error=(), can_defer=None):
    try:
        categories = await asyncio.to_thread(store.library.categories)
        result = await describe(job['content'], categories)
        if not isinstance(result, dict):
            raise ValueError('视觉识别结果无效')
        description = str(result.get('description') or '').strip()
        if not description:
            raise ValueError('视觉识别没有描述')
        vector = await embed(description)
        await asyncio.to_thread(store.complete, job['id'], result.get('category') or '',
                                result.get('category_description') or '', description, vector)
    except Exception as e:
        if isinstance(e, pre_send_error) and (can_defer is None or can_defer()):
            return False
        await asyncio.to_thread(store.fail, job['id'], f'{type(e).__name__}: {e}'[:200])
    return True


async def embed(text):
    return await _rag_embed(text)


def valid_vector(vector):
    if not isinstance(vector, list) or not vector or len(vector) > 4096:
        raise ValueError('向量无效')
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in vector):
        raise ValueError('向量无效')
    result = [float(x) for x in vector]
    if not any(result):
        raise ValueError('向量无效')
    return result


class Store:
    def __init__(self, library):
        self.library = library

    @contextmanager
    def db(self):
        with self.library._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meme_learning_mode (owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS meme_learning_jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, digest TEXT NOT NULL,
                    content BLOB, mime TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS meme_details (
                    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
                    digest TEXT NOT NULL, description TEXT NOT NULL, vector TEXT NOT NULL);
            ''')
            yield db

    def enabled(self, owner):
        with self.db() as db:
            row = db.execute('SELECT enabled FROM meme_learning_mode WHERE owner=?', (str(owner),)).fetchone()
            return bool(row and row[0])

    def set_enabled(self, owner, enabled):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO meme_learning_mode VALUES (?,?)', (str(owner), int(bool(enabled))))

    def enqueue(self, owner, content, admitted=False):
        mime = meme_logic.image_mime(content)
        digest = hashlib.sha256(content).hexdigest()
        self.library.backfill()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            mode = db.execute('SELECT enabled FROM meme_learning_mode WHERE owner=?', (str(owner),)).fetchone()
            if not admitted and (not mode or not mode[0]):
                return 'disabled'
            if (db.execute("SELECT 1 FROM meme_learning_jobs WHERE digest=? AND status IN ('pending','failed','inflight')", (digest,)).fetchone()
                    or db.execute('SELECT 1 FROM image_meta WHERE digest=?', (digest,)).fetchone()
                    or db.execute('SELECT 1 FROM meme_details WHERE digest=?', (digest,)).fetchone()):
                return 'duplicate'
            count = db.execute("SELECT COUNT(*) FROM meme_learning_jobs WHERE status IN ('pending','failed','inflight')").fetchone()[0]
            if count >= MAX_PENDING:
                return 'full'
            db.execute('INSERT INTO meme_learning_jobs (id,owner,digest,content,mime,status) VALUES (?,?,?,?,?,?)',
                       (secrets.token_hex(6), str(owner), digest, content, mime, 'pending'))
        return 'queued'

    def next_job(self):
        with self.db() as db:
            row = db.execute("SELECT id,owner,digest,content,mime FROM meme_learning_jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
        return dict(zip(('id', 'owner', 'digest', 'content', 'mime'), row)) if row else None

    def mark_inflight(self, job_id):
        with self.db() as db:
            return db.execute("UPDATE meme_learning_jobs SET status='inflight',error='' WHERE id=? AND status='pending'", (job_id,)).rowcount == 1

    def recover_inflight(self):
        with self.db() as db:
            return db.execute("UPDATE meme_learning_jobs SET status='failed',error='InterruptedRequest' WHERE status='inflight'").rowcount

    def fail(self, job_id, error):
        with self.db() as db:
            db.execute("UPDATE meme_learning_jobs SET status='failed',error=? WHERE id=? AND status IN ('pending','inflight')", (str(error)[:200], job_id))

    def retry(self, owner):
        with self.db() as db:
            return db.execute("UPDATE meme_learning_jobs SET status='pending',error='' WHERE owner=? AND status='failed'", (str(owner),)).rowcount

    def status(self, owner):
        with self.db() as db:
            rows = db.execute('SELECT status,COUNT(*) FROM meme_learning_jobs WHERE owner=? GROUP BY status', (str(owner),)).fetchall()
        result = {'pending': 0, 'failed': 0, 'done': 0}
        result.update(dict(rows))
        result['pending'] += result.pop('inflight', 0)
        return result

    def failure_reasons(self, owner):
        with self.db() as db:
            rows = db.execute("SELECT DISTINCT error FROM meme_learning_jobs WHERE owner=? AND status='failed' AND error<>'' ORDER BY error LIMIT 5", (str(owner),)).fetchall()
        return [row[0] for row in rows]

    def clear_failed(self, owner):
        with self.db() as db:
            return db.execute("DELETE FROM meme_learning_jobs WHERE owner=? AND status='failed'", (str(owner),)).rowcount

    def complete(self, job_id, name, category_description, description, vector):
        vector = valid_vector(vector)
        name, category_description, description = str(name).strip(), str(category_description).strip(), str(description).strip()
        if not name or len(name) > 40 or any(c in name for c in '/\\\r\n') or not description or len(description) > 1000 or len(category_description) > 300:
            raise ValueError('识别结果无效')
        self.library.backfill()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute("SELECT digest,content,mime FROM meme_learning_jobs WHERE id=? AND status IN ('pending','inflight')", (job_id,)).fetchone()
            if not job:
                raise ValueError('任务不存在')
            row = db.execute('SELECT id FROM categories WHERE name=?', (name,)).fetchone()
            if row:
                category_id = row[0]
            else:
                category_id = secrets.token_hex(6)
                db.execute('INSERT INTO categories VALUES (?,?,?)', (category_id, name, category_description))
            image_id, _created = meme_logic.insert_image(
                db, category_id, job[1], source=''
            )
            if not db.execute('SELECT 1 FROM meme_details WHERE image_id=?', (image_id,)).fetchone():
                db.execute('INSERT INTO meme_details(image_id,digest,description,vector) VALUES (?,?,?,?)',
                           (image_id, job[0], description, json.dumps(vector)))
            db.execute("UPDATE meme_learning_jobs SET status='done',content=NULL,error='' WHERE id=?", (job_id,))

    def choose(self, category_id, vector):
        vector = valid_vector(vector)
        query_norm = math.sqrt(sum(x*x for x in vector))
        with self.db() as db:
            manual_ids = [row[0] for row in db.execute(
                'SELECT i.id FROM images i LEFT JOIN meme_details d ON d.image_id=i.id '
                'WHERE i.category=? AND d.image_id IS NULL',
                (category_id,),
            ).fetchall()]
            rows = db.execute('SELECT i.id,d.vector FROM images i JOIN meme_details d ON d.image_id=i.id WHERE i.category=?', (category_id,)).fetchall()
        if manual_ids and secrets.randbelow(5) == 0:
            chosen = self.library.choose(category_id, image_ids=manual_ids)
            if chosen:
                return chosen
        best = None
        for image_id, encoded in rows:
            try:
                candidate = valid_vector(json.loads(encoded))
                if len(candidate) != len(vector):
                    continue
                score = sum(a*b for a, b in zip(vector, candidate)) / (query_norm * math.sqrt(sum(x*x for x in candidate)))
                if best is None or score > best[0]:
                    best = score, image_id
            except (ValueError, TypeError, OverflowError):
                continue
        return self.library.image_info(best[1]) if best is not None else None

    def has_vectors(self, category_id):
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM images i JOIN meme_details d ON d.image_id=i.id WHERE i.category=? LIMIT 1', (category_id,)).fetchone())
