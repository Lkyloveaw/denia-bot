# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 实现 V1 事件/推送/健康/统计 SQLite；
# 许可见 docs/third-party/disaster-warning-NOTICE.md，口径见 docs/research/disaster-event-storage.md

"""灾害预警 V1 事件库：事件、推送目标、源健康、统计。

设计口径（用户确认）：
- 30 天保留；存所有处理过的事件（含 filtered/ignored）。
- 不跨源合并；身份 = 各源自己的 key。
- 每个推送目标一行成功/失败；V1 不重试。
- 训练/取消报入库标 ignored，不推送。
- 不存 token、原始请求体或完整上游报文。
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

_STDLIB_LOG = logging.getLogger('disaster')
KEEP_DAYS = 30
SCHEMA_VERSION = 1
_SECRET_RE = re.compile(
    r'(?i)(token|key|password|secret|authorization)\s*[=:]\s*[^&\s,;]+'
)
_BEARER_RE = re.compile(r'(?i)bearer\s+[A-Za-z0-9._~+/=-]+')


def _sanitize(value: Any, limit: int = 300) -> str:
    """错误/说明入库前去掉明显密钥；宁可少存，不把 token 写进历史。"""
    text = str(value or '')
    text = _BEARER_RE.sub('Bearer ***', text)
    text = _SECRET_RE.sub(r'\1=***', text)
    return text[:limit]


def _log(log: Any, level: str, message: str, *args: Any) -> None:
    target = log or _STDLIB_LOG
    try:
        getattr(target, level, target.info)(message, *args)
    except Exception:  # noqa: BLE001 - 日志失败不能反过来弄挂调用方
        pass


def _secure(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS events (
        event_key TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        event_id TEXT NOT NULL DEFAULT '',
        report_num INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '',
        place TEXT NOT NULL DEFAULT '',
        magnitude REAL,
        occurred_at TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'filtered',
        tone TEXT NOT NULL DEFAULT 'neutral',
        detail TEXT NOT NULL DEFAULT '',
        first_seen REAL NOT NULL,
        last_seen REAL NOT NULL,
        pushes INTEGER NOT NULL DEFAULT 0,
        push_ok INTEGER NOT NULL DEFAULT 0,
        push_fail INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_seen ON events(last_seen)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_kind_seen ON events(kind, last_seen)
    """,
    """
    CREATE TABLE IF NOT EXISTS pushes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL,
        ts REAL NOT NULL,
        target_type TEXT NOT NULL DEFAULT '',
        target TEXT NOT NULL DEFAULT '',
        ok INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pushes_event ON pushes(event_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pushes_ts ON pushes(ts)
    """,
    """
    CREATE TABLE IF NOT EXISTS source_health (
        source TEXT PRIMARY KEY,
        last_ok REAL,
        last_error TEXT NOT NULL DEFAULT '',
        error_at REAL
    )
    """,
)


class EventStore:
    """SQLite 事件库。所有方法失败只记日志、返回空/False，不抛给聊天链路。"""

    def __init__(self, path: Path | str | None = None, *, log: Any = None):
        self.path = Path(path) if path is not None else (
            Path(__file__).resolve().parent / 'data' / 'disaster_warning.sqlite3'
        )
        self.log = log
        self._lock = threading.Lock()
        self._ready = False
        self._error = ''
        self._init()

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> str:
        return self._error

    # ---------------------------------------------------------------- 建库
    def _init(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._session() as con:
                for statement in _SCHEMA:
                    con.execute(statement)
                con.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            _secure(self.path)
            self._ready = True
        except sqlite3.DatabaseError as exc:
            backup = self.path.with_name(f'{self.path.name}.bak-{int(time.time())}')
            try:
                os.replace(self.path, backup)
                for suffix in ('-wal', '-shm'):
                    sidecar = self.path.with_name(self.path.name + suffix)
                    if sidecar.exists():
                        os.replace(sidecar, backup.with_name(backup.name + suffix))
                _log(self.log, 'warning', '[disaster] 事件库损坏，已移至 %s：%s', backup, exc)
            except OSError:
                _log(self.log, 'error', '[disaster] 事件库损坏且无法备份：%s', exc)
            try:
                with self._session() as con:
                    for statement in _SCHEMA:
                        con.execute(statement)
                    con.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                self._ready = True
            except sqlite3.DatabaseError as second:
                self._error = str(second)
                _log(self.log, 'error', '[disaster] 事件库重建失败，历史不可用：%s', second)
        except OSError as exc:
            self._error = str(exc)
            _log(self.log, 'error', '[disaster] 事件库目录不可写，历史不可用：%s', exc)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=5)
        con.row_factory = sqlite3.Row
        return con

    @contextlib.contextmanager
    def _session(self):
        con = self._connect()
        try:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA synchronous=NORMAL')
            yield con
            con.commit()
        finally:
            con.close()

    def _write(self, sql: str, params: Sequence[Any] = ()) -> bool:
        if not self._ready:
            return False
        try:
            with self._lock, self._session() as con:
                con.execute(sql, tuple(params))
            return True
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[disaster] 事件库写入失败：%s', exc)
            return False

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        if not self._ready:
            return []
        try:
            with self._lock, self._session() as con:
                rows = con.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[disaster] 事件库查询失败：%s', exc)
            return []

    # ---------------------------------------------------------------- 事件
    def record_event(
        self,
        *,
        event_key: str,
        kind: str,
        status: str = 'filtered',
        source: str = '',
        event_id: str = '',
        report_num: int = 0,
        title: str = '',
        place: str = '',
        magnitude: float | None = None,
        occurred_at: str = '',
        tone: str = 'neutral',
        detail: str = '',
        when: float | None = None,
    ) -> bool:
        key = str(event_key or '').strip()
        if not key:
            return False
        now = float(when if when is not None else time.time())
        return self._write(
            """
            INSERT INTO events(
                event_key, kind, source, event_id, report_num, title, place,
                magnitude, occurred_at, status, tone, detail, first_seen, last_seen
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_key) DO UPDATE SET
                kind=excluded.kind,
                source=excluded.source,
                event_id=excluded.event_id,
                report_num=excluded.report_num,
                title=excluded.title,
                place=excluded.place,
                magnitude=excluded.magnitude,
                occurred_at=excluded.occurred_at,
                status=excluded.status,
                tone=excluded.tone,
                detail=excluded.detail,
                last_seen=excluded.last_seen
            """,
            (
                key, str(kind or 'unknown'), str(source or ''),
                str(event_id or ''), int(report_num or 0), str(title or ''),
                str(place or ''), None if magnitude is None else float(magnitude),
                str(occurred_at or ''), str(status or 'filtered'),
                str(tone or 'neutral'), _sanitize(detail, 200), now, now,
            ),
        )

    def set_status(self, event_key: str, status: str) -> bool:
        # 只改状态，不动 last_seen：否则旧事件会被“刷新”到当前，逃过 30 天清理。
        return self._write(
            'UPDATE events SET status=? WHERE event_key=?',
            (str(status or 'filtered'), str(event_key or '')),
        )

    # ---------------------------------------------------------------- 推送
    def record_push(
        self,
        event_key: str,
        *,
        target_type: str,
        target: str,
        ok: bool,
        error: str = '',
        when: float | None = None,
    ) -> bool:
        key = str(event_key or '').strip()
        if not key:
            return False
        now = float(when if when is not None else time.time())
        try:
            with self._lock, self._session() as con:
                con.execute(
                    'INSERT INTO pushes(event_key, ts, target_type, target, ok, error)'
                    ' VALUES(?,?,?,?,?,?)',
                    (key, now, str(target_type or ''), str(target or ''),
                     1 if ok else 0, _sanitize(error, 300)),
                )
                con.execute(
                    'UPDATE events SET pushes=pushes+1, push_ok=push_ok+?,'
                    ' push_fail=push_fail+? WHERE event_key=?',
                    (1 if ok else 0, 0 if ok else 1, key),
                )
            return True
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[disaster] 推送记录写入失败：%s', exc)
            return False

    # ---------------------------------------------------------------- 源健康
    def sync_health(self, last_poll: dict[str, Any],
                    errors: dict[str, Any], *, when: float | None = None) -> bool:
        if not self._ready:
            return False
        now = float(when if when is not None else time.time())
        sources = set(last_poll or {}) | set(errors or {})
        try:
            with self._lock, self._session() as con:
                for source in sources:
                    ok_at = (last_poll or {}).get(source)
                    error = _sanitize((errors or {}).get(source), 300)
                    last_ok = float(ok_at) if isinstance(ok_at, (int, float)) else None
                    con.execute(
                        """
                        INSERT INTO source_health(source, last_ok, last_error, error_at)
                        VALUES(?,?,?,?)
                        ON CONFLICT(source) DO UPDATE SET
                            last_ok=COALESCE(excluded.last_ok, source_health.last_ok),
                            last_error=excluded.last_error,
                            error_at=excluded.error_at
                        """,
                        (str(source), last_ok, error, now if error else None),
                    )
            return True
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[disaster] 源健康写入失败：%s', exc)
            return False

    # ---------------------------------------------------------------- 统计
    def _window(self, seconds: float, now: float) -> dict[str, Any]:
        since = now - seconds
        rows = self._query(
            'SELECT kind, status, COUNT(*) AS n FROM events'
            ' WHERE last_seen>=? GROUP BY kind, status',
            (since,),
        )
        total = 0
        by_status: dict[str, int] = {}
        by_kind: dict[str, dict[str, int]] = {}
        for row in rows:
            count = int(row.get('n') or 0)
            status = str(row.get('status') or 'unknown')
            kind = str(row.get('kind') or 'unknown')
            total += count
            by_status[status] = by_status.get(status, 0) + count
            by_kind.setdefault(kind, {})[status] = by_kind.get(kind, {}).get(status, 0) + count
        push_rows = self._query(
            'SELECT COALESCE(SUM(ok),0) AS ok,'
            ' COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) AS fail'
            ' FROM pushes WHERE ts>=?',
            (since,),
        )
        pushes = push_rows[0] if push_rows else {'ok': 0, 'fail': 0}
        return {
            'since': since,
            'events': total,
            'pushed': by_status.get('pushed', 0),
            'failed': by_status.get('failed', 0),
            'filtered': by_status.get('filtered', 0),
            'ignored': by_status.get('ignored', 0),
            'by_status': by_status,
            'by_kind': by_kind,
            'push_ok': int(pushes.get('ok') or 0),
            'push_fail': int(pushes.get('fail') or 0),
        }

    def stats(self, days: int = 7, *, now: float | None = None) -> dict[str, Any]:
        current = float(now if now is not None else time.time())
        days = max(1, min(int(days or 7), 90))
        return {
            'ready': self._ready,
            'error': self._error,
            'path': str(self.path),
            'generated_at': current,
            'days': days,
            'windows': {
                '24h': self._window(24 * 3600, current),
                '7d': self._window(7 * 24 * 3600, current),
            },
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 20), 50))
        return self._query(
            'SELECT event_key, kind, source, event_id, report_num, title, place,'
            ' magnitude, occurred_at, status, tone, detail, first_seen, last_seen,'
            ' pushes, push_ok, push_fail FROM events ORDER BY last_seen DESC LIMIT ?',
            (limit,),
        )

    # ---------------------------------------------------------------- 清理
    def prune(self, days: int = KEEP_DAYS, *, now: float | None = None) -> dict[str, int]:
        if not self._ready:
            return {'events': 0, 'pushes': 0}
        cutoff = float(now if now is not None else time.time()) - max(1, int(days)) * 86400
        try:
            with self._lock, self._session() as con:
                events = con.execute(
                    'DELETE FROM events WHERE last_seen<?', (cutoff,)
                ).rowcount
                pushes = con.execute(
                    'DELETE FROM pushes WHERE ts<?', (cutoff,)
                ).rowcount
                orphan = con.execute(
                    'DELETE FROM pushes WHERE event_key NOT IN'
                    ' (SELECT event_key FROM events)'
                ).rowcount
            return {'events': int(events), 'pushes': int(pushes) + int(orphan)}
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[disaster] 事件库清理失败：%s', exc)
            return {'events': 0, 'pushes': 0}

    def close(self) -> None:
        """每个操作独立连接；保留接口，方便 Runtime.stop 调用。"""
        return None
