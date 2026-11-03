# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""服务可用性监测：五类探测 + 一个纯判定的状态机。

支持的目标类型（与上游一致）：网站/接口、端口、Docker 容器、systemd 服务、进程。

两条从上游继承的结论，值得在这里写清楚：

1. **探测失败 ≠ 服务异常**。配置写错（容器名拼错、unit 不存在）和「服务真的
   挂了」必须分开：前者推给群里只会让人把告警静音。
2. **连续失败才算数**。单次抖动不该打扰任何人，`fail_threshold` 默认 2 次。

判定部分是纯函数（`evaluate_monitor`），探测部分是异步 I/O。拆开是为了让
「连续失败几次该报警、冷却多久、恢复怎么算」能脱离网络单测。
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import datetime
from typing import Any, Sequence

__all__ = [
    'CONFIG_ERROR_PREFIX',
    'evaluate_monitor',
    'normalize_items',
    'parse_silence',
    'probe',
    'should_silence',
]

CONFIG_ERROR_PREFIX = '配置错误：'

KINDS = ('http', 'tcp', 'docker', 'systemd', 'process')

_KIND_LABELS = {
    'http': '网站',
    'tcp': '端口',
    'docker': 'Docker 容器',
    'systemd': '系统服务',
    'process': '进程',
}

#: 这些字样意味着「你填的东西不存在」，不是「它挂了」
_CONFIG_ERROR_HINTS = (
    'no such container',
    'no such object',
    'not found',
    'not-found',
    'cannot connect to the docker daemon',
    'permission denied while trying to connect',
    'unit not found',
    'failed to connect to bus',
)


def looks_like_config_error(text: str) -> bool:
    lowered = str(text or '').lower()
    return any(hint in lowered for hint in _CONFIG_ERROR_HINTS)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def normalize_items(raw: Any) -> list[dict[str, Any]]:
    """把面板/配置文件里的监测项整理成统一结构。

    容忍几种历史写法：`type`/`kind`、`unit`/`service`、`pattern`/`keyword`。
    整理不了的条目**保留但标记 `invalid`**，这样诊断命令能把它列出来，而不是
    启动时无声无息地少了一项。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get('type') or entry.get('kind') or '').strip().lower()
        name = str(entry.get('name') or '').strip()
        item = {
            'name': name or _KIND_LABELS.get(kind, '未命名'),
            'kind': kind,
            'enabled': bool(entry.get('enabled', True)),
            'timeout': _clamp(entry.get('timeout_seconds', entry.get('timeout', 10)), 1, 60, 10),
            'expected_status': _clamp(entry.get('expected_status', 0), 0, 599, 0),
            'url': str(entry.get('url') or '').strip(),
            'keyword': str(entry.get('keyword') or '').strip(),
            'host': str(entry.get('host') or '127.0.0.1').strip(),
            'port': _int(entry.get('port', 0), 0),
            'container': str(entry.get('container') or '').strip(),
            'unit': str(entry.get('unit') or entry.get('service') or '').strip(),
            'pattern': str(entry.get('pattern') or entry.get('match') or '').strip(),
            'suppress_when_down': str(entry.get('suppress_when_down') or '').strip(),
        }
        reason = _invalid_reason(item)
        item['invalid'] = reason
        key = f'{item["kind"]}:{item["name"]}'
        if key in seen:
            # 同名项共用同一个状态键，重复探测只会互相覆盖。保留第一条，
            # 在它身上记下被忽略的条数，诊断命令据此提示。
            for kept in out:
                if f'{kept["kind"]}:{kept["name"]}' == key:
                    kept['duplicates'] = int(kept.get('duplicates') or 0) + 1
                    break
            continue
        seen.add(key)
        out.append(item)
    return out


def _invalid_reason(item: dict[str, Any]) -> str:
    kind = item['kind']
    if kind not in KINDS:
        return f'未知类型 `{kind}`'
    if kind == 'http' and not item['url']:
        return '缺少网址'
    if kind == 'tcp' and not (1 <= int(item['port']) <= 65535):
        return '端口未填或超出 1–65535'
    if kind == 'docker' and not item['container']:
        return '缺少容器名'
    if kind == 'systemd' and not item['unit']:
        return '缺少服务名'
    if kind == 'process' and not item['pattern']:
        return '缺少进程匹配串'
    return ''


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _int(value: Any, default: int) -> int:
    """只做类型转换，不做范围裁剪。

    端口这类字段**不能** clamp：把 70000 悄悄变成 65535 会让一个填错的监测项
    看起来完全正常，然后在「连不上」上浪费一晚上。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_silence(text: str) -> datetime | None:
    """`YYYY-MM-DD HH:MM` → datetime。解析不了就当作没配置静默。"""
    raw = str(text or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def should_silence(until_text: str, *, now: datetime | None = None) -> bool:
    until = parse_silence(until_text)
    if until is None:
        return False
    return (now or datetime.now()) < until


# ---------------------------------------------------------------------------
# 判定（纯逻辑）
# ---------------------------------------------------------------------------


def evaluate_monitor(
    state: dict[str, Any] | None,
    ok: bool,
    *,
    fail_threshold: int = 2,
    cooldown_seconds: float = 1800.0,
    now: float | None = None,
) -> dict[str, Any]:
    """单个监测项的状态机。

    与资源告警的关键差别：**失败次数够了就报，不要求持续超限**。服务探测的
    `ok=False` 本身就是一个离散事实，不需要「连续 N 次在阈值以上」的概念。
    """
    now = time.time() if now is None else float(now)
    st = dict(state or {})
    fails = int(st.get('fails') or 0)
    alerting = bool(st.get('alerting'))
    last_sent = float(st.get('last_sent') or 0.0)
    alert_since = float(st.get('alert_since') or 0.0)
    threshold = max(1, int(fail_threshold))

    alert = False
    recovery = False
    duration = 0.0

    if ok:
        fails = 0
        if alerting:
            recovery = True
            alerting = False
            duration = max(0.0, now - alert_since) if alert_since else 0.0
            alert_since = 0.0
    else:
        fails += 1
        cooling = last_sent > 0 and (now - last_sent) < max(0.0, float(cooldown_seconds))
        if not alerting and fails >= threshold and not cooling:
            alert = True
            alerting = True
            alert_since = now
            last_sent = now

    st.update({'fails': fails, 'alerting': alerting, 'last_sent': last_sent, 'alert_since': alert_since})
    return {'state': st, 'alert': alert, 'recovery': recovery, 'duration': duration, 'fails': fails}


# ---------------------------------------------------------------------------
# 探测（异步 I/O）
# ---------------------------------------------------------------------------


async def probe(
    item: dict[str, Any],
    *,
    max_response_bytes: int = 262144,
) -> dict[str, Any]:
    """探测一个目标，返回 `{ok, detail, target, config_error}`。永不抛异常。"""
    kind = str(item.get('kind') or '')
    name = str(item.get('name') or '')
    timeout = max(1, min(60, int(item.get('timeout') or 10)))
    if item.get('invalid'):
        return {'ok': False, 'detail': f'{CONFIG_ERROR_PREFIX}{item["invalid"]}', 'target': name, 'config_error': True}

    try:
        if kind == 'http':
            return await _probe_http(item, timeout, max_response_bytes)
        if kind == 'tcp':
            return await _probe_tcp(item, timeout)
        if kind == 'docker':
            return await _probe_docker(item, timeout)
        if kind == 'systemd':
            return await _probe_systemd(item, timeout)
        if kind == 'process':
            return await _probe_process(item)
    except Exception as exc:  # noqa: BLE001 - 探测失败是一种结果，不是异常
        return {'ok': False, 'detail': f'{type(exc).__name__}: {exc}', 'target': name, 'config_error': False}
    return {'ok': False, 'detail': f'未知类型 {kind}', 'target': name, 'config_error': True}


async def _probe_http(item: dict[str, Any], timeout: int, max_bytes: int) -> dict[str, Any]:
    import httpx

    url = str(item.get('url') or '')
    expected = int(item.get('expected_status') or 0)
    keyword = str(item.get('keyword') or '').strip()
    started = time.perf_counter()

    async def run() -> dict[str, Any]:
        # 期望 3xx 时不能跟随重定向，否则拿到的是最终 200，永远对不上
        follow = not (300 <= expected < 400)
        async with httpx.AsyncClient(follow_redirects=follow, timeout=timeout) as client:
            async with client.stream('GET', url) as response:
                status = response.status_code
                if expected and status != expected:
                    return {'ok': False, 'detail': f'HTTP {status}（期望 {expected}）', 'target': _safe_target(url), 'config_error': False}
                if not expected and status >= 400:
                    return {'ok': False, 'detail': f'HTTP {status}', 'target': _safe_target(url), 'config_error': False}
                if keyword:
                    limit = max(1024, min(10 * 1024 * 1024, int(max_bytes)))
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) >= limit:
                            break
                    text = bytes(body[:limit]).decode(response.charset_encoding or 'utf-8', errors='replace')
                    if keyword not in text:
                        return {'ok': False, 'detail': '响应中未找到关键字', 'target': _safe_target(url), 'config_error': False}
                elapsed = (time.perf_counter() - started) * 1000
                return {
                    'ok': True,
                    'detail': f'HTTP {status}（{elapsed:.0f}ms）',
                    'target': _safe_target(url),
                    'config_error': False,
                    'delay_ms': elapsed,
                }

    try:
        return await asyncio.wait_for(run(), timeout=timeout + 2)
    except (TimeoutError, asyncio.TimeoutError):
        return {'ok': False, 'detail': f'探测超时（超过 {timeout + 2} 秒）', 'target': _safe_target(url), 'config_error': False}


def _safe_target(url: str) -> str:
    """去掉查询串与 `user:pass@`，避免把 API Key 写进库和群消息。"""
    text = str(url or '')
    for separator in ('?', '#'):
        text = text.split(separator, 1)[0]
    if '://' in text:
        scheme, _, rest = text.partition('://')
        if '@' in rest:
            rest = rest.split('@', 1)[1]
        return f'{scheme}://{rest}'
    return text


async def _probe_tcp(item: dict[str, Any], timeout: int) -> dict[str, Any]:
    host = str(item.get('host') or '127.0.0.1')
    port = int(item.get('port') or 0)
    target = f'{host}:{port}'
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - 关连接时的异常不影响「连上了」这个结论
            pass
        return {'ok': True, 'detail': f'TCP {target} 连接成功', 'target': target, 'config_error': False}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'detail': f'{type(exc).__name__}: {exc}', 'target': target, 'config_error': False}


async def _probe_docker(item: dict[str, Any], timeout: int) -> dict[str, Any]:
    container = str(item.get('container') or '')
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ['docker', 'inspect', '--format', '{{.State.Running}}', container],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {'ok': False, 'detail': f'{CONFIG_ERROR_PREFIX}没有 docker 命令', 'target': container, 'config_error': True}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'detail': f'docker inspect 超时（{timeout} 秒）', 'target': container, 'config_error': False}

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or 'docker inspect 失败').strip()
        if looks_like_config_error(detail):
            return {'ok': False, 'detail': f'{CONFIG_ERROR_PREFIX}{detail[:180]}', 'target': container, 'config_error': True}
        return {'ok': False, 'detail': detail[:200], 'target': container, 'config_error': False}
    running = proc.stdout.strip().lower() == 'true'
    return {'ok': running, 'detail': f'容器 running={str(running).lower()}', 'target': container, 'config_error': False}


async def _probe_systemd(item: dict[str, Any], timeout: int) -> dict[str, Any]:
    unit = str(item.get('unit') or '')
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ['systemctl', 'is-active', unit],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {'ok': False, 'detail': f'{CONFIG_ERROR_PREFIX}没有 systemctl', 'target': unit, 'config_error': True}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'detail': f'systemctl 超时（{timeout} 秒）', 'target': unit, 'config_error': False}

    state_text = (proc.stdout or proc.stderr or 'unknown').strip()
    if state_text != 'active':
        # is-active 对「停了的服务」和「根本不存在的 unit」都回 inactive，
        # 只有 LoadState 能区分拼错和真故障
        try:
            show = await asyncio.to_thread(
                subprocess.run,
                ['systemctl', 'show', '-p', 'LoadState', '--value', unit],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if (show.stdout or '').strip() == 'not-found':
                return {
                    'ok': False,
                    'detail': f'{CONFIG_ERROR_PREFIX}{unit}: unit 不存在（LoadState=not-found）',
                    'target': unit,
                    'config_error': True,
                }
        except Exception:  # noqa: BLE001
            pass
    return {
        'ok': state_text == 'active',
        'detail': f'{unit}: {state_text}',
        'target': unit,
        'config_error': False,
    }


async def _probe_process(item: dict[str, Any]) -> dict[str, Any]:
    import psutil

    pattern = str(item.get('pattern') or '').strip().lower()
    target = pattern

    def scan() -> dict[str, Any]:
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                name = str((proc.info or {}).get('name') or '')
                cmdline = ' '.join((proc.info or {}).get('cmdline') or [])
                if pattern in f'{name} {cmdline}'.lower():
                    return {'ok': True, 'detail': f'匹配到进程 {name or proc.pid}', 'target': target, 'config_error': False}
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {'ok': False, 'detail': f'未找到匹配进程：{pattern}', 'target': target, 'config_error': False}

    return await asyncio.to_thread(scan)
