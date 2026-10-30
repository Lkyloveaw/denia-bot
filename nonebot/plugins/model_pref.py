"""回话用哪个模型 —— 主人私聊里设「首选」和「次选」，首选这一回合没产出时次选顶上。

点模型可以点两种名字（`model_pref_logic.resolve_target`）：

* **`models.yml` 的条目名**（`relay`、`echo-test`）—— 一整包：中转地址 + Key + 人设；
* **上游的真模型名**（`gpt-5.6-terra`）—— 从网关 `/v1/models` 拉的清单，认出来之后
  **沿用清单来源那条条目的中转/Key/人设**，只把发请求时的模型名换掉：
  `llm/providers/openai.py:38` 把真名挂在 provider 的 `.model` 上，`:137`/`:221` 是发请求那一刻才读，
  所以改这一个字段就够，不用碰条目、也不用另存 Key（清单本身就复用了 provider 自带的
  `client.models.list()`）。同名时条目优先，因为条目才是主人认得的那层意思。

为什么不能靠 MuiceBot 自己的 `.model`：

* `change_model_config` 只改内存（`muice.py:131`），而 `configs/models.yml` 一保存就会把当前
  模型打回带 `default: true` 的那条（`_on_config_changed` → `muice.py:117`）—— 主人挑的模型
  活不过下一次改配置；真名覆盖同理，所以每回合开头都要 `enforce_model_name` 再补一次；
* 它的 `.model load` 是 SUPERUSER 的 priority 10 指令，跟我们要加的指令表是两套东西；
* 最要紧的一条：**流式失败是静默的**。`llm/_wrapper.py:55` 把 `succeed=False` 的 chunk 丢掉，
  `muice.py:347` 再抛 `RuntimeError("模型调用器返回的值应至少包含一个元素")`，最后
  `onebot.py:656-660` 的 `finally: return` 把异常吃掉 —— 现场就是「已读不回」，日志里一行错都没有。
  MuiceBot 的钩子（`plugin/hook`）没有「失败重试」这一类，也没有取消语义，所以降级做不进钩子里。

于是这层自己套壳：把 `Muice.ask_stream` / `Muice.ask` **这两个类方法**换成会救场的版本。
套在类上而不是实例上是因为 `_init_model` 重建对象时实例方法会跟着没了，类方法不会；
判据用「这一回合有没有产出」，因为失败本身传不上来。

四条规矩（`model_pref_logic.py` 里逐条有断言）：

1. 首选好好回话 → 什么都不做，连日志都不多写一行；
2. 首选**已经吐过字** → 绝不重跑，宁可那半句难看（同一轮说两遍比难看严重得多）；
3. 换人次选跑完一定要恢复回首选，连「正在降级」的标记也要清掉（`ensure_primary` 靠它让路）；
4. MuiceBot 那句「模型回复为空…」「模型未加载」先扣住不发：次选真的顶上来了，她前面就不该
   多一句系统提示；没人救场时原样转出去，保持 MuiceBot 本来的样子。

「换人」换的是 `Muice.model` + `model_config` + 由该条目派生的 `template` / `template_mode` /
`multimodal` / `model_provider`。人设单拎出来处理：`models.yml` 里每条自带 `template`，
次选那条没配的话直接沿用会把主人看到的她换成 MuiceBot 默认人格，所以没配就沿用首选那份
（`model_pref_logic.pick_template`）。

指令（主人私聊发，`.模型` / `/模型` / 直接打「模型」都认）：

    模型 查看                        当前首选/次选、今天谁顶过班、两张清单
    模型 首选 <条目名|真模型名>      换回话用的模型（写进 data/model_pref.json，重启也认）
    模型 次选 <条目名|真模型名|无>   设/清救场的备胎

拉不到上游清单时（网关没起 / Key 不对 / 断网）**一律不写盘**，并把本地条目和上次拉到的名字
都报出来 —— 存进去一个下一回合切不过去的名字，现场就是「她忽然不回话」。

开关在 `nonebot/.env`（改了要重启，插件不热加载）：

    MODEL_FAILOVER=1           # 0 = 只留指令换模型，永不自动降级（回到原来的静默）
    MODEL_FAILOVER_TIMEOUT=45  # 等第一个字最多几秒；中转站挂住不报错时靠它才降级得动

出问题时看日志里的 `[model]` 前缀。判断逻辑在 `nonebot/model_pref_logic.py`
（`tests/test_model_pref_logic.py`），接线行为在 `tests/test_model_pref_wiring.py`。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from nonebot import get_driver, logger, on_message
from nonebot.adapters import Event
from nonebot.permission import SUPERUSER
from muicebot.muice import Muice
from muicebot.plugin import PluginMetadata

try:
    import gate_logic as gl
    import model_pref_logic as mp
except ImportError:  # 不是从 nonebot\ 目录启动的，把工程根补进 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import gate_logic as gl
    import model_pref_logic as mp

__plugin_meta__ = PluginMetadata(
    name="model_pref",
    description="命令式设置回话模型：首选 + 次选（首选这一回合没产出时顶上）；"
                "名字可以是 models.yml 条目，也可以是上游真模型名",
    usage="私聊发：模型 查看 | 模型 首选 <条目名|真模型名> | 模型 次选 <条目名|真模型名|无>",
)

driver = get_driver()
STATE_PATH = mp.DEFAULT_PATH

#: 原始实现，装在 `install()` 里；测试会把它换成替身，所以套壳必须每次按名字现读。
ORIGINAL_STREAM = None
ORIGINAL_ASK = None

#: 正在跑次选那一回合的凭据。`ensure_primary` 看到非空就装看不见 ——
#: 否则新到的消息会把模型又切回首选，正好打断别人。
_IN_RESCUE: list[object] = []

#: 上一次从网关 `/v1/models` 拉到的真模型名。拉不到时（网关黑了）指令回执要能报出这些名字，
#: 但**不拿它当「上游现在还记得这个名字」的证据** —— 认不出的一律不写盘。
_UPSTREAM_CACHE: list[str] = []

#: 问上游清单最多等几秒。这条是主人发指令时同步等的，网关挂了不该让指令悬在那儿。
UPSTREAM_TIMEOUT = 10.0

#: 换人时要暂存/恢复的字段。少了哪个都会留下「人设还是次选的」这类半换状态。
FIELDS = ("model", "model_config", "model_config_name", "model_provider",
          "multimodal", "template", "template_mode", "system_prompt", "user_instructions")


# ---------------------------------------------------------------- 环境与状态


def _flag(name: str = "model_failover", default: bool = True) -> bool:
    """.env 里的开关。每次现读，测试才换得动。"""
    try:
        raw = getattr(driver.config, name, None)
    except Exception:  # noqa: BLE001 - 驱动还没起来（测试里直接调这里的函数）
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "关")


def _timeout() -> float:
    """等首字的秒数。填错了退回默认，别因为一个错别字丢掉超时。"""
    try:
        raw = getattr(driver.config, "model_failover_timeout", None)
        return float(raw) if raw not in (None, "") else mp.DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        return mp.DEFAULT_TIMEOUT


_state_cache: dict | None = None


def state() -> dict:
    """内存里那份选择。跨天了重读一次盘：顶班次数归零，首选/次选留着。"""
    global _state_cache
    day = mp.today()
    if _state_cache is None or _state_cache.get("day") != day:
        _state_cache = mp.load_state(STATE_PATH, day=day)
    return _state_cache


def _persist() -> None:
    mp.save_state(STATE_PATH, state())


# ---------------------------------------------------------------- 条目表


def entry_names() -> list[str]:
    """`configs/models.yml` 里的条目名（指令的一种名字；另一种是上游真模型名，见 `list_upstream`）。"""
    try:
        from muicebot.config import get_model_config_manager

        return list(get_model_config_manager().configs)
    except Exception as e:  # noqa: BLE001 - 读不到条目表只是「查看」少一列，不该影响回话
        logger.warning(f"[model] 模型条目表没读到：{type(e).__name__}: {e}")
        return []


def entry_details() -> dict:
    """给 `模型 查看` 的那点细节（真模型名 / provider / 人设 / 是不是流式）。"""
    try:
        from muicebot.config import get_model_config_manager

        cfgs = get_model_config_manager().configs
    except Exception:  # noqa: BLE001
        return {}
    return {
        str(k): {
            "model_name": getattr(v, "model_name", ""),
            "provider": getattr(v, "provider", ""),
            "template": getattr(v, "template", ""),
            "stream": bool(getattr(v, "stream", True)),
        }
        for k, v in cfgs.items()
    }


def _muice() -> Muice:
    return Muice.get_instance()


def current_name() -> str | None:
    """现在真正在生效的条目名（`模型 查看` 里那行「当前生效」）。"""
    try:
        return str(getattr(_muice(), "model_config_name", "") or "") or None
    except Exception:  # noqa: BLE001 - Muice 还没起来时这行没有就是了
        return None


# ---------------------------------------------------------------- 上游允许的真模型名


async def list_upstream(muice=None):  # noqa: ANN001, ANN201 - 见 _force_name
    """问网关「这条目能用哪些真模型」→ `(清单来源条目, [模型名])`；问不到返回 None，绝不抛。

    复用 provider 自带的那份 openai 客户端（`llm/providers/openai.py:47`）：中转地址和 Key
    都在它身上，`configs/models.yml` 已经配好了，我们不用另存一份、也不新增依赖。
    `_echo` 这种没有客户端的 provider 只能按条目名选，指令照样能用（回执照常报条目）。

    来源条目要跟着一块返回：真模型名只是「发请求时叫什么」，用哪个中转、哪个 Key、哪份人设
    还是跟着条目走的（`resolve_target` 的 `base_entry`）。
    """
    try:
        muice = muice if muice is not None else _muice()
        provider = getattr(muice, "model", None)
        client = getattr(provider, "client", None)
        if client is None or getattr(client, "models", None) is None:
            # `_echo` 这类 provider 压根没客户端。不出声的话，回执里那句「这次没拉到」会被
            # 读成「网关挂了」，而真正的原因是「现在生效的这条问不了上游」。
            logger.info(f"[model] 条目 {getattr(muice, 'model_config_name', '?')} 的 provider "
                        f"没有 openai 客户端，问不了上游清单（这条只能按条目名选）")
            return None
        entry = str(getattr(muice, "model_config_name", "") or "") or None
        got = await asyncio.wait_for(client.models.list(), UPSTREAM_TIMEOUT)
        ids: list[str] = []
        for item in list(getattr(got, "data", None) or ()):
            mid = str(getattr(item, "id", "") or "").strip()
            if mid and mid not in ids:
                ids.append(mid)
        if ids:
            _UPSTREAM_CACHE[:] = ids
        return entry, ids
    except Exception as e:  # noqa: BLE001 - 网关黑了只是不能按真名选，不该把指令噎住
        logger.warning(f"[model] 上游模型列表没拉到（只能按 models.yml 条目名选）："
                       f"{type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------- 把真模型名按上去


def _force_name(muice, want: str | None, tag: str) -> None:  # noqa: ANN001 - 见 _apply
    """把 `want` 写成发请求时用的模型名（`provider.model`，`:137`/`:221` 那一刻才读）。

    只动这一个字段：条目、中转地址、Key、人设都留在原处 —— 主人点的是「同一个中转站里的
    另一个模型」，不是另一家服务商。按不上（字段只读 / provider 形状变了）只记一行日志，
    这一回合就用 `models.yml` 写的名字，绝不能把消息带走。
    """
    if not want:
        return  # 主人没点过真名：models.yml 写什么就用什么
    try:
        provider = getattr(muice, "model", None)
        if provider is None or getattr(provider, "model", None) == want:
            return
        provider.model = want
        logger.info(f"[model] {tag} 发请求用真模型名 {want}"
                    f"（条目 {getattr(muice, 'model_config_name', '?')} 的中转和 Key）")
    except Exception as e:  # noqa: BLE001 - 补名字失败顶多是用错模型，不是不回话
        logger.warning(f"[model] 真模型名 {want} 没按上去，这一回合用 models.yml 写的名字："
                       f"{type(e).__name__}: {e}")


def enforce_model_name(muice) -> None:  # noqa: ANN001 - 见 _apply
    """每回合开头把首选那份「真模型名覆盖」补回去。

    为什么需要：`change_model_config` / `_init_model` 会按 `ModelConfig.model_name` 重建 provider
    （存一遍 `models.yml` 也会走到），覆盖就这样被打回默认值 —— 主人上午点的 `gpt-5.6-terra`
    下午悄悄变回 `gpt-5.6-sol`，从日志里根本看不出来。只在当前生效的就是首选那条时补，
    否则会把首选的名字按到次选（或别人的）provider 上。
    """
    s = state()
    primary = s.get("primary")
    if not primary:
        return
    if str(getattr(muice, "model_config_name", "") or "").lower() != str(primary).lower():
        return
    _force_name(muice, s.get("primary_model"), "首选")


# ---------------------------------------------------------------- 换人的三个动作


def build_pair(name: str):
    """按条目名造一份 (ModelConfig, 模型实例)。条目没了或加载不了就抛 —— 调用方负责兜住。"""
    from muicebot.config import get_model_config
    from muicebot.llm import load_model

    cfg = get_model_config(name)
    model = load_model(cfg)
    if not model.load():
        raise RuntimeError(f"{name} 的模型实例 load() 失败（provider {getattr(cfg, 'provider', '?')}）")
    return cfg, model


def _apply(muice, cfg, model):  # noqa: ANN001, ANN201 - Muice 没有公开类型可引
    """把 cfg/model 装到 muice 身上，返回恢复原状需要的快照。

    人设走 `pick_template`：次选那条没配 `template`（或是 MuiceBot 默认的「Muice」）就沿用
    首选那份 —— 换个模型不该把主人认识的那个她换掉。
    """
    snap = {k: getattr(muice, k, None) for k in FIELDS}
    muice.model = model
    muice.model_config = cfg
    muice.model_provider = getattr(cfg, "provider", muice.model_provider)
    muice.multimodal = bool(getattr(cfg, "multimodal", False))
    muice.template_mode = getattr(cfg, "template_mode", "system")
    muice.template = mp.pick_template(snap.get("template"), getattr(cfg, "template", None))
    return snap


def _restore(muice, holder: dict) -> None:  # noqa: ANN001 - 见 _apply
    """回到首选那一趟之前的样子，并把「正在降级」的标记清掉。跑完/炸了都要走到这里。

    容忍直接传快照：`_apply` 给的是快照，`_restore` 要的是 holder，两者形状不同，
    调用方（尤其是临时验证脚本）传错时不该一声不响地留在半换状态。
    """
    if "snap" not in holder and "model" in holder:
        holder = {"snap": holder}
    snap = holder.pop("snap", None)
    if snap is not None:
        for k, v in snap.items():
            setattr(muice, k, v)
    ticket = holder.pop("ticket", None)
    if ticket is not None:
        try:
            _IN_RESCUE.remove(ticket)
        except ValueError:  # 已经被清过（不该发生，但清标记不该再抛一次）
            pass
    name = holder.pop("name", None)
    if name:
        used = mp.bump(state(), name)
        _persist()
        logger.info(f"[model] 这一回合交给次选 {name} 顶过 {used} 次，已换回首选 "
                    f"{state().get('primary') or '（未设置，由 models.yml 的 default 说了算）'}"
                    f"｜当前生效 {current_name() or '?'}")


def _swap_in(muice, secondary: str, holder: dict) -> bool:  # noqa: ANN001
    """换人次选。成了返回 True；条目被删了/加载不出来只报一行日志，返回 False。"""
    holder["ticket"] = object()
    _IN_RESCUE.append(holder["ticket"])
    try:
        cfg, model = build_pair(secondary)
        holder["snap"] = _apply(muice, cfg, model)
    except Exception as e:  # noqa: BLE001 - 次选坏了不能连累这一回合，更不能把机器人留在半换状态
        logger.warning(f"[model] 次选 {secondary} 没换上去，这一回合维持原样："
                       f"{type(e).__name__}: {e}")
        return False
    holder["name"] = secondary
    muice.model_config_name = secondary
    s = state()
    if s.get("secondary") and str(secondary).lower() == str(s["secondary"]).lower():
        # 同一条目里的两个真名也能互为备份（relay/terra ↔ relay/grok-4.7），
        # 顶上时得用次选那份，而且不能去动首选那个 provider 的名字。
        _force_name(muice, s.get("secondary_model"), "次选")
    logger.info(f"[model] 已切到次选 {secondary}（provider {getattr(cfg, 'provider', '?')}"
                f"，人设 {muice.template or '无'}）跑这一回合")
    return True


def _note(ok: bool, why: str) -> None:
    """降级/不换人都要留痕：「她为什么忽然又能用了」必须能从日志里读出来。"""
    if ok:
        logger.warning(f"[model] 已降级 → 次选 {state().get('secondary') or '?'}｜{why}")
    else:
        logger.info(f"[model] 不换人：{why}")


async def _nothing():  # noqa: ANN202 - 空流，只用来「跑一趟什么都不产出」
    if False:  # pragma: no cover - 让它成为异步生成器
        yield None


# ---------------------------------------------------------------- 套壳


def ensure_primary(muice) -> None:  # noqa: ANN001 - 见 _apply
    """每回合开头对一次账：首选（条目 + 真模型名）被 `models.yml` 的保存/重载打回时自己补回来。

    这是「重启也认」的真正来路 —— 我们不去抢 MuiceBot 的全局状态，只在它被人改回去时纠正。
    条目名和真模型名要**分两步**补：`change_model_config` 只会按 `ModelConfig.model_name`
    重建 provider，认不出主人后来点的那个真名。
    """
    try:
        if _IN_RESCUE:
            return
        primary = state().get("primary")
        if not primary:
            return  # 主人没设过：完全交给 models.yml，别介入
        if str(getattr(muice, "model_config_name", "") or "").lower() == primary.lower():
            enforce_model_name(muice)  # 条目对了，真名可能刚被打回 models.yml 写的那个
            return
        names = [str(n) for n in entry_names()]
        if not any(n.lower() == primary.lower() for n in names):
            logger.warning(f"[model] 首选 {primary} 已经不在 models.yml 里了，这一回合先用 "
                           f"{getattr(muice, 'model_config_name', '?')}"
                           f"（`.模型 首选 <条目名|真模型名>` 重新设）")
            return
        was = getattr(muice, "model_config_name", "?")
        muice.change_model_config(primary)  # 位置参数：Muice 那份形参叫 config_name，别用关键字绑死
        enforce_model_name(muice)
        logger.info(f"[model] 已按首选把模型切回 {primary}（之前是 {was}）")
    except Exception as e:  # noqa: BLE001 - 自愈失败顶多是这一回合用错模型，不该让消息死在这
        logger.warning(f"[model] 切回首选失败，这一回合先用着当前模型：{type(e).__name__}: {e}")


async def wrapped_stream(self, session, message, enable_history=True, enable_plugins=True):  # noqa: ANN001, ANN202
    """`Muice.ask_stream` 的替身：照常流式往外转，只在首选没产出时补一趟次选。

    MuiceBot 那句兜底文案（「模型回复为空…」「模型未加载」）先**扣住不发**：
    它出现在一趟跑完的最后，正好是「要不要叫次选」的判据出来之前那一刻。
    次选真的顶上来了就不该在她前面多一句系统提示；没人救场时原样转出去，
    保持 MuiceBot 本来的样子（至少说明她看见你了）。
    """
    ensure_primary(self)
    s, holder = state(), {}
    args = (session, message, enable_history, enable_plugins)
    held: list = []
    spoke = False

    def make_rescue():
        if not _swap_in(self, s["secondary"], holder):
            return _nothing()
        return ORIGINAL_STREAM(self, *args)

    # 恢复全局状态交给 `with_rescue` 的 cleanup：它挂在自己那趟的 try/finally 上，
    # 消费方中途断开（QQ 发送失败 → GeneratorExit 一路传进来）也会走到，不必在这里再套一层。
    async for chunk in mp.with_rescue(
        s,
        lambda: ORIGINAL_STREAM(self, *args),
        make_rescue=make_rescue,
        enabled=_flag(),
        timeout=_timeout(),
        note=_note,
        cleanup=lambda: _restore(self, holder),
    ):
        text = getattr(chunk, "chunk", "") or ""
        if mp.noise(text):
            held.append(chunk)
            continue
        if mp.usable(text):
            spoke = True
        yield chunk
    if not spoke:
        for chunk in held:
            yield chunk


async def wrapped_ask(self, session, message, enable_history=True, enable_plugins=True):  # noqa: ANN001, ANN202
    """`Muice.ask` 的替身 —— `models.yml` 里 `stream: false` 的条目走这条，判据一样。"""
    ensure_primary(self)
    s, holder = state(), {}
    args = (session, message, enable_history, enable_plugins)
    answer, error = None, ""
    try:
        answer = await ORIGINAL_ASK(self, *args)
    except Exception as e:  # noqa: BLE001 - 异常先接住，判完该不该降级再决定往不往外抛
        error = f"{type(e).__name__}: {e}"
        holder["boom"] = e

    text = str(getattr(answer, "text", "") or "")
    if answer is not None and not getattr(answer, "succeed", True):
        error = error or "模型回报 succeed=False"

    ok, why = mp.need_failover(s, enabled=_flag(), error=error, streamed=False, text=text)
    if why:
        _note(ok, why)
    if not ok:
        if answer is None and "boom" in holder:
            raise holder["boom"]  # 不降级时保持原样，包括原来会抛的那个异常
        return answer

    if not _swap_in(self, s["secondary"], holder):
        if answer is None and "boom" in holder:
            raise holder["boom"]
        return answer
    try:
        rescued = await ORIGINAL_ASK(self, *args)
    except Exception as e:  # noqa: BLE001 - 次选也炸：把首选那句（可能是空的）原样交回去
        logger.warning(f"[model] 次选 {s['secondary']} 也没回话："
                       f"{type(e).__name__}: {e}")
        rescued = None
    finally:
        _restore(self, holder)
    if rescued is None:
        if answer is None and "boom" in holder:
            raise holder["boom"]
        return answer
    return rescued


def install() -> int:
    """套壳。返回实际套了几处；已经套过的跳过（重启失败/重复加载都不能套两层）。"""
    n = 0
    for attr, slot, shell in (("ask_stream", "ORIGINAL_STREAM", wrapped_stream),
                              ("ask", "ORIGINAL_ASK", wrapped_ask)):
        current = getattr(Muice, attr, None)
        if current is None:
            logger.error(f"[model] Muice 上没有 {attr}，MuiceBot 换名字了？降级未生效")
            continue
        if getattr(current, "_model_pref", False):
            continue
        shell.__wrapped__ = current
        shell._model_pref = True
        globals()[slot] = current
        setattr(Muice, attr, shell)
        n += 1
        logger.info(f"[model] 已套壳 Muice.{attr}（原始实现留在 {slot} 上）")
    return n


COUNT = install()
ROWS = gl.register_help_rows(mp.help_rows())  # 导入时就注册：`.help` 那条 matcher 可能比 on_startup 先跑


# ---------------------------------------------------------------- 私聊指令


def _text(event: Event) -> str:
    try:
        return event.get_plaintext() or ""
    except Exception:  # noqa: BLE001 - 不是消息事件就没文本
        return ""


def _is_private(event: Event) -> bool:
    return getattr(event, "message_type", None) == "private"


def _nicknames() -> tuple[str, ...]:
    raw = getattr(driver.config, "muice_nicknames", None) or []
    names = tuple(str(n).strip() for n in raw if str(n).strip())
    return names or ("达妮娅",)


def _superusers() -> set[str]:
    return {str(u) for u in getattr(driver.config, "superusers", None) or set()}


def _args(event: Event) -> list[str] | None:
    if not _is_private(event):
        return None
    if str(event.get_user_id()) not in _superusers():
        return None  # 换模型是主人的事；权限判定放在规则里，匹配器上的 SUPERUSER 是第二道
    return mp.parse_model_command(_text(event), _nicknames())


async def is_model_command(event: Event) -> bool:
    return _args(event) is not None


def _slot_text(s: dict, key: str) -> str:
    """日志里的 `relay（gpt-5.6-terra）`：条目名 + 主人后来点的真模型名。"""
    name = s.get(key)
    if not name:
        return "未设置"
    want = s.get(f"{key}_model")
    return f"{name}（{want}）" if want else str(name)


def run_command(args: list[str], upstream=mp.UNTRIED) -> str:  # noqa: ANN001 - 见 list_upstream
    """执行一条 `模型 …`（args 已去掉「模型」本身），改了就落盘 + 当场生效，回一句给人看的话。

    `upstream` 是 `list_upstream()` 的返回：`(来源条目, [真模型名])`；`None` = 去了但没拉到；
    `mp.UNTRIED` = 压根没去问（测试里直接调这层）。没拉到时会把**上次**拉到的名字列出来给人看，
    但不拿它当依据写盘 —— 网关可能已经把这个模型摘掉了，存进去就是下一回合她不回话。
    """
    s = state()
    changed, reply = mp.apply_command(
        s, args, entry_names(),
        details=entry_details(), current=current_name(),
        enabled=_flag(), timeout=_timeout(), upstream=upstream,
    )
    if upstream is None and _UPSTREAM_CACHE:
        reply += ("\n上次拉到的上游真模型：" + "、".join(_UPSTREAM_CACHE)
                  + "（这次网关没答，所以只能按条目名设；网关起来了再试）")
    if changed:
        _persist()
        try:
            ensure_primary(_muice())
        except Exception as e:  # noqa: BLE001 - 已经落盘了，当场没切过去下一回合也会自愈
            logger.warning(f"[model] 当场切模型没成功，下一回合开头会再试：{e}")
    logger.info(f"[model] 指令 模型 {' '.join(args)} → {'已改' if changed else '未改'}"
                f"｜首选 {_slot_text(s, 'primary')}｜次选 {_slot_text(s, 'secondary')}")
    return reply


model_cmd = on_message(rule=is_model_command, priority=1, block=True, permission=SUPERUSER)


async def command_reply(args: list[str]) -> str:
    """指令这条路：先问一次上游清单，再执行。

    单独成一个函数是因为「问网关」必须挂在真机上跑的那条路上 —— 只在测试里手动传
    `upstream=` 的话，真机永远走 `UNTRIED`，主人点 `首选 gpt-5.6-terra` 照旧被拒绝。
    """
    return run_command(args, upstream=await list_upstream())


@model_cmd.handle()
async def handle_model_command(event: Event) -> None:
    args = _args(event) or []
    await model_cmd.finish(await command_reply(args[1:]))


# ---------------------------------------------------------------- 启动


@driver.on_startup
async def setup_model_pref() -> None:
    """补套壳（导入时 Muice 可能还没被拉起来）+ 把指令挂进 `.help`。"""
    install()
    gl.register_help_rows(mp.help_rows())
    s = state()
    logger.success(
        "[model] 模型首选/次选已生效"
        f"｜首选 {_slot_text(s, 'primary')}"
        f"｜次选 {(_slot_text(s, 'secondary')) if s.get('secondary') else '未设置（报错时不降级）'}"
        f"｜自动降级 {'开' if _flag() else '关'}（首字超时 {_timeout():g}s）"
        f"｜状态文件 {STATE_PATH}"
    )
    if not _superusers():
        logger.warning("[model] SUPERUSERS 是空的：没人能设首选/次选")
