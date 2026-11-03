# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""采集层：psutil 与系统命令，产出 `status_logic.build_view()` 要的原始快照。

这一层不 import nonebot，也不碰 OneBot——QQ 侧的数据（好友、群、收发）在接线层
用 `bot.call_api` 拿，因为那需要活的 bot 实例。这里只管**本机**。

几个刻意的口径（都来自上游踩过的坑，写在这里免得后人又踩一遍）：

- **磁盘百分比用 `已用 / 总容量`**，不用 `psutil.disk_usage().percent`。后者把
  ext4 的 5% 保留块算成可用，同一块盘会虚高 2–3 个点，导致「卡上说 80%，告警
  却按 83% 算」这种对不上的现象。
- **进程 CPU 要先预热**：`cpu_percent()` 第一次调用恒为 0，不预热的话第一张卡
  的进程表全是 0.0%。
- **网卡只选有流量且在线的物理口**：`lo` 会永远为零，docker0 会跟着容器抖动，
  选错了卡片上的折线就是一条直线。
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import psutil
except ModuleNotFoundError as exc:  # pragma: no cover - 部署缺依赖时报清楚
    raise ModuleNotFoundError('状态卡需要 psutil，请先 pip install psutil') from exc

__all__ = [
    'boot_time',
    'cpu_snapshot',
    'disk_io_rates',
    'disk_snapshot',
    'memory_snapshot',
    'net_counters',
    'net_rates',
    'pick_interface',
    'process_snapshot',
    'prime_process_cpu',
    'swap_snapshot',
    'system_rows',
    'uptime_seconds',
]


# ---------------------------------------------------------------------------
# 时间与基础
# ---------------------------------------------------------------------------


def boot_time() -> float:
    try:
        return float(psutil.boot_time())
    except Exception:  # noqa: BLE001 - 拿不到就退化成进程启动时间
        return time.time()


def uptime_seconds(now: float | None = None) -> float:
    now = time.time() if now is None else float(now)
    return max(0.0, now - boot_time())


# ---------------------------------------------------------------------------
# CPU / 内存 / 交换
# ---------------------------------------------------------------------------

_CPU_BRAND: str | None = None


def cpu_brand() -> str:
    """CPU 型号。Linux 读 /proc/cpuinfo，其余平台退化成 platform.processor()。"""
    global _CPU_BRAND
    if _CPU_BRAND is not None:
        return _CPU_BRAND
    brand = ''
    try:
        text = Path('/proc/cpuinfo').read_text(encoding='utf-8', errors='ignore')
        for line in text.splitlines():
            if line.lower().startswith('model name'):
                brand = line.split(':', 1)[1].strip()
                break
    except OSError:
        brand = ''
    _CPU_BRAND = brand or platform.processor() or platform.machine() or 'CPU'
    return _CPU_BRAND


def prime_cpu() -> None:
    """让 `cpu_percent(None)` 有个基准点，否则第一次读数恒为 0。"""
    try:
        psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        pass


def cpu_snapshot() -> dict[str, Any]:
    try:
        percent = float(psutil.cpu_percent(interval=None) or 0.0)
    except Exception:  # noqa: BLE001
        percent = 0.0
    try:
        freq = psutil.cpu_freq()
        freq_mhz = float(freq.current) if freq else 0.0
    except Exception:  # noqa: BLE001
        freq_mhz = 0.0
    return {
        'percent': percent,
        'cores': psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
        'threads': psutil.cpu_count(logical=True) or 0,
        'brand': cpu_brand(),
        'freq_mhz': freq_mhz,
    }


def _mem_like(raw: Any) -> dict[str, Any]:
    total = int(getattr(raw, 'total', 0) or 0)
    used = int(getattr(raw, 'used', 0) or 0)
    # 与磁盘同口径：自己算百分比，不用库给的，保证卡片、告警、日报三处一致
    percent = (used / total * 100.0) if total else 0.0
    return {'total': total, 'used': used, 'percent': percent}


def memory_snapshot() -> dict[str, Any]:
    try:
        return _mem_like(psutil.virtual_memory())
    except Exception:  # noqa: BLE001
        return {'total': 0, 'used': 0, 'percent': 0.0}


def swap_snapshot() -> dict[str, Any]:
    try:
        return _mem_like(psutil.swap_memory())
    except Exception:  # noqa: BLE001
        return {'total': 0, 'used': 0, 'percent': 0.0}


# ---------------------------------------------------------------------------
# 磁盘
# ---------------------------------------------------------------------------


def default_disk_paths() -> list[str]:
    """默认看根分区。Windows 上取系统盘。"""
    if os.name == 'nt':
        drive = os.environ.get('SystemDrive') or 'C:'
        return [f'{drive}\\']
    return ['/']


def disk_snapshot(paths: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """逐块盘取用量。读不到的盘保留一条带 error 的记录，卡片上会标明。"""
    wanted = [str(path).strip() for path in (paths or default_disk_paths()) if str(path).strip()]
    seen: list[str] = []
    out: list[dict[str, Any]] = []
    for path in wanted:
        if path in seen:
            continue
        seen.append(path)
        try:
            usage = psutil.disk_usage(path)
            total = int(usage.total)
            used = int(usage.used)
            percent = (used / total * 100.0) if total else 0.0
            out.append({'path': path, 'total': total, 'used': used, 'percent': percent})
        except Exception as exc:  # noqa: BLE001 - 挂载点没了不该让整张卡失败
            out.append({'path': path, 'total': 0, 'used': 0, 'percent': None, 'error': str(exc)})
    return out


def disk_io_counters() -> dict[str, float]:
    """全盘读写累计字节。"""
    try:
        counters = psutil.disk_io_counters()
        if counters is None:
            return {'read': 0.0, 'write': 0.0}
        return {'read': float(counters.read_bytes), 'write': float(counters.write_bytes)}
    except Exception:  # noqa: BLE001
        return {'read': 0.0, 'write': 0.0}


def disk_io_rates(
    previous: dict[str, float] | None,
    current: dict[str, float] | None,
    elapsed: float,
) -> tuple[float, float]:
    """两次磁盘计数 → (读速率, 写速率)。与网络同样处理时钟回拨。"""
    if not previous or not current:
        return 0.0, 0.0
    span = max(0.0, float(elapsed))
    if span <= 0:
        return 0.0, 0.0
    read = float(current.get('read', 0)) - float(previous.get('read', 0))
    write = float(current.get('write', 0)) - float(previous.get('write', 0))
    if read < 0 or write < 0:
        return 0.0, 0.0
    return read / span, write / span


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------

_VIRTUAL_PREFIXES = ('lo', 'docker', 'veth', 'br-', 'virbr', 'tun', 'tap', 'wg', 'zt', 'tailscale')


def pick_interface(preferred: str | None = None) -> str:
    """挑一块网卡。

    顺序：显式指定 → 有流量且非虚拟 → 有流量 → 第一个非虚拟 → 空。
    只看「第一个网卡」在多网卡机器上经常选中 `lo`，折线就成了一条零线。
    """
    if preferred:
        return str(preferred)
    try:
        counters = psutil.net_io_counters(pernic=True) or {}
        stats = psutil.net_if_stats() or {}
    except Exception:  # noqa: BLE001
        return ''

    def is_virtual(name: str) -> bool:
        lowered = name.lower()
        return any(lowered.startswith(prefix) for prefix in _VIRTUAL_PREFIXES)

    candidates = []
    for name, counter in counters.items():
        up = getattr(stats.get(name), 'isup', True)
        traffic = int(getattr(counter, 'bytes_sent', 0)) + int(getattr(counter, 'bytes_recv', 0))
        candidates.append((name, up, traffic, is_virtual(name)))

    for name, up, traffic, virtual in candidates:
        if up and traffic > 0 and not virtual:
            return name
    for name, up, traffic, virtual in candidates:
        if traffic > 0 and not virtual:
            return name
    for name, up, traffic, virtual in candidates:
        if not virtual:
            return name
    return candidates[0][0] if candidates else ''


def net_counters(interface: str | None = None) -> dict[str, float]:
    """网卡累计收发字节。指定网卡取不到时退回全系统累计。"""
    try:
        if interface:
            per_nic = psutil.net_io_counters(pernic=True) or {}
            counter = per_nic.get(interface)
            if counter is not None:
                return {'sent': float(counter.bytes_sent), 'recv': float(counter.bytes_recv)}
        total = psutil.net_io_counters()
        if total is None:
            return {'sent': 0.0, 'recv': 0.0}
        return {'sent': float(total.bytes_sent), 'recv': float(total.bytes_recv)}
    except Exception:  # noqa: BLE001
        return {'sent': 0.0, 'recv': 0.0}


def net_rates(
    previous: dict[str, float] | None,
    current: dict[str, float] | None,
    elapsed: float,
) -> tuple[float, float]:
    if not previous or not current:
        return 0.0, 0.0
    span = max(0.0, float(elapsed))
    if span <= 0:
        return 0.0, 0.0
    up = float(current.get('sent', 0)) - float(previous.get('sent', 0))
    down = float(current.get('recv', 0)) - float(previous.get('recv', 0))
    if up < 0 or down < 0:
        return 0.0, 0.0
    return up / span, down / span


# ---------------------------------------------------------------------------
# 进程
# ---------------------------------------------------------------------------

#: 进程 CPU 基线：key -> (采样时刻, 累计 CPU 秒数)
#:
#: 不用 `Process.cpu_percent()`：它会为每个进程单独开一次句柄，Windows 上 600 个
#: 进程要 20 秒，采样循环直接卡死。改成自己记基线算差值，一次遍历搞定。
_PROC_BASELINE: dict[tuple[int, float], tuple[float, float]] = {}
_PROC_BASELINE_TTL = 600.0

#: 最近一次完整扫描的结果。Windows 上 600 个进程逐个开句柄要 8 秒级（Linux 上
#: 是 0.1 秒级），出图时再扫一遍会让「查看状态」卡住；采样循环本来就在定时扫，
#: 干脆复用它的结果。
_PROC_CACHE: dict[str, Any] = {'ts': 0.0, 'data': None}
_PROC_CACHE_TTL = 30.0


def _process_key(info: dict[str, Any]) -> tuple[int, float]:
    try:
        return int(info.get('pid') or 0), float(info.get('create_time') or 0.0)
    except (TypeError, ValueError):
        return 0, 0.0


def _cpu_total(cpu_times: Any) -> float:
    if cpu_times is None:
        return 0.0
    return (
        float(getattr(cpu_times, 'user', 0.0) or 0.0)
        + float(getattr(cpu_times, 'system', 0.0) or 0.0)
    )


def prime_process_cpu() -> None:
    """打一次基线。之后每个进程的 CPU 占用都由「累计时间差 / 实际间隔」算出。"""
    process_snapshot(1)


def process_snapshot(top_n: int = 8, *, max_age: float = 0.0) -> dict[str, Any]:
    """进程总数、运行中数量、整机 CPU 占用最高的前 N 个。

    `cpu` 是**单核口径**（一个核跑满 = 100%），`cpu_pct_total` 是整机口径
    （全部核跑满 = 100%）。卡片和报表都展示整机口径：20 核机器上显示 743%
    没人看得出到底多忙。

    `max_age > 0` 时复用该秒数内的上一次扫描结果。卡片和面板都走这条路径：
    进程榜晚 30 秒没人会计较，但让「查看状态」等 8 秒区别很大。
    """
    top_n = max(1, min(30, int(top_n)))
    now = time.monotonic()
    cached = _PROC_CACHE.get('data')
    if max_age > 0 and cached and now - float(_PROC_CACHE.get('ts') or 0.0) <= max_age:
        return {
            'total': cached.get('total'),
            'running': cached.get('running'),
            'top': (cached.get('top') or [])[:top_n],
            'cached': True,
        }
    cores = psutil.cpu_count(logical=True) or 1
    entries: list[dict[str, Any]] = []
    total = 0
    running = 0

    try:
        iterator = psutil.process_iter(['pid', 'name', 'memory_percent', 'status', 'cpu_times', 'create_time'])
    except Exception:  # noqa: BLE001
        return {'total': None, 'running': None, 'top': []}

    seen: set[tuple[int, float]] = set()
    for proc in iterator:
        info = getattr(proc, 'info', None) or {}
        total += 1
        if str(info.get('status') or '') == 'running':
            running += 1
        key = _process_key(info)
        seen.add(key)
        cpu_seconds = _cpu_total(info.get('cpu_times'))
        previous = _PROC_BASELINE.get(key)
        _PROC_BASELINE[key] = (now, cpu_seconds)
        percent = 0.0
        if previous is not None:
            elapsed = now - previous[0]
            delta = cpu_seconds - previous[1]
            # 计数器回退（进程刚 fork、系统时间校正）按 0 处理，不出现负占用
            if elapsed > 0 and delta > 0:
                percent = min(100.0 * cores, delta / elapsed * 100.0)
        entries.append({
            'pid': key[0],
            'name': str(info.get('name') or f'pid {key[0]}'),
            'cpu': percent,
            'cpu_pct_total': percent / cores,
            'mem': float(info.get('memory_percent') or 0.0),
        })

    # 已退出的进程从基线里清掉，长期运行才不会让字典一直涨
    for key in [item for item in _PROC_BASELINE if item not in seen]:
        _PROC_BASELINE.pop(key, None)

    entries.sort(key=lambda item: (-item['cpu'], -item['mem']))
    result = {'total': total, 'running': running, 'top': entries[:top_n]}
    _PROC_CACHE['ts'] = now
    _PROC_CACHE['data'] = {'total': total, 'running': running, 'top': entries[:max(top_n, 10)]}
    return result


# ---------------------------------------------------------------------------
# 系统信息
# ---------------------------------------------------------------------------


def _os_name() -> str:
    try:
        text = Path('/etc/os-release').read_text(encoding='utf-8')
        data = {}
        for line in text.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                data[key.strip()] = value.strip().strip('"')
        if data.get('PRETTY_NAME'):
            return data['PRETTY_NAME']
    except OSError:
        pass
    return f'{platform.system()} {platform.release()}'


def _load_average() -> str:
    try:
        load1, load5, load15 = os.getloadavg()
        return f'{load1:.2f} / {load5:.2f} / {load15:.2f}'
    except (AttributeError, OSError):
        return '--'


def load_per_core() -> tuple[float, float, int] | None:
    """(1 分钟负载, 每核负载, 核数)。Windows 没有 getloadavg，返回 None。

    告警判定要的是「每核负载」：8 核机器上 load=4 很轻松，2 核机器上就已经
    排满队了，只比 load 绝对值会漏报一半的机器。
    """
    if not hasattr(os, 'getloadavg'):
        return None
    try:
        load1 = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        return None
    cores = psutil.cpu_count() or 1
    return load1, load1 / max(1, cores), cores


def system_rows(*, tz_offset: int = 8, now: float | None = None) -> list[dict[str, str]]:
    """卡片「系统信息」面板的行。两列排版，六行刚好。"""
    now = time.time() if now is None else float(now)
    booted = datetime.fromtimestamp(boot_time()) + timedelta(hours=tz_offset)
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001
        host = '--'
    return [
        {'label': '主机名', 'value': host},
        {'label': '系统', 'value': _os_name()},
        {'label': '内核', 'value': platform.release() or '--'},
        {'label': 'Python', 'value': platform.python_version()},
        {'label': '开机', 'value': booted.strftime('%Y-%m-%d %H:%M')},
        {'label': '负载', 'value': _load_average()},
    ]


def cgroup_memory_limit() -> int | None:
    """容器里的内存上限。

    psutil 读的是宿主机的 `/proc/meminfo`，1GB 限额的容器跑在 3.6GB 宿主机上时，
    卡片会显示「内存只用 30%」而实际上已经顶格。诊断命令把这个值单独列出来，
    不改动卡片数字——改数字等于在卡片上撒一个没人能复核的谎。
    """
    for path in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            text = Path(path).read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if not text or text == 'max':
            continue
        try:
            value = int(text)
        except ValueError:
            continue
        if 0 < value < (1 << 60):
            return value
    return None


def process_uptime() -> float:
    """机器人进程自身跑了多久。和「开机时长」是两件事，诊断里分开显示。"""
    try:
        proc = psutil.Process(os.getpid())
        return max(0.0, time.time() - float(proc.create_time()))
    except Exception:  # noqa: BLE001
        return 0.0


_PANEL_PRIMED = False


def panel_snapshot(*, top_n: int = 6, disk_paths: Iterable[str] | None = None) -> dict[str, Any]:
    """管理面板用的一次性快照。

    面板是独立进程，没有启动钩子去预热 `cpu_percent(None)`，直接读会永远拿到
    0.0%。这里第一次调用先预热再短等一小会儿，之后读的就是真实值。

    这是同步函数，调用方应该丢进线程里跑（FastAPI 的 def 路由本来就在线程池）。
    """
    global _PANEL_PRIMED
    if not _PANEL_PRIMED:
        prime_cpu()
        time.sleep(0.15)
        _PANEL_PRIMED = True
    load = load_per_core()
    return {
        'cpu': cpu_snapshot(),
        'memory': memory_snapshot(),
        'swap': swap_snapshot(),
        'disks': disk_snapshot(disk_paths),
        'process': process_snapshot(top_n, max_age=_PROC_CACHE_TTL),
        'load': f'{load[0]:.2f} / {load[2]} 核' if load else '--',
        'uptime': uptime_seconds(),
        'collected_at': time.time(),
    }
