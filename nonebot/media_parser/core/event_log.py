"""解析器事件日志：把解析失败、超出阈值等写进一个可由管理面板读取的文件。

为什么单独一个文件而不只靠 loguru：
- 管理面板要按时间倒序列出「最近发生了什么」，翻文本日志既慢又难解析
- 面板进程和机器人进程分开，读 JSON 行比 grep 文本可靠

写入是追加式的 JSON Lines，每行一条；超过上限时自动截断旧记录。
"""

import json
import threading
import time
from pathlib import Path
from typing import Any

MAX_ENTRIES = 500
"""保留最近多少条。文件超过这个数量约 2 倍时压缩重写。"""


class ParserEventLog:
    """线程安全的追加式事件日志。"""

    def __init__(self, path: Path, max_entries: int = MAX_ENTRIES):
        self.path = path
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def record(
        self,
        kind: str,
        reason: str,
        detail: str,
        *,
        platform: str = "",
        url: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """写一条事件。kind 取 error / limit / warn。"""
        entry = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
            "kind": kind,
            "reason": reason,
            "detail": detail,
            "platform": platform,
            "url": (url or "")[:500],
        }
        if extra:
            entry["extra"] = extra
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._maybe_compact()
            except OSError:
                pass  # 日志写失败不能影响机器人本身
        return entry

    def _maybe_compact(self) -> None:
        """行数涨到上限两倍时，只保留最近 max_entries 条重写。

        每条写入都数一遍文件行数：事件日志写入不频繁（只在出错/超限时），
        这点开销远小于为省 IO 写出的估算门槛 —— 早期版本先卡 64KB 再数行，
        短记录永远达不到，结果是无界增长。
        """
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if len(lines) <= self.max_entries * 2:
                return
            keep = lines[-self.max_entries:]
            temp = self.path.with_suffix(".tmp")
            temp.write_text("\n".join(keep) + "\n", encoding="utf-8")
            temp.replace(self.path)
        except OSError:
            pass

    def read(self, limit: int = 100) -> list[dict[str, Any]]:
        """读最近的记录，按时间倒序。"""
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
            if len(entries) >= limit:
                break
        return entries

    def stats(self) -> dict[str, Any]:
        """给面板用：总数、各类计数、最早最晚时间。"""
        entries = self.read(limit=self.max_entries)
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.get("kind", "other")] = counts.get(entry.get("kind", "other"), 0) + 1
        return {
            "total": len(entries),
            "counts": counts,
            "latest": entries[0]["t"] if entries else "",
            "size": self.path.stat().st_size if self.path.exists() else 0,
        }

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


_log: ParserEventLog | None = None


def event_log(config=None) -> ParserEventLog:
    """取全局事件日志（数据目录下的 media_parser/events.jsonl）。"""
    global _log
    if _log is None:
        base = Path(config.data_dir) if config is not None else Path(__file__).resolve().parent.parent / "data" / "media_parser"
        _log = ParserEventLog(Path(base) / "events.jsonl")
    return _log
