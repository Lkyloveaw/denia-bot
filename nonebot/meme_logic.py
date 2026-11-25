"""Local meme library and incremental model marker parsing."""
from __future__ import annotations

import io
import hashlib
import json
import random
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, UnidentifiedImageError

DEFAULT_ROOT = Path(__file__).resolve().parent / 'data' / 'memes'
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_ID = re.compile(r'^[a-f0-9]{12}$')
_SCOPE = re.compile(r'^(?:group|private):[0-9]{1,20}$')
_TAG_SPLIT = re.compile(r'[,，、\n]+')
_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS categories (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, category TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE, content BLOB NOT NULL, mime TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS image_meta (
        image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
        tags TEXT NOT NULL DEFAULT '', weight REAL NOT NULL DEFAULT 1.0,
        digest TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS image_meta_digest ON image_meta(digest);
'''


def _clean_tags(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('标签格式无效')
    tags = []
    for raw in _TAG_SPLIT.split(value):
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > 24 or any(ord(ch) < 32 for ch in tag):
            raise ValueError('单个标签须在 24 个字符以内')
        if tag not in tags:
            tags.append(tag)
            if len(tags) > 20:
                raise ValueError('标签最多 20 个')
    return ', '.join(tags)


def _clean_weight(value):
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('权重须为 0.1 到 5 的数字') from exc
    if not 0.1 <= weight <= 5:
        raise ValueError('权重须在 0.1 到 5 之间')
    return round(weight, 2)


def image_mime(content):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_IMAGE_BYTES:
        raise ValueError('图片大小须在 5 MB 以内')
    try:
        with Image.open(io.BytesIO(content)) as image:
            formats = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp'}
            mime = formats.get(image.format)
            if not mime or max(image.size) > 4096 or image.width * image.height > 16_000_000:
                raise ValueError('仅支持尺寸不超过 4096 的 PNG、JPEG、GIF、WebP 图片')
            image.verify()
            return mime
    except (UnidentifiedImageError, OSError, SyntaxError, EOFError, Image.DecompressionBombError) as e:
        raise ValueError('图片格式无效') from e


def _backfill_batch(db, size):
    rows = db.execute(
        '''SELECT i.id, i.content FROM images i
           LEFT JOIN image_meta m ON m.image_id = i.id
           WHERE m.image_id IS NULL OR m.digest = ''
           ORDER BY i.rowid LIMIT ?''',
        (max(1, int(size)),),
    ).fetchall()
    for image_id, content in rows:
        digest = hashlib.sha256(bytes(content)).hexdigest()
        db.execute('INSERT OR IGNORE INTO image_meta(image_id,digest) VALUES (?,?)',
                   (image_id, digest))
        db.execute("UPDATE image_meta SET digest=? WHERE image_id=? AND digest=''",
                   (digest, image_id))
    return len(rows)


def backfill_digests(db, batch=100):
    """Fill missing image_meta.digest values in small batches; caller owns the transaction."""
    total = 0
    while True:
        count = _backfill_batch(db, batch)
        if not count:
            return total
        total += count


def backfill_library(root, batch=100):
    """Backfill an existing library with one short write transaction per batch."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'library.sqlite3', timeout=10)
    db.execute('PRAGMA foreign_keys=ON')
    try:
        db.executescript(_SCHEMA)
        db.commit()
        total = 0
        while True:
            db.execute('BEGIN IMMEDIATE')
            count = _backfill_batch(db, batch)
            if not count:
                db.rollback()
                return total
            db.commit()
            total += count
    finally:
        db.close()


def insert_image(db, category_id, content, tags='', weight=1.0, source='', mime=None):
    """Insert one image inside the caller's write transaction; return (id, created).

    ``mime`` is only for callers that already ran ``image_mime`` on the bytes.
    """
    if not _ID.fullmatch(str(category_id)):
        raise ValueError('分类不存在')
    content = bytes(content)
    mime = mime or image_mime(content)
    tags = _clean_tags(tags)
    weight = _clean_weight(weight)
    source = str(source or '').strip()
    if len(source) > 500 or any(ord(ch) < 32 for ch in source):
        raise ValueError('图片来源无效')
    digest = hashlib.sha256(content).hexdigest()
    missing = db.execute("SELECT 1 FROM image_meta WHERE digest='' LIMIT 1").fetchone()
    if not missing:
        missing = db.execute(
            'SELECT 1 FROM images i LEFT JOIN image_meta m ON m.image_id=i.id'
            ' WHERE m.image_id IS NULL LIMIT 1'
        ).fetchone()
    if missing:
        backfill_digests(db)
    row = db.execute('SELECT image_id FROM image_meta WHERE digest=? ORDER BY image_id LIMIT 1',
                     (digest,)).fetchone()
    if row:
        return row[0], False
    image_id = secrets.token_hex(6)
    try:
        db.execute('INSERT INTO images(id,category,content,mime) VALUES (?,?,?,?)',
                   (image_id, category_id, content, mime))
        db.execute(
            'INSERT INTO image_meta(image_id,tags,weight,digest,source) VALUES (?,?,?,?,?)',
            (image_id, tags, weight, digest, source),
        )
    except sqlite3.IntegrityError as e:
        raise ValueError('分类不存在') from e
    return image_id, True


class Library:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)
        self._backfilled = False

    def backfill(self):
        if self._backfilled:
            return 0
        total = backfill_library(self.root)
        self._backfilled = True
        return total

    @contextmanager
    def _connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'library.sqlite3', timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.executescript(_SCHEMA)
            with db:
                yield db
        finally:
            db.close()

    def categories(self):
        with self._connect() as db:
            rows = db.execute('SELECT c.id,c.name,c.description,COUNT(i.id) FROM categories c LEFT JOIN images i ON c.id=i.category GROUP BY c.id ORDER BY c.name').fetchall()
        return [dict(id=a, name=b, description=c, count=d) for a, b, c, d in rows]

    def put_category(self, name, description, category_id=None):
        name, description = str(name).strip(), str(description).strip()
        if not name or len(name) > 40 or any(c in name for c in '/\\\r\n') or len(description) > 300:
            raise ValueError('分类名称或描述无效')
        if category_id is not None and not _ID.fullmatch(str(category_id)):
            raise ValueError('分类不存在')
        with self._connect() as db:
            try:
                if category_id:
                    cur = db.execute('UPDATE categories SET name=?,description=? WHERE id=?', (name, description, category_id))
                    if not cur.rowcount:
                        raise ValueError('分类不存在')
                else:
                    category_id = secrets.token_hex(6)
                    db.execute('INSERT INTO categories VALUES (?,?,?)', (category_id, name, description))
            except sqlite3.IntegrityError as e:
                raise ValueError('分类名称已存在') from e
        return category_id

    def delete_category(self, category_id):
        if not _ID.fullmatch(str(category_id)):
            raise ValueError('分类不存在')
        with self._connect() as db:
            if not db.execute('DELETE FROM categories WHERE id=?', (category_id,)).rowcount:
                raise ValueError('分类不存在')

    def add_image(self, category_id, content, tags='', weight=1.0, source='', mime=None):
        self.backfill()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return insert_image(db, category_id, content, tags, weight, source, mime)

    def images(self, category_id):
        with self._connect() as db:
            rows = db.execute(
                '''SELECT i.id,i.mime,COALESCE(m.tags,''),COALESCE(m.weight,1.0),COALESCE(m.source,'')
                   FROM images i LEFT JOIN image_meta m ON m.image_id=i.id
                   WHERE i.category=? ORDER BY i.rowid DESC''',
                (category_id,),
            ).fetchall()
        return [dict(id=i, mime=mime, tags=tags, weight=weight, source=source)
                for i, mime, tags, weight, source in rows]

    def set_metadata(self, image_id, tags=None, weight=None):
        if not _ID.fullmatch(str(image_id)):
            raise ValueError('图片不存在')
        if tags is None and weight is None:
            raise ValueError('没有需要保存的标签或权重')
        assignments, values = [], []
        if tags is not None:
            assignments.append('tags=?')
            values.append(_clean_tags(tags))
        if weight is not None:
            assignments.append('weight=?')
            values.append(_clean_weight(weight))
        values.append(str(image_id))
        with self._connect() as db:
            if not db.execute('SELECT 1 FROM images WHERE id=?', (image_id,)).fetchone():
                raise ValueError('图片不存在')
            db.execute('INSERT INTO image_meta(image_id) VALUES (?) ON CONFLICT(image_id) DO NOTHING', (image_id,))
            db.execute(f"UPDATE image_meta SET {','.join(assignments)} WHERE image_id=?", values)
            row = db.execute(
                'SELECT image_id,tags,weight,source FROM image_meta WHERE image_id=?', (image_id,)
            ).fetchone()
        return dict(id=row[0], tags=row[1], weight=row[2], source=row[3])

    def image_info(self, image_id):
        if not _ID.fullmatch(str(image_id)):
            raise ValueError('图片不存在')
        with self._connect() as db:
            row = db.execute('SELECT content,mime FROM images WHERE id=?', (image_id,)).fetchone()
        if row is None:
            raise ValueError('图片不存在')
        return bytes(row[0]), row[1]

    def image(self, image_id):
        return self.image_info(image_id)[0]

    def choose(self, category_id, tags=None, image_ids=None):
        with self._connect() as db:
            rows = db.execute(
                '''SELECT i.id,COALESCE(m.tags,''),COALESCE(m.weight,1.0)
                   FROM images i LEFT JOIN image_meta m ON m.image_id=i.id
                   WHERE i.category=?''',
                (category_id,),
            ).fetchall()
        if image_ids is not None:
            allowed = {str(value) for value in image_ids}
            rows = [row for row in rows if row[0] in allowed]
        if not rows:
            return None
        wanted = [tag.strip().lower() for tag in _TAG_SPLIT.split(str(tags or '')) if tag.strip()]
        if wanted:
            matched = [row for row in rows if any(
                tag.strip().lower() in wanted for tag in str(row[1] or '').split(',')
            )]
            rows = matched or rows
        try:
            weights = [max(0.1, float(row[2] or 1.0)) for row in rows]
            chosen = random.choices(rows, weights=weights, k=1)[0]
        except (TypeError, ValueError):
            chosen = random.choice(rows)
        return self.image_info(chosen[0])

    def delete_image(self, image_id):
        if not _ID.fullmatch(str(image_id)):
            raise ValueError('图片不存在')
        with self._connect() as db:
            if not db.execute('DELETE FROM images WHERE id=?', (image_id,)).rowcount:
                raise ValueError('图片不存在')

    def rules(self):
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='rules'").fetchone()
        return json.loads(row[0]) if row else {'enabled': True, 'disabled_scopes': []}

    def set_rules(self, enabled, disabled_scopes):
        if not isinstance(enabled, bool) or not isinstance(disabled_scopes, list) or any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in disabled_scopes):
            raise ValueError('会话格式须为 group:群号 或 private:QQ号')
        rules = {'enabled': enabled, 'disabled_scopes': sorted(set(disabled_scopes))}
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('rules',?)", (json.dumps(rules),))
        return rules

    def enabled(self, scope):
        rules = self.rules()
        return rules['enabled'] and scope not in rules['disabled_scopes']


class MarkerParser:
    """Consume markers split across chunks, emitting normal text as early as possible."""
    prefix = '[[meme:'

    def __init__(self, valid):
        self.valid = set(valid)
        self.pending = ''
        self.selected = None

    def feed(self, chunk):
        self.pending += chunk
        output = ''
        while self.pending:
            start = self.pending.find(self.prefix)
            if start < 0:
                keep = next((n for n in range(min(len(self.pending), len(self.prefix) - 1), 0, -1) if self.pending.endswith(self.prefix[:n])), 0)
                output += self.pending[:-keep] if keep else self.pending
                self.pending = self.pending[-keep:] if keep else ''
                break
            output += self.pending[:start]
            self.pending = self.pending[start:]
            end = self.pending.find(']]', len(self.prefix))
            if end < 0:
                if len(self.pending) > 100:
                    self.pending = ''
                break
            category = self.pending[len(self.prefix):end]
            if category in self.valid and self.selected is None:
                self.selected = category
            self.pending = self.pending[end + 2:]
        return output

    def finish(self):
        text = '' if self.pending.startswith(self.prefix) else self.pending
        self.pending = ''
        return text
