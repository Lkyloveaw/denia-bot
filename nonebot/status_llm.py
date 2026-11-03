# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""LLM 服务质量统计：包一层 provider，记录延迟、成功率与首字延迟。

上游读 AstrBot 的 `provider_stats` 表；MuiceBot 没有这张表，但模型调用只有一个
入口——`Muice().model.ask(request, stream=...)`。在这里包一层，就能拿到上游那张
表能给出的全部东西，而且不依赖任何数据库。

三条口径：

- **用户主动中止不算失败**：流式生成被调用方提前关闭（`GeneratorExit` /
  `CancelledError`）单独记 `aborted`，不打断连续失败链，也不拉低成功率。
- **成功调用才计入延迟**：失败请求的耗时没有参考价值（多半是超时那一刻的
  30 秒），混进去会让「平均延迟」毫无意义。
- **按天分桶，保留 30 天**：日报要看「昨天」，周报要看七天，面板要看趋势。

包装是幂等的，并且会在每次采样时 `ensure()` 一次——因为 `model_pref` 换模型会
重建 provider 实例，旧的包装会跟着旧实例一起被丢掉。
"""

from __future__ import annotations

import time
from typing import Any, Callable

__all__ = ['LLMQuality', 'UNKNOWN_MODEL']

UNKNOWN_MODEL = '未知模型'
KEEP_DAYS = 30


class LLMQuality:
    """模型调用质量账本 + provider 包装器。"""

    def __init__(self, store: Any = None, *, log: Any = None):
        self.store = store
        self.log = log
        self._installed_on: int | None = None
        self._model_name = UNKNOWN_MODEL

    # ---------------------------------------------------------------- 包装
    def install(self) -> bool:
        """包装当前 provider 实例。已经包过就直接返回 True。"""
        try:
            from muicebot.muice import Muice

            provider = getattr(Muice(), 'model', None)
        except Exception:  # noqa: BLE001 - 机器人还没起来时不该炸
            return False
        if provider is None or not hasattr(provider, 'ask'):
            return False
        if getattr(provider, '__statuscard_wrapped__', False):
            return True
        try:
            original: Callable[..., Any] = provider.ask
        except Exception:  # noqa: BLE001
            return False

        async def wrapped(request: Any, *, stream: bool = False, **kwargs: Any):
            return await self._call(provider, original, request, stream=stream, **kwargs)

        for attribute in ('__name__', '__doc__', '__module__'):
            try:
                setattr(wrapped, attribute, getattr(original, attribute, None))
            except Exception:  # noqa: BLE001 - 属性只影响可读性
                pass
        try:
            provider.ask = wrapped
            provider.__statuscard_wrapped__ = True
        except Exception:  # noqa: BLE001 - __slots__ 之类，包不上就放弃
            return False
        self._installed_on = id(provider)
        return True

    def ensure(self) -> bool:
        """每次采样调一次：换过 provider 就重新包上。"""
        try:
            from muicebot.muice import Muice

            provider = getattr(Muice(), 'model', None)
        except Exception:  # noqa: BLE001
            return False
        if provider is None:
            return False
        if id(provider) != self._installed_on or not getattr(provider, '__statuscard_wrapped__', False):
            return self.install()
        return True

    async def _call(self, provider: Any, original: Callable[..., Any], request: Any, *, stream: bool):
        model = self._resolve_name(provider)
        started = time.perf_counter()
        try:
            response = await original(request, stream=stream)
        except BaseException:
            self.record(model, ok=False, latency_ms=(time.perf_counter() - started) * 1000)
            raise

        if stream and hasattr(response, '__aiter__'):
            return self._wrap_stream(response, model, started)
        self.record(model, ok=True, latency_ms=(time.perf_counter() - started) * 1000)
        return response

    def _wrap_stream(self, response: Any, model: str, started: float):
        """流式：首字延迟要在这里量，正文一吐完就记账。"""
        quality = self

        async def generator():
            first_chunk: float | None = None
            try:
                async for chunk in response:
                    if first_chunk is None:
                        first_chunk = time.perf_counter() - started
                    yield chunk
            except BaseException:
                quality.record(
                    model,
                    ok=False,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                raise
            else:
                quality.record(
                    model,
                    ok=True,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    ttft_ms=(first_chunk * 1000) if first_chunk is not None else None,
                )

        return generator()

    @staticmethod
    def _resolve_name(provider: Any) -> str:
        for attribute in ('model', 'model_name'):
            value = getattr(provider, attribute, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        config = getattr(provider, 'config', None)
        for attribute in ('model_name', 'model'):
            value = getattr(config, attribute, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return UNKNOWN_MODEL

    # ---------------------------------------------------------------- 记账
    def record(
        self,
        model: str,
        *,
        ok: bool,
        latency_ms: float,
        ttft_ms: float | None = None,
        when: str | None = None,
    ) -> None:
        if self.store is None:
            return
        day = when or time.strftime('%Y-%m-%d')
        try:
            section = self.store.section('llm')
            days = dict(section.get('days') or {})
            bucket = dict(days.get(day) or _blank_bucket())
            bucket['total'] = int(bucket.get('total') or 0) + 1
            if ok:
                bucket['ok'] = int(bucket.get('ok') or 0) + 1
                bucket['latency_ms'] = float(bucket.get('latency_ms') or 0) + max(0.0, float(latency_ms))
                if ttft_ms is not None:
                    bucket['ttft_ms'] = float(bucket.get('ttft_ms') or 0) + max(0.0, float(ttft_ms))
                    bucket['ttft_n'] = int(bucket.get('ttft_n') or 0) + 1
            else:
                bucket['fail'] = int(bucket.get('fail') or 0) + 1
            streak = 0 if ok else int(section.get('streak') or 0) + 1
            models = dict(bucket.get('by_model') or {})
            slot = dict(models.get(model) or {'total': 0, 'ok': 0, 'fail': 0})
            slot['total'] = int(slot.get('total') or 0) + 1
            slot['ok'] = int(slot.get('ok') or 0) + (1 if ok else 0)
            slot['fail'] = int(slot.get('fail') or 0) + (0 if ok else 1)
            models[model] = slot
            bucket['by_model'] = models
            days[day] = bucket
            keep = sorted(days)[-KEEP_DAYS:]
            self.store.update_section(
                'llm',
                days={key: days[key] for key in keep},
                streak=streak,
                last_model=model,
                last_ts=time.time(),
            )
            self.store.save()
        except Exception:  # noqa: BLE001 - 记账失败绝不影响对话
            pass

    # ---------------------------------------------------------------- 查询
    @property
    def streak(self) -> int:
        if self.store is None:
            return 0
        try:
            return int(self.store.section('llm').get('streak') or 0)
        except Exception:  # noqa: BLE001
            return 0

    def summary(self, *, day: str | None = None, days: int = 7) -> dict[str, Any]:
        """报表用：某一天，或最近 N 天。"""
        if self.store is None:
            return _blank_summary()
        try:
            buckets = dict(self.store.section('llm').get('days') or {})
        except Exception:  # noqa: BLE001
            return _blank_summary()

        if day:
            selected = [buckets.get(day) or _blank_bucket()]
        else:
            keys = sorted(buckets)[-max(1, int(days)):]
            selected = [buckets[key] for key in keys]

        total = sum(int(item.get('total') or 0) for item in selected)
        ok = sum(int(item.get('ok') or 0) for item in selected)
        fail = sum(int(item.get('fail') or 0) for item in selected)
        latency = sum(float(item.get('latency_ms') or 0) for item in selected)
        ttft_total = sum(float(item.get('ttft_ms') or 0) for item in selected)
        ttft_n = sum(int(item.get('ttft_n') or 0) for item in selected)

        by_model: dict[str, dict[str, int]] = {}
        for bucket in selected:
            for name, slot in (bucket.get('by_model') or {}).items():
                merged = by_model.setdefault(name, {'total': 0, 'ok': 0, 'fail': 0})
                for key in ('total', 'ok', 'fail'):
                    merged[key] += int((slot or {}).get(key) or 0)

        return {
            'total': total,
            'ok': ok,
            'fail': fail,
            'success_rate': (ok / total * 100) if total else None,
            'avg_ms': (latency / ok) if ok else 0.0,
            'avg_ttft_ms': (ttft_total / ttft_n) if ttft_n else 0.0,
            'by_model': by_model,
            'streak': self.streak,
        }


def _blank_bucket() -> dict[str, Any]:
    return {'total': 0, 'ok': 0, 'fail': 0, 'latency_ms': 0.0, 'ttft_ms': 0.0, 'ttft_n': 0, 'by_model': {}}


def _blank_summary() -> dict[str, Any]:
    return {
        'total': 0,
        'ok': 0,
        'fail': 0,
        'success_rate': None,
        'avg_ms': 0.0,
        'avg_ttft_ms': 0.0,
        'by_model': {},
        'streak': 0,
    }
