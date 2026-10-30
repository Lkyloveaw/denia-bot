r"""「回话用哪个模型」的纯逻辑 —— 首选 + 次选（首选报错时顶上），不 import nonebot / muicebot。

拆在这一层的只有四个判断，因为它们是最容易算错、错了最难查的地方：

1. **主人给的那个词到底指谁**：`模型 首选 relay` 点的是 `models.yml` 的**条目**（一整包
   中转地址 + Key + 人设），`模型 首选 gpt-5.6-terra` 点的是**上游真模型名** —— 后者沿用
   拉到这份清单那个条目的中转/Key/人设，只把发请求时的模型名换掉。认不出的名字写进状态，
   下一次她直接不回话，所以「存不存」这件事必须在这一层判清楚。
2. **指令改不改得动状态**：条目名不存在、或者上游清单压根没拉到时，一律不写盘。
3. **这一回合算不算首选失败了**：判松了会把正在说话的首选换掉（同一轮重说一遍），
   判紧了等于没做降级（还是已读不回）。MuiceBot 的流式失败是**静默**的 ——
   `_wrapper.py:55` 把 `succeed=False` 的 chunk 丢掉，`muice.py:347` 再抛 `RuntimeError`，
   最后 `onebot.py:660` 的 `finally: return` 把异常吃掉，所以只能从「这轮有没有产出」倒推。
4. **换人时人设跟不跟着换**：`models.yml` 里每条自带 `template`，次选那条没配的话
   直接沿用会掉成 MuiceBot 的默认人设 —— 主人看到的就不是她了（`pick_template`）。

接线在 `nonebot/plugins/model_pref.py`（套壳 `Muice.ask_stream` / `Muice.ask` + `.模型` 指令），
断言在 `nonebot/tests/test_model_pref_{logic,wiring}.py`。状态落盘 `nonebot/data/model_pref.json`
（跟 `gate.json` 一样：重启还在，`models.yml` 被存一遍打回 `default:` 也能自愈）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

try:
    import gate_logic as gl
except ImportError:  # 不是从 nonebot\ 目录导入的
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gate_logic as gl

DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "model_pref.json"

#: 首字超时。中转站最常见的坏法是「挂住不报错」，没有超时就永远等不到降级那一刻。
DEFAULT_TIMEOUT = 45.0

#: MuiceBot `ModelConfig.template` 的默认值（`llm/_config.py:16`），等于「这条没配人设」。
DEFAULT_TEMPLATE = "Muice"

#: MuiceBot 自己塞的兜底文案（`muice.py:303` / `muice.py:362`），出现了等于没回话。
BOILERPLATE = ("模型未加载", "模型回复为空")

#: 「压根没去拉上游清单」和「拉了但失败（None）」是两件事，用一个看不见的哨兵分开。
_MISSING = object()
#: 同一个哨兵的对外名字：接线层默认传它，意思是「这次没去网关问过」。
UNTRIED = _MISSING

MODEL_HEADS = {"模型", "模型设置", "model-pref"}
#: 裸写（不带 `.`）时也认这两个。`model-pref` 只在带前缀时认，免得正常说话被当成指令。
BARE_MODEL_HEADS = {"模型", "模型设置"}

PRIMARY_ALIASES = {"首选", "主选", "primary"}
SECONDARY_ALIASES = {"次选", "备选", "备胎", "secondary", "backup"}
#: 不含「看」「状态」这种单字/常用词 —— 「模型状态不太好」是句人话，不是指令。
VIEW_ALIASES = {"查看", "show", "list"}
CLEAR_WORDS = {"无", "没", "没有", "关", "关闭", "off", "none", "取消", "clear"}

_SUBS_BY_LEN = sorted(
    VIEW_ALIASES | PRIMARY_ALIASES | SECONDARY_ALIASES, key=len, reverse=True
)

USAGE = "用法：模型 查看 ｜ 模型 首选 <条目名|上游真模型名> ｜ 模型 次选 <条目名|上游真模型名|无>"


# ---------------------------------------------------------------- 状态


def today() -> str:
    return time.strftime("%Y-%m-%d")


def new_state(day: str | None = None) -> dict:
    return {
        "day": day or today(),
        "primary": None,
        "primary_model": None,
        "secondary": None,
        "secondary_model": None,
        "swaps": {},
        "updated": None,
    }


def _name(value: Any) -> str | None:
    """条目名只能是非空字符串，别的一律当没设过（手改坏 json 不该把机器人带崩）。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _norm(src: Any, day: str) -> dict:
    s = new_state(day)
    if not isinstance(src, dict):
        return s
    s["primary"] = _name(src.get("primary"))
    s["secondary"] = _name(src.get("secondary"))
    #: 真模型名覆盖只在条目还在的时候有意义；条目被清了覆盖也跟着清（下面 `apply_command` 管）。
    s["primary_model"] = _name(src.get("primary_model")) if s["primary"] else None
    s["secondary_model"] = _name(src.get("secondary_model")) if s["secondary"] else None
    swaps = src.get("swaps")
    if isinstance(swaps, dict):
        s["swaps"] = {
            str(k): v
            for k, v in swaps.items()
            if isinstance(v, int) and not isinstance(v, bool) and v > 0
        }
    if isinstance(src.get("updated"), str):
        s["updated"] = src["updated"]
    file_day = src.get("day")
    if isinstance(file_day, str) and file_day != day:  # 跨天：顶班次数归零，选择留着
        s["swaps"] = {}
    return s


def load_state(path: Path | str = DEFAULT_PATH, day: str | None = None) -> dict:
    """读盘。文件没有、坏的、半截的都当成「还没设过」，不许抛。"""
    day = day or today()
    try:
        raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    return _norm(raw, day)


def save_state(path: Path | str, state: dict) -> None:
    """原子替换：先写 .tmp 再 rename，中途断电不会留下半个 json。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    payload = {k: state.get(k) for k in (
        "day", "primary", "primary_model", "secondary", "secondary_model", "swaps", "updated",
    )}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def bump(state: dict, name: str) -> int:
    """记一次「这个条目顶过班」，返回累计次数。"""
    key = str(name)
    state.setdefault("swaps", {})
    state["swaps"][key] = int(state["swaps"].get(key, 0)) + 1
    return state["swaps"][key]


# ---------------------------------------------------------------- 指令解析


def parse_model_command(text: str, nicknames: Iterable[str] = ()) -> list[str] | None:
    """把一条消息认成模型指令 → `["模型", 子指令, 参数...]`；不是指令返回 None。

    复用 `gate_logic.parse_gate_command`（前缀、昵称、裸写那套规矩两边要一致），
    它只认限流自己的子指令词头，所以「模型首选relay」这种不打空格写法先补一个空格
    再交给它 —— 补出来的候选认不成就还是普通聊天，不硬掰。
    """
    s = (text or "").strip()
    forms = [s]
    for head in sorted(MODEL_HEADS, key=len, reverse=True):
        for sub in _SUBS_BY_LEN:
            if head + sub in s:
                forms.append(s.replace(head + sub, f"{head} {sub} ", 1))
    for cand in forms:
        got = gl.parse_gate_command(cand, MODEL_HEADS, nicknames, allow_bare=BARE_MODEL_HEADS)
        if got is not None:
            return got
    return None


def _lookup(name: str, names: Iterable[str]) -> str | None:
    """条目名不区分大小写（`models.yml` 里习惯全小写，打字时容易飘）。"""
    table = {str(n).lower(): str(n) for n in names or ()}
    return table.get(str(name or "").strip().lower())


def _entry_list(names: Iterable[str]) -> str:
    got = [str(n) for n in names or ()]
    return "、".join(got) if got else "（models.yml 里一个条目都没有）"


def _model_list(upstream: Iterable[str] | None) -> str:
    if upstream is None:
        return "（这次没拉到）"
    got = [str(m) for m in upstream or ()]
    return "、".join(got) if got else "（上游一个模型都没给）"


def _split_upstream(upstream) -> tuple[str | None, Any]:
    """把 `(清单来源条目, [模型名])` 拆开；`_MISSING` / `None` / 裸列表都兜住。"""
    if upstream is _MISSING:
        return None, _MISSING
    if upstream is None:
        return None, None
    if isinstance(upstream, tuple):
        src, models = upstream[0], upstream[1]
        return (str(src) if src else None), [str(m) for m in (models or ())]
    return None, [str(m) for m in (upstream or ())]


def resolve_target(
    value: str,
    names: Iterable[str],
    upstream: Iterable[str] | None,
    *,
    base_entry: str | None = None,
) -> dict:
    """把主人给的那个词翻译成「用哪条目 + 要不要覆盖真模型名」。

    先按 `models.yml` 的**条目名**认（同名时条目优先 —— 条目才是主人认得的那层意思：
    一整包中转地址 + Key + 人设）；认不出再按上游 `/v1/models` 给的**真模型名**认，
    这时沿用拉到这份清单的那个条目，只把发请求时的模型名换掉
    （`llm/providers/openai.py:38` 把真名挂在 provider 的 `.model` 上，`:137`/`:221` 发请求才读）。

    `upstream` 两种形状都收：`[模型名]`，或 `list_upstream()` 原样返回的
    `(来源条目, [模型名])`。后者更保险 —— 真名该挂在**清单是从哪条拉来的**那个条目下，
    那个条目才带着对的中转地址和 Key；`base_entry` 只在裸列表时兜底。

    返回 `{"kind": "entry"|"model"|"error", ...}`，**认不出一律 error**：写进状态的名字
    必须是下一回合真能切过去的，否则就是「她忽然整晚不回话」。
    """
    name = str(value or "").strip()
    if not name:
        return {"kind": "error", "why": USAGE}

    entry = _lookup(name, names)
    if entry:
        return {"kind": "entry", "entry": entry, "model": None}

    src, models = _split_upstream(upstream)
    if models is None or models is _MISSING:
        return {"kind": "error", "why":
                f"没有叫「{name}」的模型条目，而且上游模型列表这次没拉到（网关没起 / Key 不对 / 断网），"
                f"所以只能按 models.yml 的条目名选。可选条目：{_entry_list(names)}"}

    model = _lookup(name, models)
    if model:
        host = src or base_entry
        if not host:
            return {"kind": "error", "why":
                    f"「{model}」是上游的真模型名，但这次不知道它该挂在哪个条目下（条目带着中转地址和 Key）。"
                    f"先把首选设成一个条目：模型 首选 {_entry_list(names).split('、')[0]}"}
        return {"kind": "model", "entry": host, "model": model}

    return {"kind": "error", "why":
            f"没有叫「{name}」的模型。两张清单：\n"
            f"· models.yml 条目：{_entry_list(names)}\n"
            f"· 上游可选真模型：{_model_list(models)}"}


def _same_choice(state: dict) -> bool:
    """首选/次选指的是不是**同一个模型** —— 同条目不同真模型是不同选择，反之是空转。"""
    return (
        str(state.get("primary") or "").lower() == str(state.get("secondary") or "").lower()
        and (state.get("primary_model") or None) == (state.get("secondary_model") or None)
    )


def _canon_model(entry: str | None, model: str | None, details: dict | None) -> str | None:
    """覆盖名等于条目本来写的名字时清成 None —— 别让「等于默认」看起来像一种特殊状态，
    否则次选填条目名、首选填同名模型会被当成两个不同选择，降级空转。"""
    if not model:
        return None
    got = (details or {}).get(str(entry)) or {}
    if str(got.get("model_name") or "").strip().lower() == str(model).lower():
        return None
    return model


# ---------------------------------------------------------------- 这一回合算不算失败


def usable(text: str) -> bool:
    """模型到底有没有产出正文。空白和 MuiceBot 的兜底文案都算「没回话」。"""
    s = (text or "").strip()
    if not s:
        return False
    return not any(s.startswith(word) for word in BOILERPLATE)


def noise(text: str) -> bool:
    """单个 chunk 是不是 MuiceBot 的兜底文案（`muice.py:303`、`muice.py:362`）。

    和 `usable` 用同一份词头，但判的是「这一段该不该原样转出去」：降级成功后，
    首选那句「模型回复为空…」不该留在她说的话前面。
    """
    s = (text or "").strip()
    return bool(s) and any(s.startswith(word) for word in BOILERPLATE)


def need_failover(
    state: dict,
    *,
    enabled: bool = True,
    error: str = "",
    streamed: bool = False,
    text: str = "",
) -> tuple[bool, str]:
    """该不该让次选顶上 → `(是与否, 说给日志看的理由)`。

    先问「这一回合到底算不算失败」：**首选正常回话时直接返回空理由**，
    接线那边靠这个空串决定要不要写日志 —— 不然每条正常回复都要多打一行「为什么不降级」。
    判失败之后才排优先级：开关 > 已经吐过字（绝对不能重跑）> 有没有可用的备胎。
    """
    primary = state.get("primary")
    secondary = state.get("secondary")

    failed = bool(error) or not usable(text)
    if not failed:
        return False, ""
    if not enabled:
        return False, "MODEL_FAILOVER=0，降级没开"
    if streamed:
        return False, "首选已经吐过字，重跑会把同一轮说两遍"
    if not secondary:
        return False, "没设次选（模型 次选 <条目名|上游真模型名> 可以设）"
    if primary and _same_choice(state):
        return False, "次选跟首选是同一个模型，重跑一遍不叫降级"
    if error:
        return True, f"首选报错：{error}"
    return True, "首选没吐出可用正文"


def pick_template(primary: str | None, fallback: str | None) -> str | None:
    """次选这条 models.yml 条目没配人设时，沿用首选的 —— 换模型不能把她换成别人。"""
    if not fallback or fallback == DEFAULT_TEMPLATE:
        return primary
    return fallback


# ---------------------------------------------------------------- 跑一趟 / 换人重跑


async def _close(source) -> None:  # noqa: ANN001 - 传进来的是异步生成器，类型写不死
    """超时或被放弃时把那条流收掉，别让 openai 的连接挂在事件循环里。"""
    aclose = getattr(source, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:  # noqa: BLE001 - 收尾失败不该盖掉真正的失败原因
        pass


async def drive(source, record: dict, *, timeout: float | None = None):
    """跑一趟模型：chunk **立刻**转出去（流式不能憋），同时把结论写进 `record`。

    `record` 最后长这样：`{"text": 攒下的正文, "streamed": 说没说过像样的话, "error": 失败原因}`。

    两件事容易踩：
    * `streamed` 用 `usable()` 判，不是「有没有 chunk」—— MuiceBot 空回复时会吐一个
      带「模型回复为空…」的 chunk（`muice.py:364`），那不算说过话，照样该降级。
    * 超时只掐**第一个** chunk。中转站挂住不报错是最常见的坏法；首字到了之后
      中间慢是不该被掐断的。
    """
    parts: list[str] = []
    record.update(text="", streamed=False, error="")
    it = source.__aiter__()
    waiting_first = True
    try:
        while True:
            try:
                if waiting_first and timeout:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout)
                else:
                    chunk = await it.__anext__()
            except StopAsyncIteration:
                break
            except (asyncio.TimeoutError, TimeoutError):
                record["error"] = f"首字超时 {timeout:g}s，模型那边一个字都没给"
                waiting_first = False
                break
            waiting_first = False
            parts.append(getattr(chunk, "chunk", "") or "")
            yield chunk
    except Exception as e:  # noqa: BLE001 - provider 抛的异常要变成可判定的信号
        record["error"] = f"{type(e).__name__}: {e}"
    finally:
        record["text"] = "".join(parts)
        record["streamed"] = usable(record["text"])
        await _close(source)  # 正常跑完是空操作；超时/中断时这才把连接收掉


async def with_rescue(
    state: dict,
    make_first,  # noqa: ANN001 - 无参函数，返回一趟模型的异步生成器
    *,
    make_rescue=None,  # noqa: ANN001
    enabled: bool = True,
    timeout: float | None = None,
    note=None,  # noqa: ANN001
    cleanup=None,  # noqa: ANN001
):
    """首选跑一趟；判该换人时用 `make_rescue()` 再跑一趟，跑完一定 `cleanup()`。

    谁换人、怎么换（改 `Muice.model` / `model_config` / 人设）都不在这里 —— 那是指令
    接线的事。这里只保证三件事：**没产出才重跑**、**吐过字绝不重跑**、
    **不管次选成没成都把全局状态恢复回首选**。
    """
    first: dict = {}
    async for chunk in drive(make_first(), first, timeout=timeout):
        yield chunk

    ok, why = need_failover(
        state,
        enabled=enabled,
        error=first.get("error", ""),
        streamed=bool(first.get("streamed")),
        text=first.get("text", ""),
    )
    if not why:  # 首选好好回了，一句话都别多说
        return
    if note is not None:
        note(ok, why)
    if not ok or make_rescue is None:
        return

    second: dict = {}
    try:
        async for chunk in drive(make_rescue(), second, timeout=timeout):
            yield chunk
    finally:
        if cleanup is not None:
            cleanup()


# ---------------------------------------------------------------- 查看与执行指令


def _describe(name: str | None, details: dict | None, override: str | None = None) -> str:
    if not name:
        return "未设置"
    d = (details or {}).get(str(name)) or {}
    bits = []
    if override:
        bits.append(f"真模型 {override}（覆盖 models.yml 的 {d.get('model_name') or '没写'}）")
    elif d.get("model_name"):
        bits.append(str(d["model_name"]))
    if d.get("provider"):
        bits.append(f"@{d['provider']}")
    if d.get("template"):
        bits.append(f"人设 {d['template']}")
    bits.append("流式" if d.get("stream") else "非流式")
    return f"{name}（{'，'.join(bits)}）"


def render_state(
    state: dict,
    names: Iterable[str],
    *,
    details: dict | None = None,
    current: str | None = None,
    enabled: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    upstream=_MISSING,
) -> str:
    names = list(names or ())
    sec = state.get("secondary")
    lines = [
        f"首选 {_describe(state.get('primary'), details, state.get('primary_model'))}",
        f"次选 {_describe(sec, details, state.get('secondary_model'))}"
        if sec else "次选 未设置 —— 首选报错时不会降级",
    ]
    if current:
        lines.append(f"当前生效：{current}")
    swaps = state.get("swaps") or {}
    lines.append(
        "今天顶过班：" + ("、".join(f"{k} {v} 次" for k, v in sorted(swaps.items())) if swaps else "没有")
    )
    lines.append(f"可选条目：{_entry_list(names)}")
    if upstream is not _MISSING:  # 只有真的去网关拉过才提这一行，别在断网时假装看过
        lines.append(f"上游可选真模型：{_model_list(upstream)}")
    lines.append(
        f"开关 MODEL_FAILOVER：{'开' if enabled else '关'}"
        f"｜首字超时 {timeout:g}s（都改 `nonebot/.env`，改完要重启）"
    )
    lines.append(USAGE)
    return "\n".join(lines)


def apply_command(
    state: dict,
    args: list[str],
    names: Iterable[str],
    *,
    details: dict | None = None,
    current: str | None = None,
    enabled: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    upstream=_MISSING,
) -> tuple[bool, str]:
    """执行一条 `模型 …` 指令（args 已去掉「模型」本身）→ `(改了吗, 回给人看的话)`。

    `upstream` 是给「真模型名」那半截用的：`(从哪个条目拉到的, [模型名])`。
    没去拉传 `UNTRIED`（默认），拉失败传 `None` —— 这时**只认条目名**，点模型名一律拒绝，
    因为存进去一个下一回合切不过去的名字，看起来就像「她无缘无故不回话」。
    """
    names = list(names or ())
    args = list(args or [])
    _, models = _split_upstream(upstream)  # 元组里的来源条目交给 `resolve_target` 自己认

    def view(**over):
        kw = {"details": details, "enabled": enabled, "timeout": timeout}
        kw.update(over)
        if models is not _MISSING:
            kw["upstream"] = models
        return render_state(state, names, **kw)

    if not args or args[0] in VIEW_ALIASES:
        return False, view(current=current)

    action = str(args[0]).strip()
    value = args[1].strip() if len(args) > 1 else ""

    def chosen(v: str):
        """解析一个词 → (条目, 真模型名覆盖)；认不出返回错误文案。"""
        got = resolve_target(v, names, upstream, base_entry=state.get("primary"))
        if got["kind"] == "error":
            return None, None, got["why"]
        return got["entry"], _canon_model(got["entry"], got["model"], details), None

    if action in PRIMARY_ALIASES:
        if not value:
            return False, f"{USAGE}\n首选要么给 models.yml 的条目名，要么给上游的真模型名。"
        entry, model, err = chosen(value)
        if err:
            return False, err
        state["primary"] = entry
        state["primary_model"] = model
        note = f"首选已改成 {_describe(entry, details, model)}。"
        if state.get("secondary") and _same_choice(state):
            state["secondary"] = None
            state["secondary_model"] = None
            note += "次选跟它指的是同一个模型，已顺手清掉（同名不会降级）。"
        return True, note + "\n" + view(current=entry)

    if action in SECONDARY_ALIASES:
        if not value:
            return False, f"{USAGE}\n次选要给条目名或上游真模型名，不要了写「无」。"
        if value in CLEAR_WORDS:
            if not state.get("secondary"):
                return False, "次选本来就是空的。"
            state["secondary"] = None
            state["secondary_model"] = None
            return True, "次选已清掉，之后首选报错就不降级了（保持原来的静默）。"
        entry, model, err = chosen(value)
        if err:
            return False, err
        was, was_model = state.get("secondary"), state.get("secondary_model")
        state["secondary"], state["secondary_model"] = entry, model
        if state.get("primary") and _same_choice(state):
            state["secondary"], state["secondary_model"] = was, was_model
            return False, "次选不能跟首选是同一个模型 —— 换人重跑同一个模型没有意义。"
        return True, (
            f"次选已设成 {_describe(entry, details, model)}。"
            "首选报错、或者一整轮没产出时，这一回合会用它重跑一次。\n" + view(current=current)
        )

    return False, (
        f"认不出「{action}」这个子指令。\n{USAGE}\n可选条目：{_entry_list(names)}"
        + (f"\n上游可选真模型：{_model_list(models)}" if models is not _MISSING else "")
    )


# ---------------------------------------------------------------- 帮助列表


def help_rows() -> list[tuple[str, str]]:
    """给 `.help` 的指令表；由 `plugins/model_pref.py` 注册进 `gate_logic`。"""
    return [
        ("模型 查看", "当前首选/次选 + 条目名和上游真模型名两张清单（仅主人）"),
        ("模型 首选 <条目名|真模型名>", "换回话用的模型，重启也认（仅主人）"),
        ("模型 次选 <条目名|真模型名|无>", "首选报错时顶上来的备胎（仅主人）"),
    ]
