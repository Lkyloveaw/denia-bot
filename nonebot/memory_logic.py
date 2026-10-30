"""Per-user persistent memories and safe handling of asynchronous extraction."""
import json
import math
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / 'data' / 'memories.sqlite3'
MAX_MEMORIES = 200


@contextmanager
def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''
        CREATE TABLE IF NOT EXISTS memory_users (user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            text TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, text));
        CREATE TABLE IF NOT EXISTS memory_vectors (
            memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            vector BLOB NOT NULL, model TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            text TEXT NOT NULL, source TEXT NOT NULL, similar_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,text));
        CREATE TABLE IF NOT EXISTS memory_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, event_id TEXT NOT NULL,
            text TEXT, source TEXT NOT NULL, context TEXT NOT NULL, revision INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id,event_id));
        CREATE TABLE IF NOT EXISTS memory_preferences (user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
    ''')
    try:
        with con:
            yield con
    finally:
        con.close()


def revision(path, user_id):
    with _connect(path) as con:
        row = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (str(user_id),)).fetchone()
        return row['revision'] if row else 0


def list_memories(path, user_id):
    with _connect(path) as con:
        return [dict(r) for r in con.execute(
            'SELECT id, text, source FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?',
            (str(user_id), MAX_MEMORIES))]


def parse_extraction(raw):
    try:
        value = json.loads(raw)
        values = value.get('memories', []) if isinstance(value, dict) else []
        if not isinstance(values, list):
            return []
        result = []
        for v in values[:3]:
            if isinstance(v, str):
                text = ' '.join(v.split())
                if 4 <= len(text) <= 120 and text not in result:
                    result.append(text)
        return result
    except (ValueError, TypeError):
        return []


def store(path, user_id, facts, source, expected_revision):
    user_id = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (user_id,)).fetchone()
        if (row['revision'] if row else 0) != expected_revision:
            return 0
        count = con.execute('SELECT COUNT(*) FROM memories WHERE user_id=?', (user_id,)).fetchone()[0]
        inserted = 0
        for fact in facts:
            if count >= MAX_MEMORIES:
                break
            cur = con.execute('INSERT OR IGNORE INTO memories(user_id,text,source) VALUES(?,?,?)',
                              (user_id, fact, source))
            inserted += cur.rowcount
            count += cur.rowcount
        return inserted


def forget(path, user_id, target):
    user_id = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        if target == '全部':
            cur = con.execute('DELETE FROM memories WHERE user_id=?', (user_id,))
            con.execute('DELETE FROM memory_candidates WHERE user_id=?', (user_id,))
        elif str(target).isdigit():
            cur = con.execute('DELETE FROM memories WHERE user_id=? AND id=?', (user_id, int(target)))
            con.execute('DELETE FROM memory_candidates WHERE user_id=? AND similar_id=?', (user_id, int(target)))
        else:
            return False
        con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (user_id,))
        con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (user_id,))
        return cur.rowcount > 0


def correct(path, user_id, memory_id, text):
    if not str(memory_id).isdigit() or not 4 <= len(text.strip()) <= 120:
        return False
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        try:
            cur = con.execute('UPDATE memories SET text=? WHERE user_id=? AND id=?', (text.strip(), str(user_id), int(memory_id)))
        except sqlite3.IntegrityError:
            return False
        if cur.rowcount:
            con.execute('DELETE FROM memory_vectors WHERE memory_id=?', (int(memory_id),))
            con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (str(user_id),))
            con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (str(user_id),))
        return bool(cur.rowcount)


def render_memories(memories, in_group=False):
    if not memories:
        return ''
    intro = '\n\n以下是与当前用户有关的长期记忆，只是参考，可能过时或有误；用户纠正时以用户说法为准。'
    if in_group:
        intro += ' 标注私聊的内容只作理解背景，不要主动在群里透露或复述私聊细节。'
    lines = [f"- {'[私聊] ' if m['source'] == 'private' else ''}{m['text']}" for m in memories]
    return intro + '\n' + '\n'.join(lines)


def _pack(vector):
    values = [float(x) for x in vector]
    if not values or len(values) > 4096 or any(not math.isfinite(x) for x in values):
        raise ValueError('invalid vector')
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0:
        raise ValueError('zero vector')
    return struct.pack('<%sf' % len(values), *(x / norm for x in values))


def _similarity(a, b):
    if not a or not b or len(a) != len(b) or len(a) % 4:
        return -1.0
    count = len(a) // 4
    return sum(x * y for x, y in zip(struct.unpack('<%sf' % count, a), struct.unpack('<%sf' % count, b)))


def set_vector(path, user_id, memory_id, expected_text, vector):
    packed = _pack(vector)
    with _connect(path) as con:
        row = con.execute('SELECT id FROM memories WHERE id=? AND user_id=? AND text=?', (memory_id, str(user_id), expected_text)).fetchone()
        if not row:
            return False
        con.execute('INSERT OR REPLACE INTO memory_vectors(memory_id,vector,model) VALUES(?,?,?)', (memory_id, packed, 'bge-m3-fp16-v1'))
        return True


def missing_vectors(path, user_id, limit=20):
    with _connect(path) as con:
        return [dict(r) for r in con.execute('''SELECT m.id,m.text FROM memories m LEFT JOIN memory_vectors v
            ON m.id=v.memory_id AND v.model='bge-m3-fp16-v1' WHERE m.user_id=? AND v.memory_id IS NULL ORDER BY m.id LIMIT ?''', (str(user_id),limit))]


def recall(path, user_id, vector, query, limit=5):
    packed = _pack(vector) if vector is not None else None
    with _connect(path) as con:
        rows = con.execute('''SELECT m.id,m.text,m.source,v.vector FROM memories m
            LEFT JOIN memory_vectors v ON m.id=v.memory_id AND v.model='bge-m3-fp16-v1'
            WHERE m.user_id=?''', (str(user_id),)).fetchall()
    scored = []
    for row in rows:
        score = _similarity(packed, row['vector']) if packed is not None else -1
        text = str(query)
        ignored = {'什么', '用户', '他们', '知道', '你好', '今天', '可以', '这些', '我的'}
        lexical = any(text[i:i+2] not in ignored and text[i:i+2].strip() == text[i:i+2]
                      and text[i:i+2] in row['text'] for i in range(max(0, len(text)-1)))
        if score >= 0.60 or (lexical and (packed is None or row['vector'] is None or score >= 0.55)):
            scored.append((max(score, 0.61 if lexical else -1), dict(row)))
    scored.sort(key=lambda x: (-x[0], -x[1]['id']))
    return [{k: v for k, v in row.items() if k != 'vector'} for _, row in scored[:limit]]


def store_vector_fact(path, user_id, text, source, expected_revision, vector):
    user_id, text = str(user_id), ' '.join(text.split())
    if not 4 <= len(text) <= 120:
        return 'invalid'
    packed = _pack(vector)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (user_id,)).fetchone()
        if (row['revision'] if row else 0) != expected_revision:
            return 'stale'
        rows = con.execute('''SELECT m.id,m.text,v.vector FROM memories m LEFT JOIN memory_vectors v
            ON m.id=v.memory_id AND v.model='bge-m3-fp16-v1' WHERE m.user_id=?''', (user_id,)).fetchall()
        if any(r['text'] == text for r in rows):
            return 'duplicate'
        similar = max((( _similarity(packed, r['vector']), r['id']) for r in rows), default=(-1, None))
        if len(rows) >= MAX_MEMORIES or similar[0] >= 0.90:
            count = con.execute('SELECT COUNT(*) FROM memory_candidates WHERE user_id=?', (user_id,)).fetchone()[0]
            if count >= 50:
                return 'candidate_full'
            con.execute('INSERT OR IGNORE INTO memory_candidates(user_id,text,source,similar_id) VALUES(?,?,?,?)', (user_id, text, source, similar[1]))
            return 'candidate'
        cur = con.execute('INSERT INTO memories(user_id,text,source) VALUES(?,?,?)', (user_id,text,source))
        con.execute('INSERT INTO memory_vectors(memory_id,vector,model) VALUES(?,?,?)', (cur.lastrowid,packed,'bge-m3-fp16-v1'))
        return 'saved'


def list_candidates(path, user_id):
    with _connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,text,source,similar_id FROM memory_candidates WHERE user_id=? ORDER BY id DESC LIMIT 50', (str(user_id),))]


def resolve_candidate(path, user_id, candidate_id, action):
    if not str(candidate_id).isdigit() or action not in ('accept', 'replace', 'discard'):
        return False
    uid = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM memory_candidates WHERE id=? AND user_id=?', (int(candidate_id), uid)).fetchone()
        if not row:
            return False
        if action == 'accept':
            count = con.execute('SELECT COUNT(*) FROM memories WHERE user_id=?', (uid,)).fetchone()[0]
            if count >= MAX_MEMORIES:
                return False
            con.execute('INSERT OR IGNORE INTO memories(user_id,text,source) VALUES(?,?,?)', (uid,row['text'],row['source']))
        elif action == 'replace':
            try:
                cur = con.execute("UPDATE memories SET text=?, source=CASE WHEN source='private' OR ?='private' THEN 'private' ELSE 'group' END WHERE id=? AND user_id=?", (row['text'],row['source'],row['similar_id'],uid))
            except sqlite3.IntegrityError:
                return False
            if not cur.rowcount:
                return False
            con.execute('DELETE FROM memory_vectors WHERE memory_id=?', (row['similar_id'],))
        con.execute('DELETE FROM memory_candidates WHERE id=? AND user_id=?', (int(candidate_id),uid))
        con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (uid,))
        con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (uid,))
        return True


def enqueue(path, user_id, text, source, event_id, context, expected_revision=None):
    uid = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        pref = con.execute('SELECT enabled FROM memory_preferences WHERE user_id=?', (uid,)).fetchone()
        if pref is not None and not pref['enabled']:
            return None
        con.execute("DELETE FROM memory_jobs WHERE status='done' AND created_at < datetime('now','-7 days')")
        con.execute("DELETE FROM memory_jobs WHERE status='failed' AND created_at < datetime('now','-7 days')")
        rev = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (uid,)).fetchone()
        current_revision = rev['revision'] if rev else 0
        if expected_revision is not None and expected_revision != current_revision:
            return None
        con.execute('INSERT OR IGNORE INTO memory_jobs(user_id,event_id,text,source,context,revision) VALUES(?,?,?,?,?,?)',
                    (uid,str(event_id),text[:2000],source,json.dumps(context[-3:],ensure_ascii=False),current_revision))
        return con.execute('SELECT id FROM memory_jobs WHERE user_id=? AND event_id=?', (uid,str(event_id))).fetchone()[0]


def pending_jobs(path):
    with _connect(path) as con:
        return [dict(r) for r in con.execute("SELECT * FROM memory_jobs WHERE status='pending' ORDER BY id LIMIT 32")]


def fail_job(path, job_id, error):
    with _connect(path) as con:
        con.execute("UPDATE memory_jobs SET attempts=attempts+1,last_error=?,status=CASE WHEN attempts+1>=3 THEN 'failed' ELSE 'pending' END WHERE id=? AND status='pending'", (str(error)[:80],job_id))


def complete_job(path, job_id):
    with _connect(path) as con:
        con.execute("UPDATE memory_jobs SET status='done',text=NULL,context='[]',last_error=NULL WHERE id=?", (job_id,))


def is_enabled(path, user_id):
    with _connect(path) as con:
        row = con.execute('SELECT enabled FROM memory_preferences WHERE user_id=?', (str(user_id),)).fetchone()
        return bool(row['enabled']) if row else True


def set_enabled(path, user_id, enabled):
    uid = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('INSERT INTO memory_preferences(user_id,enabled) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled', (uid, int(bool(enabled))))
        if not enabled:
            con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (uid,))
            con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (uid,))


def job_status(path, user_id):
    with _connect(path) as con:
        return {r['status']: r['n'] for r in con.execute("SELECT status,COUNT(*) n FROM memory_jobs WHERE user_id=? AND status!='done' GROUP BY status", (str(user_id),))}


def retry_failed(path, user_id):
    with _connect(path) as con:
        cur = con.execute("UPDATE memory_jobs SET status='pending',attempts=0,last_error=NULL WHERE user_id=? AND status='failed'", (str(user_id),))
        return cur.rowcount
