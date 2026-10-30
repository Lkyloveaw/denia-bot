"""Token 消耗账本：按「天 × 模型」累计调用次数与 token。

为什么要自己记一份：MuiceBot 只在消息表里存一个总 token 整数（`Msg.usage`），
既没有模型维度，也没法按天拆分，做不了「模型排名」。这里旁路记录每次调用的
模型名与用量，互不干扰。

存储是一个小 JSON 文件，结构：

    {
      "days": {
        "2026-10-30": {
          "总模型名": {"calls": 12, "tokens": 45678},
          ...
        }
      }
    }

保留最近 `KEEP_DAYS` 天，更早的自动丢弃。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

KEEP_DAYS = 90
"""保留多少天。面板要「多个档位」的总量，90 天足够，文件也不会变大。"""

UNKNOWN_MODEL = "未知模型"


def today() -> str:
    return date.today().isoformat()


def _blank() -> dict[str, Any]:
    return {"days": {}}


class TokenLedger:
    """线程安全的 token 账本。"""

    def __init__(self, path: Path, keep_days: int = KEEP_DAYS):
        self.path = path
        self.keep_days = keep_days
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 读写
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _blank()
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            return _blank()
        return data

    def _save(self, data: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass  # 记账失败不能影响聊天

    def _prune(self, data: dict[str, Any]) -> dict[str, Any]:
        cutoff = (date.today() - timedelta(days=self.keep_days)).isoformat()
        data["days"] = {d: v for d, v in data["days"].items() if d >= cutoff}
        return data

    # ---------------------------------------------------------------- 记账
    def record(self, model: str, tokens: int, *, when: str | None = None) -> None:
        """记一次调用。tokens <= -1 视为模型没报告用量，只累计次数。"""
        name = (model or UNKNOWN_MODEL).strip() or UNKNOWN_MODEL
        day = when or today()
        count = int(tokens) if tokens is not None else -1
        with self._lock:
            data = self._prune(self._load())
            entry = data["days"].setdefault(day, {}).setdefault(name, {"calls": 0, "tokens": 0})
            entry["calls"] += 1
            if count > 0:
                entry["tokens"] += count
            self._save(data)

    # ---------------------------------------------------------------- 查询
    def summary(self, days: int = 7) -> dict[str, Any]:
        """最近 N 天概览：总量、按模型的排名、按天的曲线。

        返回结构直接给前端用：
            {
              "totals": {"today":…, "week":…, "month":…, "all":…},
              "models": [{"model","tokens","calls","share"}, …],   # 近 N 天，按用量降序
              "daily": [{"day","tokens","calls"}, …],              # 近 N 天，按日期升序
              "range": {"days": N, "from": …, "to": …}
            }
        """
        with self._lock:
            data = self._prune(self._load())

        daily_map = data["days"]
        keys = sorted(daily_map)
        all_tokens = sum(m["tokens"] for d in daily_map.values() for m in d.values())
        all_calls = sum(m["calls"] for d in daily_map.values() for m in d.values())

        def tokens_of(day_list: list[str]) -> int:
            return sum(m["tokens"] for d in day_list for m in daily_map.get(d, {}).values())

        today_key = today()
        yesterday_key = (date.today() - timedelta(days=1)).isoformat()
        week_keys = _last_keys(7)
        month_keys = _last_keys(30)

        # 近 N 天的模型排名
        recent_keys = _last_keys(max(1, int(days)))
        per_model: dict[str, dict[str, int]] = {}
        for day in recent_keys:
            for model, stat in daily_map.get(day, {}).items():
                slot = per_model.setdefault(model, {"tokens": 0, "calls": 0})
                slot["tokens"] += stat["tokens"]
                slot["calls"] += stat["calls"]

        recent_total = sum(v["tokens"] for v in per_model.values()) or 1
        models = [
            {
                "model": model,
                "tokens": stat["tokens"],
                "calls": stat["calls"],
                "share": round(stat["tokens"] / recent_total, 4),
            }
            for model, stat in per_model.items()
        ]
        models.sort(key=lambda x: (-x["tokens"], -x["calls"], x["model"]))

        daily = []
        for key in recent_keys:
            entry = daily_map.get(key, {})
            daily.append({
                "day": key,
                "tokens": sum(m["tokens"] for m in entry.values()),
                "calls": sum(m["calls"] for m in entry.values()),
            })

        return {
            "totals": {
                "today": tokens_of([today_key]),
                "yesterday": tokens_of([yesterday_key]),
                "week": tokens_of(week_keys),
                "month": tokens_of(month_keys),
                "all": all_tokens,
            },
            "calls": {
                "today": _calls_of(daily_map, [today_key]),
                "week": _calls_of(daily_map, week_keys),
                "all": all_calls,
            },
            "models": models,
            "daily": daily,
            "range": {
                "days": days,
                "from": recent_keys[0] if recent_keys else today(),
                "to": recent_keys[-1] if recent_keys else today(),
            },
            "first_day": keys[0] if keys else "",
        }

    def clear(self) -> None:
        with self._lock:
            self._save(_blank())


def _last_keys(days: int) -> list[str]:
    """最近 N 天（含今天）的日期键，升序。"""
    now = date.today()
    return [(now - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _calls_of(daily_map: dict[str, Any], keys: list[str]) -> int:
    return sum(m["calls"] for d in keys for m in daily_map.get(d, {}).values())
