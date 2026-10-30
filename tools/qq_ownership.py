"""qq_ownership.py —— 不注入、只读地判断「哪个 QQ 进程登录了哪个号」。

为什么需要它：SnowLuma 的注入粒度是 PID，而它自带的 probe-login 渠道
（ptlocal 的 pt_get_uins）读的是 %APPDATA%\\QQ 下共用的「本机记住的号」列表。
QQNT 多开时这条渠道会给出错的答案：两个实例都报同一个号，还会间歇返回空列表。
所以账号身份要靠**文件句柄归属**来认：

  QQ 登录后会持续持有该账号 CEF 分区（%APPDATA%\\QQ\\Partitions\\qqnt_<uin>）里的
  leveldb/LOCK、Cookies、DIPS-wal 等文件句柄。用 NtQuerySystemInformation
  把全系统句柄过一遍，筛出路径落在某个 qqnt_<uin> 分区里的，按 QQ.exe 进程树
  折算回主进程 —— 这就是「这个 PID 登录了这个号」的硬证据，全程不注入、不写盘。

附带还算一条弱证据：实例自己的分区叫 Partitions/qqnt_<本机端口>，
端口能由 netstat 精确归属到 PID，可用来交叉验证「一 PID 一实例」。

用法：
    python qq_ownership.py            # 人读
    python qq_ownership.py --json     # 机器读
    python qq_ownership.py --uin 3892894069   # 只问：哪个 PID 登录了这个号
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as W
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
PARTITIONS = APPDATA / "QQ" / "Partitions"

STATUS_SUCCESS = 0x00000000
STATUS_BUFFER_OVERFLOW = 0x80000005
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
PROCESS_DUP_HANDLE = 0x0040
QUERY_LIMITED_INFORMATION = 0x00100000
DUPLICATE_SAME_ACCESS = 0x2
OBJECT_NAME_INFORMATION = 1
OBJECT_TYPE_INFORMATION = 2
# SystemExtendedHandleInformation(64)：句柄值/PID 都是完整指针宽度。
# 老的 16 号在 x64 上把 HandleValue 截成 USHORT，拿到的表全是错位的垃圾。
SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# 句柄是指针宽度，不声明 restype 会被截成 int
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.GetCurrentProcess.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", W.USHORT), ("MaximumLength", W.USHORT), ("Buffer", ctypes.c_void_p)]


class SYSTEM_HANDLE_ENTRY(ctypes.Structure):
    """SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX（配 64 号信息类用）。"""
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_size_t),
        ("HandleValue", ctypes.c_size_t),
        ("GrantedAccess", W.ULONG),
        ("CreatorBackTraceIndex", W.USHORT),
        ("ObjectTypeIndex", W.USHORT),
        ("HandleAttributes", W.ULONG),
        ("Reserved", W.ULONG),
    ]


class SYSTEM_HANDLE_INFO(ctypes.Structure):
    _fields_ = [
        ("NumberOfHandles", ctypes.c_size_t),
        ("Reserved", ctypes.c_size_t),
        ("Handles", SYSTEM_HANDLE_ENTRY * 1),
    ]


def _u16_str(p_addr: int, length: int) -> str:
    if not p_addr or length <= 0:
        return ""
    buf = ctypes.create_string_buffer(length)
    ctypes.memmove(buf, ctypes.c_void_p(p_addr), length)
    return buf.raw.decode("utf-16-le", "replace")


def object_name(handle: int, size: int = 4096) -> str:
    """ObjectHandle → 对象名（\\Device\\HarddiskVolumeN\\...）。

    缓冲区必须给够：拿 16 字节的 UNICODE_STRING 当缓冲区只会得到
    STATUS_BUFFER_OVERFLOW 且读不到内容。0x80000005 也当作可用结果，
    结构体已经填好，只是名字被截断。
    """
    buf = ctypes.create_string_buffer(size)
    got = W.ULONG()
    st = ntdll.NtQueryObject(ctypes.c_void_p(handle), OBJECT_NAME_INFORMATION,
                             buf, size, ctypes.byref(got)) & 0xFFFFFFFF
    if st not in (STATUS_SUCCESS, STATUS_BUFFER_OVERFLOW):
        return ""
    u = ctypes.cast(buf, ctypes.POINTER(UNICODE_STRING)).contents
    return _u16_str(u.Buffer or 0, u.Length)


def object_type(handle: int) -> str:
    """OBJECT_TYPE_INFORMATION 的头一个字段就是类型的 UNICODE_STRING。"""
    buf = ctypes.create_string_buffer(1024)
    got = W.ULONG()
    st = ntdll.NtQueryObject(ctypes.c_void_p(handle), OBJECT_TYPE_INFORMATION,
                             buf, 1024, ctypes.byref(got)) & 0xFFFFFFFF
    if st not in (STATUS_SUCCESS, STATUS_BUFFER_OVERFLOW):
        return ""
    u = ctypes.cast(buf, ctypes.POINTER(UNICODE_STRING)).contents
    return _u16_str(u.Buffer or 0, u.Length)


_table_cache: tuple[float, list[SYSTEM_HANDLE_ENTRY]] | None = None


def handle_table(*, refresh: bool = False) -> list[SYSTEM_HANDLE_ENTRY]:
    """全系统句柄表（x64 上 20 万条量级），60 秒内复用，别白扫。"""
    global _table_cache
    import time
    now = time.time()
    if not refresh and _table_cache and now - _table_cache[0] < 60:
        return _table_cache[1]
    size, res, buf = 1 << 20, STATUS_INFO_LENGTH_MISMATCH, None
    for _ in range(10):
        buf = ctypes.create_string_buffer(size)
        res = ntdll.NtQuerySystemInformation(
            SYSTEM_EXTENDED_HANDLE_INFORMATION, buf, size, None) & 0xFFFFFFFF
        if res == STATUS_SUCCESS:
            break
        size *= 2
    if res != STATUS_SUCCESS:
        raise OSError(0, f"NtQuerySystemInformation(64) 失败 0x{res:08X}")
    count = ctypes.cast(buf, ctypes.POINTER(ctypes.c_size_t)).contents.value
    base = ctypes.addressof(buf) + SYSTEM_HANDLE_INFO.Handles.offset
    arr = ctypes.cast(base, ctypes.POINTER(SYSTEM_HANDLE_ENTRY * count)).contents
    entries = [arr[i] for i in range(count)]
    _table_cache = (now, entries)
    return entries


def file_type_index() -> int:
    """拿一个自己进程里的真实文件句柄，反查 File 类型在句柄表里的索引。

    这个索引每次开机都可能不一样（本机实测 42），所以只能问出来，不能写死。
    """
    probe = kernel32.CreateFileW(str(Path(sys.executable)), GENERIC_READ, 0x7, None,
                                 OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, None)
    hv = int(probe or 0)
    if not hv or hv == 0xFFFFFFFFFFFFFFFF:
        raise OSError(f"CreateFileW 失败 err={ctypes.get_last_error()}")
    try:
        if object_type(hv) != "File":
            raise OSError("参考句柄类型不是 File")
        me = kernel32.GetCurrentProcessId()
        for e in handle_table(refresh=True):
            if e.UniqueProcessId == me and int(e.HandleValue or 0) == hv:
                return int(e.ObjectTypeIndex)
        raise OSError("在句柄表里没找到自己那个文件句柄")
    finally:
        kernel32.CloseHandle(hv)


kernel32.DuplicateHandle.argtypes = [
    ctypes.c_void_p,                         # 源进程
    ctypes.c_void_p,                         # 源句柄值
    ctypes.c_void_p,                         # 目标进程（用伪句柄 -1）
    ctypes.POINTER(ctypes.c_void_p),         # 出参
    W.DWORD, W.BOOL, W.DWORD,
]
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
CURRENT_PROCESS = ctypes.c_void_p(0xFFFFFFFFFFFFFFFF)   # GetCurrentProcess() 的伪句柄值
_proc_cache: dict[int, object] = {}


def _proc_handle(pid: int):
    """OpenProcess 很贵，同一个 PID 只开一次。-1 表示开不动（权限/保护进程）。"""
    if pid in _proc_cache:
        return _proc_cache[pid]
    h = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
    _proc_cache[pid] = h if h else None
    return _proc_cache[pid]


def dup(pid: int, handle_value: int):
    """把别的进程的一个句柄复制到本进程里，才能问它的名字。"""
    src = _proc_handle(pid)
    if not src:
        return None
    dst = ctypes.c_void_p()
    ok = kernel32.DuplicateHandle(src, ctypes.c_void_p(handle_value), CURRENT_PROCESS,
                                  ctypes.byref(dst), QUERY_LIMITED_INFORMATION, False,
                                  DUPLICATE_SAME_ACCESS)
    return dst.value if ok else None


# ---------------------------------------------------------------- 进程

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", W.DWORD),
        ("cntUsage", W.DWORD),
        ("th32ProcessID", W.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", W.DWORD),
        ("cntThreads", W.DWORD),
        ("th32ParentProcessID", W.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", W.DWORD),
        ("szExeFile", W.WCHAR * 260),
    ]


def all_processes() -> dict[int, tuple[int, str]]:
    """{pid: (ppid, 可执行文件名)}，用 Toolhelp 快照，不依赖 wmic（Win11 已删）。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if int(snap) == INVALID_HANDLE_VALUE:
        raise OSError(f"CreateToolhelp32Snapshot 失败 err={ctypes.get_last_error()}")
    try:
        res: dict[int, tuple[int, str]] = {}
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            res[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile)
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return res
    finally:
        kernel32.CloseHandle(snap)


def qq_processes() -> tuple[dict[int, int], set[int]]:
    """({pid: ppid, 只含 QQ.exe}, 主进程集合)。主进程 = 父进程不是 QQ.exe。"""
    procs = all_processes()
    par = {p: pp for p, (pp, name) in procs.items() if name.lower() == "qq.exe"}
    return par, {p for p in par if par.get(p, 0) not in par}


def main_of(pid: int, par: dict[int, int], mains: set[int]) -> int:
    seen = set()
    while pid not in mains and pid in par and pid not in seen:
        seen.add(pid)
        pid = par[pid]
    return pid if pid in mains else 0


def listening_ports() -> dict[int, set[int]]:
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    res: dict[int, set[int]] = defaultdict(set)
    for line in out.splitlines():
        f = line.split()
        if len(f) < 5 or f[0].upper() != "TCP" or f[3].upper() != "LISTENING":
            continue
        m = re.search(r":(\d+)$", f[1])
        if m and f[-1].isdigit():
            res[int(f[-1])].add(int(m.group(1)))
    return res


# ---------------------------------------------------------------- 判定

PART_RE = re.compile(r"Partitions[\\/]+qqnt_(\d{4,12})[\\/]", re.I)


def partition_hits() -> dict[int, dict[int, int]]:
    """{分区数字: {QQ pid: 句柄数}}，pid 含子进程。"""
    idx = file_type_index()
    par, mains = qq_processes()
    qq = set(par)
    hits: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for e in handle_table():
        if e.ObjectTypeIndex != idx or int(e.UniqueProcessId) not in qq:
            continue
        pid, hv = int(e.UniqueProcessId), int(e.HandleValue or 0)
        if not hv:
            continue
        h = dup(pid, hv)
        if not h:
            continue
        try:
            path = object_name(h).replace("\\??\\", "\\")
        finally:
            kernel32.CloseHandle(h)
        m = PART_RE.search(path)
        if m:
            hits[int(m.group(1))][pid] += 1
    return hits


def known_uins() -> set[str]:
    if not PARTITIONS.is_dir():
        return set()
    return {d.name[5:] for d in PARTITIONS.iterdir()
            if d.name.startswith("qqnt_") and d.name[5:].isdigit() and int(d.name[5:]) > 0xFFFF}


def resolve() -> dict[int, dict]:
    """{主进程 PID: {uin, why, inst_port, account_hits, uin_hits_all}}"""
    par, mains = qq_processes()
    ports = listening_ports()
    uins = known_uins()
    raw = partition_hits()

    per_main: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for num, owners in raw.items():
        for pid, n in owners.items():
            root = main_of(pid, par, mains)
            if root:
                per_main[root][num] += n

    out: dict[int, dict] = {}
    for pid in sorted(mains):
        ev = dict(per_main.get(pid, {}))
        inst = sorted(n for n in ev if n in ports.get(pid, set()))
        acct = {str(n): c for n, c in ev.items() if str(n) in uins}
        if len(acct) == 1:
            uin = next(iter(acct))
            why = f"账号分区 qqnt_{uin} 有 {acct[uin]} 个文件句柄被本进程树持有"
        elif len(acct) > 1:
            uin = ""
            why = f"同时命中多个账号分区 {acct}（多开串了，不敢认）"
        else:
            uin = ""
            why = f"没抓到账号分区句柄（实例分区端口 {inst}）"
        out[pid] = {"uin": uin, "why": why, "inst_port": inst, "account_hits": acct}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="只读判断每个 QQ 进程登录了哪个号")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--uin", help="只回答：这个号在哪个 PID")
    args = ap.parse_args()

    r = resolve()
    if args.uin:
        pids = sorted(p for p, v in r.items() if v["uin"] == args.uin)
        print(json.dumps({"uin": args.uin, "pids": pids,
                          "detail": {str(p): r[p] for p in pids}}, ensure_ascii=False))
        return 0 if len(pids) == 1 else 1

    if args.json:
        print(json.dumps({str(k): v for k, v in r.items()}, ensure_ascii=False))
        return 0

    if not r:
        print("没看到任何 QQ 主进程")
        return 1
    for pid, v in r.items():
        print(f"PID {pid:<7} 识别={v['uin'] or '认不出':<11} 实例端口={v['inst_port']} 账号分区={v['account_hits']}")
        print(f"          依据: {v['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
