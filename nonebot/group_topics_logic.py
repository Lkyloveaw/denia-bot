"""Per-group opt-in keyword and speaker counts; message bodies are not stored."""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / 'data' / 'group_topics.sqlite3'
RETENTION_SECONDS = 7 * 86400
MAX_TERMS_PER_MESSAGE = 12
_stop = set('什么 怎么 为什么 这个 那个 这样 现在 今天 明天 昨天 大家 他们 我们 你们 自己 觉得 还是 可以 可能 已经 一直 一个 真的 知道 不是 没有 因为 所以 但是 然后 如果 这样子 哈哈 哈哈哈 哈哈哈哈 嗯嗯 好的 就是 多少 还是 一下 一些 比较 有点 这种 不是吧 机器人 群友 达妮娅 小娅 记忆 话题 查看 开启 关闭'.split())
_url = re.compile(r'(?:https?://|www\.)\S+', re.IGNORECASE)
_secret = re.compile(r'(?i)\b(?:sk|ah|api|token|key|password)[-_:=][A-Za-z0-9_./+=-]{6,}\b')
_digits = re.compile(r'\d{3,}')
_term = re.compile(r'^(?:[\u4e00-\u9fff]{2,8}|[a-zA-Z]{3,16})$')


@contextmanager
def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.executescript('''
      CREATE TABLE IF NOT EXISTS topic_groups(group_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS topic_counts(group_id TEXT NOT NULL, hour INTEGER NOT NULL,
        term TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(group_id,hour,term));
      CREATE TABLE IF NOT EXISTS topic_seen(group_id TEXT NOT NULL, message_id TEXT NOT NULL,
        hour INTEGER NOT NULL, PRIMARY KEY(group_id,message_id));
      CREATE TABLE IF NOT EXISTS topic_speakers(group_id TEXT NOT NULL, hour INTEGER NOT NULL,
        user_id TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL,
        PRIMARY KEY(group_id,hour,user_id));
      CREATE INDEX IF NOT EXISTS topic_counts_hour ON topic_counts(hour);
      CREATE INDEX IF NOT EXISTS topic_seen_hour ON topic_seen(hour);
      CREATE INDEX IF NOT EXISTS topic_speakers_hour ON topic_speakers(hour);
    ''')
    try:
        with con:
            yield con
    finally:
        con.close()


def enabled(path, group_id):
    return group_state(path, group_id)[0]


def group_state(path, group_id):
    with _connect(path) as con:
        row = con.execute('SELECT enabled,version FROM topic_groups WHERE group_id=?', (str(group_id),)).fetchone()
        return (bool(row['enabled']), row['version']) if row else (False, 0)


def version(path, group_id):
    return group_state(path, group_id)[1]


def set_enabled(path, group_id, value):
    with _connect(path) as con:
        con.execute('INSERT INTO topic_groups(group_id,enabled,version) VALUES(?,?,1) ON CONFLICT(group_id) DO UPDATE SET enabled=excluded.enabled,version=version+1', (str(group_id), int(bool(value))))


def keywords(text, tokenizer=None):
    text = (text or '').strip()
    if not text or text.startswith(('/', '.', '。', '!', '！')):
        return []
    clean = _digits.sub(' ', _secret.sub(' ', _url.sub(' ', text[:2000]))).lower()
    if tokenizer is None:
        import jieba
        tokenizer = jieba.lcut
    words = []
    for raw in tokenizer(clean):
        word = str(raw).strip().lower()
        if _term.fullmatch(word) and word not in _stop and word in clean and word not in words:
            words.append(word)
            if len(words) >= MAX_TERMS_PER_MESSAGE:
                break
    return words


def _prune(con, now):
    cutoff = int((now - RETENTION_SECONDS) // 3600)
    con.execute('DELETE FROM topic_counts WHERE hour < ?', (cutoff,))
    con.execute('DELETE FROM topic_seen WHERE hour < ?', (cutoff,))
    con.execute('DELETE FROM topic_speakers WHERE hour < ?', (cutoff,))


def cleanup(path, now=None):
    with _connect(path) as con:
        _prune(con, time.time() if now is None else now)


def record(path, group_id, message_id, text, now=None, tokenizer=None, expected_version=None, user_id=None, display_name=''):
    group_id = str(group_id)
    if not group_id or not message_id or not enabled(path, group_id):
        return 0
    words = [] if text.startswith(('群话题', '记忆')) else keywords(text, tokenizer)
    user_id = str(user_id or '')
    if not words and not user_id.isdigit():
        return 0
    now = time.time() if now is None else now
    hour = int(now // 3600)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT enabled,version FROM topic_groups WHERE group_id=?', (group_id,)).fetchone()
        if not row or not row['enabled'] or (expected_version is not None and row['version'] != expected_version):
            return 0
        _prune(con, now)
        seen = con.execute('INSERT OR IGNORE INTO topic_seen(group_id,message_id,hour) VALUES(?,?,?)', (group_id, str(message_id), hour))
        if not seen.rowcount:
            return 0
        if user_id.isdigit():
            name = ''.join(c for c in str(display_name or '') if c.isprintable()).strip()[:64]
            con.execute('''INSERT INTO topic_speakers(group_id,hour,user_id,display_name,count) VALUES(?,?,?,?,1)
                ON CONFLICT(group_id,hour,user_id) DO UPDATE SET count=count+1,
                display_name=CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE topic_speakers.display_name END''',
                (group_id, hour, user_id, name))
        for word in words:
            con.execute('INSERT INTO topic_counts(group_id,hour,term,count) VALUES(?,?,?,1) ON CONFLICT(group_id,hour,term) DO UPDATE SET count=count+1', (group_id,hour,word))
        return len(words)


def top(path, group_id, now=None, limit=10, min_count=1):
    now = time.time() if now is None else now
    with _connect(path) as con:
        _prune(con, now)
        group_id = str(group_id)
        row = con.execute('SELECT enabled FROM topic_groups WHERE group_id=?', (group_id,)).fetchone()
        if not row or not row['enabled']:
            return []
        return [(r['term'], r['n']) for r in con.execute('''SELECT term,SUM(count) n FROM topic_counts
            WHERE group_id=? GROUP BY term HAVING n>=? ORDER BY n DESC, term LIMIT ?''',
            (group_id,min_count,max(1,min(int(limit),20))))]


def speaker_top(path, group_id, now=None, limit=20):
    with _connect(path) as con:
        _prune(con, time.time() if now is None else now)
        group_id = str(group_id)
        row = con.execute('SELECT enabled FROM topic_groups WHERE group_id=?', (group_id,)).fetchone()
        if not row or not row['enabled']:
            return []
        return [dict(r) for r in con.execute('''SELECT s.user_id, SUM(s.count) AS count,
            COALESCE((SELECT t.display_name FROM topic_speakers t WHERE t.group_id=s.group_id
                AND t.user_id=s.user_id AND t.display_name != '' ORDER BY t.hour DESC LIMIT 1), '') AS display_name
            FROM topic_speakers s WHERE s.group_id=? GROUP BY s.group_id,s.user_id
            ORDER BY count DESC, s.user_id LIMIT ?''', (group_id, max(1, min(int(limit), 20))))]


def clear(path, group_id):
    with _connect(path) as con:
        con.execute('INSERT INTO topic_groups(group_id,enabled,version) VALUES(?,0,1) ON CONFLICT(group_id) DO UPDATE SET version=version+1', (str(group_id),))
        con.execute('DELETE FROM topic_counts WHERE group_id=?', (str(group_id),))
        con.execute('DELETE FROM topic_seen WHERE group_id=?', (str(group_id),))
        con.execute('DELETE FROM topic_speakers WHERE group_id=?', (str(group_id),))
