#!/usr/bin/env python3
r"""
只注入指定 QQ 号，其余账号完全不被 hook。

原理：
  SnowLuma 的注入粒度是 PID，不是 UIN，所以要先认出每个 QQ 主进程是哪个号。
  本脚本用三条通道，可信度从高到低：

    0. 文件句柄归属（tools/qq_ownership.py，主力）—— QQ 登录后会一直持有
       %APPDATA%\QQ\Partitions\qqnt_<uin> 里的 leveldb/LOCK、Cookies、
       DIPS-wal 等句柄。用 NtQuerySystemInformation 把句柄表过一遍，落在
       哪个账号分区、归属哪棵 QQ.exe 进程树，就是这个 PID 登录了这个号。
       只读、不注入、多开不串，实测 0.5 秒出结果。

    1. probe-login（SnowLuma 自带）—— 多开下**不可信**，见下面「已知坑」。

    2. CEF partition 时间戳配对 —— 实例分区 qqnt_<端口> 与账号分区
       qqnt_<uin> 在同一次启动里几乎同时被写 Preferences，靠时间差配对。
       两台实例只差 2 秒启动时会配不唯一，所以只当前两条都失效时的兜底。

  三条都认不出、或硬证据互相冲突 → 跳过该 PID，绝不猜着注入。

已知坑（为什么不直接信 SnowLuma 的 probe）：
  ptlocal 的 /pt_get_uins 读的是 %APPDATA%\QQ 下**共用**的「本机记住的号」列表，
  不是「这个 PID 登录的号」。实测两个实例的端口（4301/4303）都回 1690711850，
  小号 3892894069 压根不在里面（扫码登录、没勾记住密码）。
  而 SnowLuma 的 tryPtloginMethod（index.mjs:1036）会连打两次、取**较短**的那个结果
  （`reduce((a,b)=> a.length<=b.length ? a : b)`），只在长度恰好为 1 时才认身份；
  小号那个实例的 pt 服务会间歇返回空列表（实测 4 次里 2 次为 []），
  于是退化成 "fallback" → 接口回 {"info": null}。
  换句话说：probe 没有信息是它「两次取较短」+ 列表抖动；就算有信息，给的也是错的号。
  所以 probe 只做交叉验证，绝不用它来决定注谁。

配套设置：config/runtime.json 里 hookAutoLoad 必须是 false，
          改用本脚本显式注入，这样非目标账号从头到尾不进 hook。

用法：
    python 只注入小号.py            # 执行一次
    python 只注入小号.py --check    # 只报告身份识别结果，不注入
    python 只注入小号.py --watch    # 常驻，每 15 秒收敛一次（QQ 重启后自动恢复）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------- 配置 ----------------
TARGET_UIN = "3892894069"                     # 只注入这个号
BASE = "http://127.0.0.1:5099"                # SnowLuma WebUI
ROOT = Path(__file__).resolve().parent.parent
PASS_FILE = ROOT / "tools" / "snowluma-webui-pass.txt"
QQ_PARTITIONS = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "QQ" / "Partitions"
WATCH_INTERVAL = 15
PAIR_TOLERANCE = 90                           # 端口目录与账号目录允许的时间差（秒）
# -------------------------------------


def http(method: str, path: str, token: str | None = None, body: dict | None = None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        return {"_httpError": e.code, "_body": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def login() -> str | None:
    if not PASS_FILE.exists():
        print(f"[!] 找不到密码文件: {PASS_FILE}")
        print("    请把 SnowLuma WebUI 密码写进该文件（单独一行）。")
        return None
    pw = PASS_FILE.read_text(encoding="utf-8").strip()
    res = http("POST", "/api/login", body={"password": pw})
    tok = res.get("token")
    if not tok:
        print(f"[!] 登录失败: {res}")
        return None
    return tok


# ---------------- 身份识别：partition 时间戳配对 ----------------

def qq_listen_ports() -> dict[int, set[int]]:
    """netstat -ano → {pid: 该进程监听的端口}"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20).stdout
    except Exception:  # noqa: BLE001
        return {}
    res: dict[int, set[int]] = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) < 5 or f[0].upper() != "TCP" or f[3].upper() != "LISTENING":
            continue
        m = re.search(r":(\d+)$", f[1])
        if not m or not f[-1].isdigit():
            continue
        res.setdefault(int(f[-1]), set()).add(int(m.group(1)))
    return res


def _stamp(d: Path) -> float:
    """Preferences 每次实例启动写一次，比目录 mtime 干净。"""
    for name in ("Preferences", "Local State"):
        try:
            return (d / name).stat().st_mtime
        except OSError:
            pass
    try:
        return d.stat().st_mtime
    except OSError:
        return 0.0


def uin_from_partitions(pid: int) -> tuple[str, str]:
    ports = qq_listen_ports().get(pid, set())
    if not QQ_PARTITIONS.is_dir():
        return "", f"找不到 {QQ_PARTITIONS}"
    named: dict[int, Path] = {}
    for d in QQ_PARTITIONS.iterdir():
        m = re.fullmatch(r"qqnt_(\d+)", d.name)
        if m and d.is_dir():
            named[int(m.group(1))] = d
    port_stamps = {n: _stamp(named[n]) for n in ports if n in named}
    if not port_stamps:
        return "", f"PID {pid} 的端口 {sorted(ports)} 没有对应的 Partitions/qqnt_<port> 目录"
    # 端口 ≤65535，比这大的数字目录才是 UIN
    uin_cands = {n: t for n, d in named.items() if n not in ports and n > 0xFFFF and (t := _stamp(d))}
    if not uin_cands:
        return "", "Partitions 下没有 qqnt_<uin> 账号目录"
    ranked = sorted(
        ((min(abs(t - ps) for ps in port_stamps.values()), n) for n, t in uin_cands.items()),
    )
    best_dist, best_uin = ranked[0]
    detail = "、".join(f"{u}({int(d)}s)" for d, u in ranked)
    if best_dist > PAIR_TOLERANCE:
        return "", f"账号目录与端口目录时间差过大：{detail}"
    if len(ranked) > 1 and ranked[1][0] <= PAIR_TOLERANCE:
        return "", f"时间上配对不唯一：{detail}"
    return str(best_uin), f"按 Preferences 时间配对 {detail}"


def handle_identities() -> tuple[dict[int, tuple[str, str]], str]:
    """通道 0：文件句柄归属。见 tools/qq_ownership.py —— 不注入、只读。

    这是三条通道里唯一硬的：QQ 登录后持续持有该账号 CEF 分区的文件句柄，
    句柄归属到哪个 PID 就是哪个 PID 登录了这个号，多开也不会串。
    """
    try:
        from qq_ownership import resolve
    except Exception as e:  # noqa: BLE001
        return {}, f"句柄通道导入失败: {e}"
    try:
        r = resolve()
    except Exception as e:  # noqa: BLE001
        return {}, f"句柄通道不可用: {e}"
    return {pid: (v["uin"], v["why"]) for pid, v in r.items()}, "句柄通道已取"


def pick_uin(handle: str, handle_why: str, probe: str, part: str,
             dup_probe: bool, why: str) -> tuple[str, str]:
    """三条通道的裁决。句柄证据优先；probe 在多开下给的是「本机记住的号」，
    和 partition 冲突时不是 partition 错，是 probe 错。"""
    if handle:
        if probe and probe != handle:
            return handle, f"句柄：{handle_why}（probe={probe} 是共用的「记住的号」，弃用）"
        if part and part != handle:
            return handle, f"句柄：{handle_why}（时间配对给出的 {part} 不可靠，弃用）"
        return handle, f"句柄：{handle_why}" + ("；probe 一致" if probe else "")
    if probe and part:
        if probe == part:
            return probe, f"probe 与时间配对一致（无句柄证据）"
        if dup_probe:
            return part, f"时间配对：{why}（probe={probe} 是多开全局假答案）"
        return "", f"probe={probe} 与时间配对={part} 冲突，且没有句柄证据"
    if part:
        return part, f"时间配对：{why}"
    if probe:
        if dup_probe:
            return "", f"probe={probe} 同时挂在多个 PID 上，且没有句柄/时间佐证"
        return probe, "probe-login（单开可信，多开请复核）"
    return "", f"三条通道都没结论；probe 无结果，{why}"


def resolve_all(token: str) -> dict[int, tuple[str, str]]:
    """一次看全部 QQ 主进程，返回 {pid: (uin, 依据)}。认不出就是 ("", 原因)。"""
    handles, hnote = handle_identities()
    rows: dict[int, tuple[str, str, str, str, str]] = {}
    for p in http("GET", "/api/processes", token).get("list", []):
        pid = p.get("pid")
        info = (http("GET", f"/api/processes/{pid}/probe-login", token) or {}).get("info")
        h, hwhy = handles.get(int(pid), ("", hnote)) if pid is not None else ("", hnote)
        part, pwhy = uin_from_partitions(pid)
        rows[pid] = (h, hwhy, str((info or {}).get("uin") or ""), part, pwhy)

    dup = {u for _, _, u, _, _ in rows.values()
           if u and [r[2] for r in rows.values()].count(u) > 1}
    return {pid: pick_uin(h, hwhy, probe, part, probe in dup, pwhy)
            for pid, (h, hwhy, probe, part, pwhy) in rows.items()}


# ---------------- 收敛 ----------------

def verify_after_load(token: str, pid: int) -> bool:
    """注入后拿 hook 报上来的真实 UIN 复核，不符立刻卸载。"""
    time.sleep(3)
    for _ in range(3):
        for p in http("GET", "/api/processes", token).get("list", []):
            if p.get("pid") == pid:
                uin = str(p.get("uin") or "")
                if uin == TARGET_UIN:
                    return True
                if uin and uin != "0":
                    http("POST", f"/api/processes/{pid}/unload", token)
                    print(f"  [回滚] PID {pid} hook 报的 uin={uin} 不是 {TARGET_UIN}，已卸载")
                    return False
        time.sleep(2)
    print(f"  [警告] PID {pid} 注入后没读到 uin，请去 WebUI 确认")
    return False


def converge(token: str, *, verbose: bool = True) -> tuple[int, int]:
    """把注入状态收敛到「只有 TARGET_UIN」。返回 (注入数, 卸载数)。"""
    procs = http("GET", "/api/processes", token).get("list", [])
    loaded = unloaded = 0
    identities: dict[int, tuple[str, str]] = {}

    for p in procs:
        pid = p.get("pid")
        uin = str(p.get("uin") or "0")
        injected = bool(p.get("injected"))

        if injected:
            if uin == TARGET_UIN:
                continue
            # 注错人了 → 卸掉
            r = http("POST", f"/api/processes/{pid}/unload", token)
            if r.get("success"):
                unloaded += 1
                if verbose:
                    print(f"  [卸载] PID {pid}  uin={uin or '?'}")
            else:
                if verbose:
                    print(f"  [卸载失败] PID {pid}: {r}")
            continue

        # 未注入 → 先认身份。所有 PID 一次性一起判，才识别得出「全局假答案」
        if not identities:
            identities.update(resolve_all(token))
        who, why = identities.get(pid, ("", "本轮没看到这个进程"))
        if who == TARGET_UIN:
            r = http("POST", f"/api/processes/{pid}/load", token)
            if r.get("success") and verify_after_load(token, pid):
                loaded += 1
                if verbose:
                    print(f"  [注入] PID {pid}  uin={who}  依据: {why}")
            elif verbose:
                print(f"  [注入失败] PID {pid}: {r}")
        elif verbose:
            print(f"  [跳过] PID {pid}  uin={who or '?'}  依据: {why}")

    return loaded, unloaded


def check(token: str) -> int:
    print(f"QQ 主进程身份识别（目标 {TARGET_UIN}）：\n")
    rows = http("GET", "/api/processes", token).get("list", [])
    if not rows:
        print("  一个 QQ 主进程都没看到（QQ 没开？SnowLuma 没起来？）")
        return 1
    ports = qq_listen_ports()
    identities = resolve_all(token)
    for p in rows:
        pid = p.get("pid")
        who, why = identities.get(pid, ("", "本轮没看到这个进程"))
        flag = "★ 会注入" if who == TARGET_UIN else "· 不碰"
        print(f"  PID {pid:<7} {flag:<9} 状态={p.get('status'):<13} "
              f"hook_uin={p.get('uin'):<11} 识别={who or '认不出':<11} 监听={sorted(ports.get(pid, set()))}")
        print(f"          依据: {why}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="只注入指定的 QQ 号")
    ap.add_argument("--watch", action="store_true", help="常驻运行，定期收敛")
    ap.add_argument("--check", action="store_true", help="只报告身份识别结果，不注入")
    ap.add_argument("--quiet", action="store_true", help="只在有变化时输出")
    args = ap.parse_args()

    token = login()
    if not token:
        return 1

    if args.check:
        return check(token)

    if not args.watch:
        l, u = converge(token)
        print(f"完成：注入 {l} 个，卸载 {u} 个（目标 {TARGET_UIN}）")
        return 0

    print(f"常驻监控中（每 {WATCH_INTERVAL}s 收敛一次，目标 {TARGET_UIN}）。Ctrl+C 退出。")
    while True:
        try:
            l, u = converge(token, verbose=not args.quiet)
            if l or u:
                print(f"  → 注入 {l}，卸载 {u}")
            time.sleep(WATCH_INTERVAL)
        except KeyboardInterrupt:
            print("\n退出。")
            return 0
        except Exception as e:  # noqa: BLE001
            # token 可能失效，重登
            print(f"  轮次异常: {e}，重新登录…")
            token = login() or token
            time.sleep(WATCH_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
