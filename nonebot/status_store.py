# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""状态卡的持久化层：一个是 JSON 状态，一个是 SQLite 采样库。

分两个存储是因为生命周期完全不同：

- **状态**（`statuscard-state.json`）—— 网络采样点、本月流量、告警规则进度、
  掉线时间、定时任务标记。特点是小、必须原子、必须能手工看一眼。
- **采样**（`statuscard.sqlite3`）—— 每 5 分钟一条的资源/网站/进程数据、异常
  事件、每日快照、发送记录。特点是量大、要按时间范围聚合、要能裁剪。

两者都不 import `nonebot`。日志对象由接线层注入，测试里传 `None` 就走标准库。
存储失败一律**不外抛**：采样写不进去不该影响聊天，但必须留下痕迹。
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

_STDLIB_LOG = logging.getLogger('statuscard')


def _log(log: Any, level: str, message: str, *args: Any) -> None:
    """同时容忍 nonebot 的 loguru 和标准库 logging。"""
    target = log or _STDLIB_LOG
    try:
        getattr(target, level, target.info)(message, *args)
    except Exception:  # noqa: BLE001 - 日志失败不能反过来把调用方弄挂
        pass


# ---------------------------------------------------------------------------
# JSON 状态
# ---------------------------------------------------------------------------

_DEFAULT_STATE: dict[str, Any] = {
    'version': 1,
    'alerts': {'rules': {}},
    'offline': {},
    'net': {'history': [], 'interval': 60},
    'disk_io': {},
    'counters': {'recv': 0, 'sent': 0},
    'schedule': {'daily_status': '', 'daily_report': '', 'weekly_report': ''},
    'llm': {'calls': 0, 'ok': 0, 'fail': 0, 'latency_ms': 0.0, 'ttft_ms': 0.0},
    'first_seen': '',
    'last_sample': 0.0,
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """递归补默认值。旧版本文件缺字段时不该整份丢掉。

    必须深拷贝 `base`：浅拷贝会让返回值与 `_DEFAULT_STATE` 共享嵌套 dict，
    任何一个实例改了状态，后面**同进程新建的实例**都会读到那份脏数据，
    即使它的状态文件根本不存在。
    """
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class StateStore:
    """带原子写的 JSON 状态文件。"""

    def __init__(self, path: Path | str, *, log: Any = None):
        self.path = Path(path)
        self.log = log
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(_DEFAULT_STATE)
        self._loaded = False

    # ---------------------------------------------------------------- 读写
    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._load_locked()

    def _load_locked(self) -> dict[str, Any]:
        if not self._loaded:
            raw: dict[str, Any] = {}
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(raw, dict):
                    raw = {}
            except FileNotFoundError:
                raw = {}
            except (OSError, ValueError) as exc:
                _log(self.log, 'warning', '[status] 状态文件读取失败，按空状态启动: %s', exc)
                raw = {}
            self._data = _deep_merge(_DEFAULT_STATE, raw)
            if not self._data.get('first_seen'):
                self._data['first_seen'] = date.today().isoformat()
            self._loaded = True
        return self._data

    def save(self, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            if data is not None:
                self._data = _deep_merge(_DEFAULT_STATE, dict(data))
            self._save_locked()

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + '.tmp')
            temp.write_text(json.dumps(self._data, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, self.path)
            _secure(self.path)
        except OSError as exc:
            _log(self.log, 'warning', '[status] 状态文件写入失败: %s', exc)

    # ---------------------------------------------------------------- 便捷访问
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._load_locked().get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._load_locked()[key] = value

    def section(self, key: str) -> dict[str, Any]:
        """取一个 dict 段；类型不对时换回空 dict，避免调用方到处判类型。"""
        with self._lock:
            value = self._load_locked().get(key)
            return value if isinstance(value, dict) else {}

    def update_section(self, key: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            data = self._load_locked()
            section = data.get(key)
            if not isinstance(section, dict):
                section = {}
                data[key] = section
            section.update(values)
            return section


def _secure(path: Path) -> None:
    """只给所有者读写。失败不影响功能（Windows 上基本不会成功）。"""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# SQLite 采样库
# ---------------------------------------------------------------------------

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS resource_samples (
        ts INTEGER PRIMARY KEY,
        cpu REAL, mem REAL, swap REAL, disk REAL,
        net_up REAL, net_down REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS website_samples (
        ts INTEGER NOT NULL,
        name TEXT NOT NULL,
        ok INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT '',
        delay_ms REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (ts, name)
    )""",
    """
    CREATE TABLE IF NOT EXISTS process_samples (
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        value REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (ts, kind, name)
    )""",
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        resolved_ts INTEGER NOT NULL DEFAULT 0
    )""",
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        day TEXT PRIMARY KEY,
        friends INTEGER, groups INTEGER, members INTEGER,
        recv_total INTEGER, sent_total INTEGER
    )""",
    """
    CREATE TABLE IF NOT EXISTS report_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        ts INTEGER NOT NULL,
        ok INTEGER NOT NULL DEFAULT 0,
        detail TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_res_ts ON resource_samples(ts)",
    "CREATE INDEX IF NOT EXISTS idx_web_ts ON website_samples(ts)",
    "CREATE INDEX IF NOT EXISTS idx_web_name ON website_samples(name, ts)",
    "CREATE INDEX IF NOT EXISTS idx_proc_ts ON process_samples(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_resolved ON events(resolved_ts)",
    "CREATE INDEX IF NOT EXISTS idx_runs_day ON report_runs(day)",
)


class SampleStore:
    """采样、事件、快照、发送记录的读写。

    每次操作开一个短连接：采样频率是分钟级，连接开销可以忽略，换来的是
    「不会因为一个连接被多线程共用而偶发 database is locked」。
    """

    def __init__(self, path: Path | str, *, log: Any = None):
        self.path = Path(path)
        self.log = log
        self._lock = threading.Lock()
        self._ready = False
        self._init()

    # ---------------------------------------------------------------- 建库
    def _init(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._session() as con:
                for statement in _SCHEMA:
                    con.execute(statement)
            _secure(self.path)
            self._ready = True
        except sqlite3.DatabaseError as exc:
            # 损坏的库不阻塞机器人：挪走留证，重建一个空的。
            backup = self.path.with_name(f'{self.path.name}.bak-{int(time.time())}')
            try:
                os.replace(self.path, backup)
                # WAL 附属文件必须一起挪走：留在原地的 -wal 会让新建的空库
                # 立刻再次报 "file is not a database"。
                for suffix in ('-wal', '-shm'):
                    sidecar = self.path.with_name(self.path.name + suffix)
                    if sidecar.exists():
                        os.replace(sidecar, backup.with_name(backup.name + suffix))
                _log(self.log, 'warning', '[status] 采样库损坏，已移至 %s：%s', backup, exc)
            except OSError:
                _log(self.log, 'error', '[status] 采样库损坏且无法备份：%s', exc)
            try:
                with self._session() as con:
                    for statement in _SCHEMA:
                        con.execute(statement)
                self._ready = True
            except sqlite3.DatabaseError as second:
                _log(self.log, 'error', '[status] 采样库重建失败，历史数据不可用：%s', second)
        except OSError as exc:
            _log(self.log, 'error', '[status] 采样库目录不可写，历史数据不可用：%s', exc)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=5)
        con.row_factory = sqlite3.Row
        return con

    @contextlib.contextmanager
    def _session(self):
        """开一个连接，退出时提交并**关闭**。

        `with sqlite3.connect(...)` 只提交不关闭：连接会一直被引用，Windows 上
        直接表现为库文件删不掉，Linux 上则是只增不减的文件描述符。

        PRAGMA 也放在 try 里面：库文件损坏时 `journal_mode=WAL` 会抛
        `DatabaseError`，如果它在 `_connect()` 里执行，连接就再也没人关了。
        """
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
            _log(self.log, 'warning', '[status] 写入失败: %s (%s)', exc, sql.split('(')[0].strip())
            return False

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        if not self._ready:
            return []
        try:
            with self._lock, self._session() as con:
                rows = con.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[status] 查询失败: %s', exc)
            return []

    def _write_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> bool:
        data = [tuple(row) for row in rows]
        if not data or not self._ready:
            return False
        try:
            with self._lock, self._session() as con:
                con.executemany(sql, data)
            return True
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[status] 批量写入失败: %s', exc)
            return False

    # ---------------------------------------------------------------- 写入
    def add_resource(
        self,
        ts: int,
        *,
        cpu: float,
        mem: float,
        swap: float,
        disk: float | None,
        net_up: float,
        net_down: float,
    ) -> bool:
        return self._write(
            'INSERT OR REPLACE INTO resource_samples(ts, cpu, mem, swap, disk, net_up, net_down)'
            ' VALUES(?,?,?,?,?,?,?)',
            (int(ts), float(cpu), float(mem), float(swap), _opt(disk), float(net_up), float(net_down)),
        )

    def add_website_samples(self, ts: int, samples: Sequence[dict[str, Any]]) -> bool:
        return self._write_many(
            'INSERT OR REPLACE INTO website_samples(ts, name, ok, status, delay_ms) VALUES(?,?,?,?,?)',
            [
                (int(ts), str(item.get('name') or ''), 1 if item.get('ok') else 0,
                 str(item.get('status') or ''), float(item.get('delay_ms') or 0))
                for item in samples
            ],
        )

    def add_process_samples(self, ts: int, samples: Sequence[dict[str, Any]]) -> bool:
        return self._write_many(
            'INSERT OR REPLACE INTO process_samples(ts, kind, name, value) VALUES(?,?,?,?)',
            [
                (int(ts), str(item.get('kind') or ''), str(item.get('name') or ''), float(item.get('value') or 0))
                for item in samples
            ],
        )

    def add_event(self, ts: int, kind: str, title: str, detail: str = '') -> int:
        if not self._ready:
            return 0
        try:
            with self._lock, self._session() as con:
                cur = con.execute(
                    'INSERT INTO events(ts, kind, title, detail) VALUES(?,?,?,?)',
                    (int(ts), str(kind), str(title), str(detail)),
                )
                return int(cur.lastrowid or 0)
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[status] 事件写入失败: %s', exc)
            return 0

    def resolve_event(self, title: str, ts: int) -> bool:
        """把最近一条未恢复的同名事件标上恢复时间。

        恢复时长靠这个字段算，所以它必须在恢复时立刻落库：插件如果随后被重启，
        内存里的配对就没了，而这个字段还在。
        """
        if not self._ready:
            return False
        try:
            with self._lock, self._session() as con:
                row = con.execute(
                    'SELECT id FROM events WHERE title=? AND resolved_ts=0 ORDER BY ts DESC LIMIT 1',
                    (str(title),),
                ).fetchone()
                if row is None:
                    return False
                con.execute('UPDATE events SET resolved_ts=? WHERE id=?', (int(ts), int(row['id'])))
            return True
        except sqlite3.Error as exc:
            _log(self.log, 'warning', '[status] 事件恢复写入失败: %s', exc)
            return False

    def record_snapshot(
        self,
        day: str,
        *,
        friends: int | None,
        groups: int | None,
        members: int | None,
        recv_total: int | None,
        sent_total: int | None,
    ) -> bool:
        return self._write(
            'INSERT OR REPLACE INTO snapshots(day, friends, groups, members, recv_total, sent_total)'
            ' VALUES(?,?,?,?,?,?)',
            (str(day), _opt(friends), _opt(groups), _opt(members), _opt(recv_total), _opt(sent_total)),
        )

    def add_report_run(self, day: str, ts: int, ok: bool, detail: str = '') -> bool:
        return self._write(
            'INSERT INTO report_runs(day, ts, ok, detail) VALUES(?,?,?,?)',
            (str(day), int(ts), 1 if ok else 0, str(detail)),
        )

    # ---------------------------------------------------------------- 读取
    def list_resources(self, since_ts: int, limit: int = 20000) -> list[dict[str, Any]]:
        return self._query(
            'SELECT * FROM resource_samples WHERE ts >= ? ORDER BY ts ASC LIMIT ?',
            (int(since_ts), int(limit)),
        )

    def list_website_samples(self, since_ts: int, limit: int = 20000) -> list[dict[str, Any]]:
        return self._query(
            'SELECT * FROM website_samples WHERE ts >= ? ORDER BY ts ASC LIMIT ?',
            (int(since_ts), int(limit)),
        )

    def list_process_samples(self, since_ts: int, limit: int = 20000) -> list[dict[str, Any]]:
        return self._query(
            'SELECT * FROM process_samples WHERE ts >= ? ORDER BY ts ASC LIMIT ?',
            (int(since_ts), int(limit)),
        )

    def list_events(self, since_ts: int, limit: int = 5000) -> list[dict[str, Any]]:
        return self._query(
            'SELECT * FROM events WHERE ts >= ? ORDER BY ts ASC LIMIT ?',
            (int(since_ts), int(limit)),
        )

    def get_snapshot(self, day: str) -> dict[str, Any] | None:
        rows = self._query('SELECT * FROM snapshots WHERE day=?', (str(day),))
        return rows[0] if rows else None

    def list_snapshots(self, days: int = 30) -> list[dict[str, Any]]:
        since = (date.today() - timedelta(days=max(1, int(days)))).isoformat()
        return self._query('SELECT * FROM snapshots WHERE day >= ? ORDER BY day ASC', (since,))

    def latest_report_run(self, day: str | None = None) -> dict[str, Any] | None:
        # 同一秒内可能写入多条（出图失败立刻回退文字），只按 ts 排会随机取到旧的那条
        if day:
            rows = self._query(
                'SELECT * FROM report_runs WHERE day=? ORDER BY ts DESC, id DESC LIMIT 1',
                (str(day),),
            )
        else:
            rows = self._query('SELECT * FROM report_runs ORDER BY ts DESC, id DESC LIMIT 1')
        return rows[0] if rows else None

    def snapshot_ready(self, day: str) -> bool:
        return self.get_snapshot(day) is not None

    # ---------------------------------------------------------------- 维护
    def prune(self, *, resource_days: int = 30, event_days: int = 90, now: float | None = None) -> dict[str, int]:
        """裁剪过期数据。返回各表删掉的行数，诊断命令会显示。"""
        now = time.time() if now is None else float(now)
        resource_cutoff = int(now - max(1, int(resource_days)) * 86400)
        event_cutoff = int(now - max(1, int(event_days)) * 86400)
        removed = {
            'resources': self._delete(
                'DELETE FROM resource_samples WHERE ts < ?', (resource_cutoff,)),
            'websites': self._delete(
                'DELETE FROM website_samples WHERE ts < ?', (resource_cutoff,)),
            'processes': self._delete(
                'DELETE FROM process_samples WHERE ts < ?', (resource_cutoff,)),
            'events': self._delete(
                'DELETE FROM events WHERE ts < ? AND resolved_ts > 0', (event_cutoff,)),
        }
        return removed

    def _delete(self, sql: str, params: Sequence[Any]) -> int:
        if not self._ready:
            return 0
        try:
            with self._lock, self._session() as con:
                cur = con.execute(sql, tuple(params))
                return int(cur.rowcount or 0)
        except sqlite3.Error:
            return 0

    # ---------------------------------------------------------------- 诊断
    def stats(self) -> dict[str, Any]:
        """各表行数与最新采样时间，面板和诊断命令都要用。"""
        if not self._ready:
            return {'ready': False}
        counts = {}
        for table in ('resource_samples', 'website_samples', 'process_samples', 'events', 'snapshots', 'report_runs'):
            rows = self._query(f'SELECT COUNT(*) AS n FROM {table}')
            counts[table] = int(rows[0]['n']) if rows else 0
        latest = self._query('SELECT MAX(ts) AS ts FROM resource_samples')
        return {
            'ready': True,
            'path': str(self.path),
            'counts': counts,
            'latest_resource_ts': (latest[0]['ts'] if latest else None),
        }


def _opt(value: Any) -> Any:
    """None / 非数值 → SQL NULL，避免把 'None' 字符串写进库。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def today_key(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime('%Y-%m-%d')


def yesterday_key(now: datetime | None = None) -> str:
    return ((now or datetime.now()) - timedelta(days=1)).strftime('%Y-%m-%d')


def day_window(day: str, *, tz_offset: int = 8) -> tuple[int, int]:
    """`2026-10-30` → 当天 00:00 与次日 00:00 的时间戳（默认 UTC+8）。

    必须构造带时区的 datetime：naive datetime 的 `timestamp()` 会按**系统本地
    时区**解释，同一个部署换成 UTC 机器，日报的「昨天」就会整体挪一天。
    """
    zone = timezone(timedelta(hours=int(tz_offset)))
    try:
        start = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=zone)
    except ValueError:
        start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    begin = int(start.timestamp())
    return begin, begin + 86400
