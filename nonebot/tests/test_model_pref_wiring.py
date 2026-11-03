r"""`plugins/model_pref` 接线的断言测试 —— 用假的 provider / 假的 Muice 跑套壳后的调用，直接跑：

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe tests\test_model_pref_wiring.py

跟 `test_model_pref_logic.py` 的分工：那边管「该不该降级、指令改不改得动状态」，
这边管**换人这件事在真实对象身上做不做得干净**：
`Muice.model` / `model_config` / `template` 有没有被换掉又恢复、
`models.yml` 把 `default:` 打回时会不会自愈、`.模型` 指令是不是只有主人在私聊里能用。

不连 QQ、不调真模型：provider 全是本地造的假对象，状态文件挪到临时目录。
`Muice.ask_stream` 的真实实现要数据库会话和钩子上下文，那是 MuiceBot 自己的事，
这里把 `ORIGINAL_STREAM` / `ORIGINAL_ASK` 换成「照同样规矩在调用时读 `self.model`」的替身，
壳的行为照样能被完整验出来。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)  # MuiceBot 按 ./plugins 找插件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot
ensure_nonebot()
import gate_logic as gl  # noqa: E402
import model_pref_logic as mp  # noqa: E402
import plugins.gate as gate  # noqa: E402
import plugins.model_pref as pref  # noqa: E402
from nonebot.adapters.onebot.v11 import (  # noqa: E402
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from muicebot.muice import Muice  # noqa: E402

MASTER = "10000001"
OTHER = "3865067623"
GROUP = 741096253
SELF = 3892894069
FAILED: list[str] = []


def run(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAILED.append(f"{name}: {e}")
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        FAILED.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
    else:
        print(f"  ok    {name}")


# ---------------------------------------------------------------- 替身


class Chunk:
    def __init__(self, chunk=""):
        self.chunk = chunk
        self.usage = 1
        self.resources = []
        self.succeed = True


class Answer:
    """像 `ModelCompletions`（非流式的返回值）。"""

    def __init__(self, text="", succeed=True):
        self.text, self.succeed, self.usage, self.resources = text, succeed, 1, []


class FakeClient:
    """替 `openai.AsyncOpenAI` 那份客户端：只被 `client.models.list()` 用到。"""

    def __init__(self, ids=(), boom=None, hang=False):
        self.models = self._Models(ids, boom, hang)
        self.calls = 0

    class _Models:
        def __init__(self, ids, boom, hang):
            self.ids, self.boom, self.hang, self.list_calls = ids, boom, hang, 0

        async def list(self):
            self.list_calls += 1
            if self.boom:
                raise self.boom
            if self.hang:  # 网关挂了不答：指令不能被悬在这儿
                await asyncio.sleep(30)
            return type("R", (), {"data": [type("M", (), {"id": i})() for i in self.ids]})()


class FakeModel:
    """流式 provider 的替身：`ask(request, stream=True)` 返回异步生成器。

    `.model` 是**真模型名**（`llm/providers/openai.py:38` 把 `config.model_name` 挂在这上面，
    `:137`/`:221` 发请求那一刻才读），`.client` 是那份 openai 客户端 —— 问上游能点哪些模型
    就靠它，Key 和地址都在里面，我们不用另存一份。
    """

    is_running = True

    def __init__(self, words=(), boom=None, silent=False, hang=False, model="relay", client=None):
        self.words, self.boom, self.silent, self.hang = words, boom, silent, hang
        self.model, self.client = model, client
        self.calls = 0

    async def ask(self, request, *, stream=True):  # noqa: ANN001, ARG002
        self.calls += 1
        if self.silent:
            return
        if self.hang:  # 「挂住不报错」那种坏法：一个字都不给，也不抛
            await asyncio.sleep(30)
            yield Chunk("永远到不了的一句")
            return
        for w in self.words:
            yield Chunk(w)
        if self.boom:
            raise self.boom

    def load(self):
        return True


class SyncModel:
    """非流式 provider 的替身：`ask(..., stream=False)` 直接返回一个 Answer。"""

    is_running = True

    def __init__(self, text=None, boom=None, succeed=None, model="relay"):
        self.text, self.boom = text, boom
        self.model = model
        self.succeed = (self.text is not None) if succeed is None else succeed
        self.calls = 0

    def ask(self, request, *, stream=False):  # noqa: ANN001, ARG002
        self.calls += 1
        if self.boom:
            raise self.boom
        return Answer(self.text or "", succeed=self.succeed)

    def load(self):
        return True


class FakeConfig:
    def __init__(self, name="relay", template="Denia", provider="openai", template_mode="system"):
        self.model_name = name
        self.template = template
        self.provider = provider
        self.template_mode = template_mode
        self.multimodal = False
        self.function_call = False
        self.stream = True


class FakeMuice:
    """只实现壳真正会碰到的那几样：model / model_config / template / change_model_config。"""

    def __init__(self, model, name="relay"):
        cfg = FakeConfig(name=name)
        self.model = model
        self.model_config = cfg
        self.model_config_name = name
        self.model_provider = cfg.provider
        self.template = cfg.template
        self.template_mode = cfg.template_mode
        self.multimodal = False
        self.changes = []

    def change_model_config(self, name=None, reload=False):  # noqa: ANN001, ARG002 - 与 Muice 同名同参
        self.changes.append(name)
        self.model_config_name = name


async def fake_ask_stream(self, session, message, enable_history=True, enable_plugins=True):
    """替身版的 `Muice.ask_stream`：跟真的一样在**开始迭代时**才读 `self.model`。"""
    async for c in self.model.ask(None, stream=True):
        yield c


async def fake_ask(self, session, message, enable_history=True, enable_plugins=True):
    return self.model.ask(None, stream=False)


def use_temp_state(**over):
    """状态文件挪到临时目录，别拿真 data/model_pref.json 做实验。"""
    path = Path(tempfile.mkdtemp()) / "model_pref.json"
    pref.STATE_PATH = path
    pref._state_cache = None
    s = mp.new_state()
    s.update({"primary": None, "secondary": None}, **over)
    mp.save_state(path, s)
    pref._state_cache = None
    pref.build_pair = lambda name: (_ for _ in ()).throw(AssertionError("这个用例不该换人"))
    pref.ORIGINAL_STREAM = fake_ask_stream
    pref.ORIGINAL_ASK = fake_ask
    return path


def env_over(**over):
    """临时改 `.env` 里的那些开关（`MODEL_FAILOVER` / `MODEL_FAILOVER_TIMEOUT`），返回还原函数。

    值为 `None` 表示「当成文件里没写这个键」—— `_flag` / `_timeout` 见 None 才走默认值。
    """
    missing = "<没有这个键>"
    cfg = nonebot.get_driver().config
    saved = {k: getattr(cfg, k, missing) for k in over}
    for k, v in over.items():
        setattr(cfg, k, v)

    def restore():
        for k, v in saved.items():
            if v is missing:
                try:
                    delattr(cfg, k)
                except Exception:  # noqa: BLE001 - pydantic 不给删就写回 None，效果一样
                    setattr(cfg, k, None)
            else:
                setattr(cfg, k, v)

    return restore


def collect(src):
    """把异步生成器跑完，返回转出去的正文。"""

    async def go():
        out = []
        async for c in src:
            out.append(getattr(c, "chunk", "") or "")
        return "".join(out)

    return asyncio.run(go())


# ---------------------------------------------------------------- 套壳


def t_install_wraps_both_model_entry_points():
    assert getattr(Muice.ask_stream, "_model_pref", False), "ask_stream 没被套壳，降级永远不会发生"
    assert getattr(Muice.ask, "_model_pref", False), "非流式的 ask 也要管，models.yml 里 stream: false 的条目照样会用到"
    assert pref.install() == 0, "重复加载不能套两层（两层会各自换人一次）"
    assert getattr(Muice.ask_stream, "__wrapped__", None) is not None, "要留住原始实现，回滚和排查才找得到"


def t_the_wrapper_only_reaches_the_model_once_per_turn():
    """首选正常回话时，套壳不该多跑一趟、也不该碰次选。"""
    use_temp_state(primary="relay", secondary="echo-test")
    good = FakeModel(words=["嗯，我在的"])
    fake = FakeMuice(good)
    assert collect(pref.wrapped_stream(fake, None, None)) == "嗯，我在的"
    assert good.calls == 1, f"成功那一趟只该调一次模型：{good.calls}"
    assert fake.changes == [], f"对了就别再切全局：{fake.changes}"


def t_a_silent_primary_gets_rescued_and_the_state_is_put_back():
    bad = FakeModel(silent=True)
    hero = FakeModel(words=["（次选救场）"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)

    out = collect(pref.wrapped_stream(fake, None, None))
    assert out == "（次选救场）", f"次选的话要转出去：{out}"
    assert hero.calls == 1 and bad.calls == 1, f"首选跑一趟发现静默，次选补一趟：{bad.calls}/{hero.calls}"
    assert fake.model is bad, "跑完必须把首选的模型对象还回来"
    assert fake.template == "Denia", "换模型不能把人设也换掉"
    assert fake.model_config_name == "relay", "生效条目名也要恢复"


def t_a_raising_primary_gets_rescued_too():
    bad = FakeModel(boom=RuntimeError("模型调用器返回的值应至少包含一个元素"))
    hero = FakeModel(words=["补上的回复"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    assert collect(pref.wrapped_stream(fake, None, None)) == "补上的回复"
    assert fake.model is bad


def t_boilerplate_only_replies_count_as_failure():
    """MuiceBot 空回复时吐的那句「模型回复为空…」不算产出，照样该降级。"""
    bad = FakeModel(words=["模型回复为空，可能是模型未能正确处理请求，请检查模型配置或输入内容。"])
    hero = FakeModel(words=["正常的一句"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    out = collect(pref.wrapped_stream(fake, None, None))
    assert out == "正常的一句", f"次选顶上时不该留那句系统提示：{out!r}"


def t_the_boilerplate_still_goes_out_when_nobody_rescues():
    """没人救场时别改 MuiceBot 原来的样子 —— 那句「模型回复为空」至少说明她看见了。"""
    bad = FakeModel(words=["模型回复为空，可能是模型未能正确处理请求，请检查模型配置或输入内容。"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary=None)
    out = collect(pref.wrapped_stream(fake, None, None))
    assert out.startswith("模型回复为空"), f"没设次选时保持原样：{out!r}"


def t_a_half_sent_reply_is_never_restarted():
    """首选说了半句才断 —— 宁可那半句难看，也不能再补一整段。"""
    bad = FakeModel(words=["前半句话"], boom=RuntimeError("流断了"))
    hero = FakeModel(words=["后半段"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    out = collect(pref.wrapped_stream(fake, None, None))
    assert out == "前半句话", f"已经吐过字就收手：{out}"
    assert hero.calls == 0, "次选不该被叫起来"


def t_the_secondary_inherits_the_personality_unless_it_has_its_own():
    hero = FakeModel(words=["救场"])
    fake = FakeMuice(bad := FakeModel(silent=True))
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name, template="Muice"), hero)
    seen = {}
    orig_apply = pref._apply

    def spy(muice, cfg, model):
        snap = orig_apply(muice, cfg, model)
        seen["template"] = muice.template
        return snap

    pref._apply = spy
    try:
        collect(pref.wrapped_stream(fake, None, None))
    finally:
        pref._apply = orig_apply
    assert seen.get("template") == "Denia", f"次选条目没配人设时要沿用首选的：{seen}"

    # 次选自己配了人设就别抢
    pref._apply = orig_apply
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name, template="Other"), hero)
    collect(pref.wrapped_stream(fake, None, None))


def t_the_switch_and_a_missing_secondary_both_keep_hands_off():
    bad = FakeModel(silent=True)
    hero = FakeModel(words=["不该出现"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)

    restore = env_over(model_failover="0")
    try:
        assert collect(pref.wrapped_stream(fake, None, None)) == ""
        assert hero.calls == 0, "MODEL_FAILOVER=0 时一个字节都不该动"
    finally:
        restore()

    use_temp_state(primary="relay", secondary=None)
    assert collect(pref.wrapped_stream(fake, None, None)) == ""
    assert hero.calls == 0, "没设次选就维持原来的静默"


def t_failover_is_on_even_when_the_env_says_nothing():
    """`.env` 里没写 `MODEL_FAILOVER` 这个键时默认必须是「开」。

    这层是给「首选坏了她就不说话」兜底的，默认关等于装了个不响的保险丝；
    关掉得是主人明确写 `MODEL_FAILOVER=0`，不是忘了写。
    """
    bad = FakeModel(silent=True)
    hero = FakeModel(words=["默认就该救场"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    restore = env_over(model_failover=None)  # None = 当成文件里没这个键
    try:
        assert collect(pref.wrapped_stream(fake, None, None)) == "默认就该救场"
    finally:
        restore()


def t_restoring_with_a_bare_snapshot_still_works():
    """`_apply` 给的是快照，`_restore` 要的是 holder —— 传错了不许装作恢复了。

    （写临时验证脚本时真踩过：`_restore(muice, snap)` 一声不响什么都不做，
    机器人留在半换状态，检查却报「恢复了」。一个判断换掉这个坑。）
    """
    bad = FakeModel(words=["x"])
    hero = FakeModel(words=["y"])
    fake = FakeMuice(bad)
    snap = pref._apply(fake, FakeConfig(name="echo-test", provider="_echo"), hero)
    assert fake.model is hero and fake.model_provider == "_echo", "先确认 _apply 真的换上了"
    pref._restore(fake, snap)  # 故意传快照而不是 holder
    assert fake.model is bad, "传错形态也要把人换回来"
    assert fake.model_provider == "openai", f"provider 也得回去：{fake.model_provider}"


def t_a_primary_that_hangs_without_erroring_still_gets_rescued():
    """「挂住不报错」才是中转站最常见的坏法 —— 超时那一跳必须真的传到跑模型的地方。

    只测报错分支会漏掉这种：provider 一个 chunk 都不给、也不抛异常，
    没有超时的话 `await` 就永远悬在那儿，降级永远不会发生。
    """
    bad = FakeModel(hang=True)
    hero = FakeModel(words=["挂住也救回来了"])
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    restore = env_over(model_failover_timeout="0.05")
    try:
        assert collect(pref.wrapped_stream(fake, None, None)) == "挂住也救回来了"
    finally:
        restore()
    assert hero.calls == 1, "次选只补一趟"


def t_a_broken_secondary_leaves_her_in_one_piece():
    """次选那条被删了 / 加载不出来：不许把机器人留在半换状态，也不许把异常抛给用户。"""
    bad = FakeModel(silent=True)
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="gone")

    def boom(name):
        raise FileNotFoundError(f"没有这个模型配置: {name}")

    pref.build_pair = boom
    out = collect(pref.wrapped_stream(fake, None, None))
    assert out == "", f"次选加载失败时维持静默：{out!r}"
    assert fake.model is bad, "换人失败也要把首选的模型对象还回来"
    assert fake.template == "Denia", "人设不能被半途改掉"
    assert not pref._IN_RESCUE, "失败了还要把「正在降级」的标记清掉，否则自愈会一直罢工"


# ---------------------------------------------------------------- 非流式那条路


def t_the_non_streaming_path_rescues_too():
    bad = SyncModel(boom=RuntimeError("非流式也炸"))
    hero = SyncModel(text="次选的非流式回复")
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    out = asyncio.run(pref.wrapped_ask(fake, None, None))
    assert out.text == "次选的非流式回复", out.text
    assert fake.model is bad, "跑完恢复首选"


def t_the_non_streaming_path_leaves_a_good_answer_alone():
    good = SyncModel(text="正常的一句")
    fake = FakeMuice(good)
    use_temp_state(primary="relay", secondary="echo-test")
    out = asyncio.run(pref.wrapped_ask(fake, None, None))
    assert out.text == "正常的一句"
    assert good.calls == 1, f"成功了不该再跑一趟：{good.calls}"


def t_an_unsuccessful_non_stream_answer_gets_rescued():
    bad = SyncModel(text=None)  # 空正文
    hero = SyncModel(text="补上的一句")
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    out = asyncio.run(pref.wrapped_ask(fake, None, None))
    assert out.text == "补上的一句", out.text


def t_a_non_stream_answer_that_admits_failure_is_no_answer():
    """正文像话但模型自己回了 `succeed=False`（第三方 provider 会把上游错误当正文返回）：不算回了话。"""
    bad = SyncModel(text="upstream connect error or disconnect/reset before headers", succeed=False)
    hero = SyncModel(text="补上的一句")
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", secondary="echo-test")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    out = asyncio.run(pref.wrapped_ask(fake, None, None))
    assert out.text == "补上的一句", f"模型都说自己失败了，别把那句错误当回复：{out.text}"


# ---------------------------------------------------------------- 自愈


def t_primary_is_forced_back_when_models_yml_clobbered_it():
    """存一遍 models.yml 会把当前模型打回 `default:`（`muice.py:117`），下一轮必须自己切回来。"""
    use_temp_state(primary="relay", secondary="echo-test")
    fake = FakeMuice(FakeModel(words=["x"]), name="echo-test")
    pref.ensure_primary(fake)
    assert fake.changes == ["relay"], f"该切回首选：{fake.changes}"


def t_ensure_primary_does_not_touch_an_already_correct_setup():
    use_temp_state(primary="relay", secondary=None)
    fake = FakeMuice(FakeModel(words=["x"]), name="relay")
    pref.ensure_primary(fake)
    assert fake.changes == [], "已经对了就别去动全局"


def t_a_reloaded_entry_still_gets_its_real_name_back():
    """条目没被打回、只有 provider 被重建（`_init_model` 的常见结果）：光比条目名不够。

    真机上这就是「上午点的 gpt-5.6-terra，下午悄悄变回 gpt-5.6-sol」的来路 ——
    `change_model_config` 不在候选里（条目本来就对），补名字只能靠这条路。
    """
    hero = FakeModel(words=["x"], model="gpt-5.6-sol")  # 重建后回到 models.yml 写的名字
    fake = FakeMuice(hero)
    use_temp_state(primary="relay", primary_model="gpt-5.6-terra")
    pref.ensure_primary(fake)
    assert fake.changes == [], "条目本来就是对的，不该再切一次全局"
    assert fake.model.model == "gpt-5.6-terra", f"真名要补回来：{fake.model.model}"


def t_ensure_primary_stands_down_while_someone_is_rescuing():
    """别人正在跑次选那一趟时，新到的消息不能把模型又切回首选 —— 会打断对方。"""
    use_temp_state(primary="relay", secondary="echo-test")
    fake = FakeMuice(FakeModel(words=["x"]), name="echo-test")
    pref._IN_RESCUE.append(object())
    try:
        pref.ensure_primary(fake)
    finally:
        pref._IN_RESCUE.clear()
    assert fake.changes == [], "降级进行中，ensure_primary 必须装看不见"


def t_ensure_primary_survives_a_vanished_entry():
    use_temp_state(primary="gone", secondary=None)
    fake = FakeMuice(FakeModel(words=["x"]), name="relay")
    pref.ensure_primary(fake)  # 不许抛
    assert fake.changes == [], "models.yml 里已经没有首选那条了，就别硬切"


def t_ensure_primary_is_quiet_when_nothing_is_configured():
    use_temp_state()  # 首选都没设：完全不介入，MuiceBot 自己说了算
    fake = FakeMuice(FakeModel(words=["x"]), name="echo-test")
    pref.ensure_primary(fake)
    assert fake.changes == []


# ---------------------------------------------------------------- 指令


def event(text, *, user=MASTER, group=None):  # noqa: ANN001, ANN201 - 与 test_gate_wiring 同法造事件
    seg = Message([MessageSegment.text(text)])
    common = dict(time=1700000000, self_id=SELF, post_type="message", message_id=1,
                  user_id=int(user), message=seg, original_message=seg, raw_message=text, font=0)
    if group is None:
        return PrivateMessageEvent(
            message_type="private", sub_type="friend", to_me=True,
            sender={"user_id": int(user), "nickname": "对乙酰氨基酚", "card": "",
                    "role": "friend", "title": ""},
            **common,
        )
    return GroupMessageEvent(
        message_type="group", sub_type="member", group_id=group, to_me=False,
        sender={"user_id": int(user), "nickname": "别人", "card": "",
                "role": "member", "title": ""},
        **common,
    )


def t_the_command_only_fires_for_the_master_in_private():
    assert asyncio.run(pref.is_model_command(event(".模型 查看"))) is True
    assert asyncio.run(pref.is_model_command(event("模型 查看"))) is True
    assert asyncio.run(pref.is_model_command(event("达妮娅，.模型 查看"))) is True
    assert asyncio.run(pref.is_model_command(event(".模型 查看", group=GROUP))) is False, "群里不许发指令"
    assert asyncio.run(pref.is_model_command(event(".模型 查看", user=OTHER))) is False, "只有主人能改模型"
    assert asyncio.run(pref.is_model_command(event("今天天气不错"))) is False, "普通聊天不能被指令吃掉"


def t_setting_the_primary_persists_to_disk():
    path = use_temp_state(primary=None, secondary=None)
    reply = pref.run_command(["首选", "relay"])
    assert "relay" in reply, reply
    back = mp.load_state(path)
    assert back["primary"] == "relay", f"重启后要还是它：{back}"


def t_an_unknown_entry_is_refused_and_listed():
    use_temp_state(primary=None, secondary=None)
    reply = pref.run_command(["首选", "并不存在"])
    assert "并不存在" in reply and "relay" in reply, f"要把可选项报出来：{reply}"
    assert pref.state()["primary"] is None, "认不出的名字绝不能落盘"


def t_viewing_works_without_a_live_model_call():
    use_temp_state(primary="relay", secondary="echo-test")
    reply = pref.run_command(["查看"])
    for need in ("首选", "次选", "relay", "echo-test", "MODEL_FAILOVER"):
        assert need in reply, f"查看里少了「{need}」：\n{reply}"
    assert "上游可选真模型" not in reply, "没去网关拉过就别在回执里假装看过：\n" + reply


def t_help_lists_the_model_commands():
    text = gate.help_text()
    assert "模型 首选" in text, f"`.help` 里要看得见模型指令：\n{text}"
    assert text.count("模型 首选") == 1, "注册过两遍就会在帮助里出现两次"


# ---------------------------------------------------------------- 日志留痕


def t_a_rescue_always_leaves_a_log_line():
    """「她为什么这一轮说了两段 / 为什么忽然又能用了」必须能从日志里读出来，前缀 [model]。"""
    seen: list[str] = []

    class Recorder:
        def info(self, m):
            seen.append(str(m))

        def warning(self, m):
            seen.append(str(m))

        def debug(self, m):
            seen.append(str(m))

        def success(self, m):
            seen.append(str(m))

    old = pref.logger
    pref.logger = Recorder()
    try:
        pref._note(True, "首选报错：RuntimeError: 模型调用器返回的值应至少包含一个元素")
        pref._note(False, "没设次选（模型 次选 <条目名> 可以设）")
    finally:
        pref.logger = old

    assert any("[model] 已降级" in m for m in seen), seen
    assert any("echo-test" in m or "次选" in m for m in seen), f"要说用的是谁：{seen}"
    assert any("[model] 不换人" in m for m in seen), seen


# ---------------------------------------------------------------- 上游的真模型名（不是条目名）


def t_the_override_reaches_the_provider_that_actually_sends_the_request():
    """`首选 gpt-5.6-terra` 存的是「条目 relay + 覆盖真名」—— 发请求时 provider 得用那个名字。"""
    hero = FakeModel(words=["嗯"], model="gpt-5.6-sol")
    fake = FakeMuice(hero)
    use_temp_state(primary="relay", primary_model="gpt-5.6-terra")
    pref.enforce_model_name(fake)
    assert hero.model == "gpt-5.6-terra", f"真名没按上去：{hero.model}"
    assert fake.model_config_name == "relay", "条目不该被顺带改掉（中转地址/Key 跟着条目走）"


def t_no_override_means_nobody_touches_the_provider_name():
    hero = FakeModel(words=["嗯"], model="gpt-5.6-sol")
    fake = FakeMuice(hero)
    use_temp_state(primary="relay")
    pref.enforce_model_name(fake)
    assert hero.model == "gpt-5.6-sol", "主人没点过真名就别动 models.yml 写的名字"


def t_a_rebuilt_provider_gets_the_name_back():
    """有人存了一遍 `models.yml` → 实例被重建、真名打回默认那条，下一回合开头要补得回来。"""
    hero = FakeModel(words=["嗯"], model="gpt-5.6-sol")
    fake = FakeMuice(hero)
    use_temp_state(primary="relay", primary_model="gpt-5.6-terra")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    fake.model_config_name = "other"  # 当成被 `default:` 打回了别的条目
    fake.model.model = "gpt-5.6-sol"  # 重建后的样子
    pref.ensure_primary(fake)
    assert fake.changes == ["relay"], fake.changes
    assert fake.model.model == "gpt-5.6-terra", f"自愈只切条目不补真名，等于没改：{fake.model.model}"


def t_the_rescue_uses_the_secondary_real_name_and_leaves_the_primary_alone():
    """同一条目里的两个真名也能互为备份：顶上时用次选的名字，首选那个名字不许被抹掉。"""
    bad = FakeModel(silent=True, model="gpt-5.6-terra")
    hero = FakeModel(words=["次选救场"], model="gpt-5.6-sol")
    fake = FakeMuice(bad)
    use_temp_state(primary="relay", primary_model="gpt-5.6-terra",
                   secondary="relay", secondary_model="grok-4.7")
    pref.build_pair = lambda name: (FakeConfig(name=name), hero)
    assert collect(pref.wrapped_stream(fake, None, None)) == "次选救场"
    assert hero.model == "grok-4.7", f"次选那一趟得用次选的真名：{hero.model}"
    assert bad.model == "gpt-5.6-terra", f"首选的覆盖名不该被动到：{bad.model}"
    assert fake.model is bad, "跑完要回到首选那个 provider"


def t_the_upstream_list_comes_from_the_providers_own_client():
    """问「上游允许哪些模型」复用 provider 自带的 openai 客户端 —— Key 和地址都在它身上。"""
    hero = FakeModel(words=["嗯"], client=FakeClient(["gpt-5.6-terra", "gpt-5.6-sol"]))
    fake = FakeMuice(hero, name="relay")
    use_temp_state(primary="relay")
    got = asyncio.run(pref.list_upstream(fake))
    assert got == ("relay", ["gpt-5.6-terra", "gpt-5.6-sol"]), got
    assert pref._UPSTREAM_CACHE == ["gpt-5.6-terra", "gpt-5.6-sol"], "拉到过就得记住，网关黑了才有东西可报"
    pref._UPSTREAM_CACHE.clear()


def t_a_hanging_gateway_does_not_hold_the_command():
    """网关接了连接却不答：`models.list()` 得有超时，不然主人的指令一直悬着不回了。"""
    hero = FakeModel(words=["嗯"], client=FakeClient(hang=True))
    fake = FakeMuice(hero, name="relay")
    use_temp_state(primary="relay")
    old = pref.UPSTREAM_TIMEOUT
    pref.UPSTREAM_TIMEOUT = 0.02
    try:
        assert asyncio.run(pref.list_upstream(fake)) is None, "挂住要当成没拉到，不是永远等"
    finally:
        pref.UPSTREAM_TIMEOUT = old


def t_a_command_asks_the_gateway_before_answering():
    """「问上游」得挂在指令这条路上，不能只在测试里手动传 —— 不然真机上永远走 `UNTRIED`，
    主人点 `首选 gpt-5.6-terra` 依旧被拒绝，跟没修一样。"""
    use_temp_state(primary="relay")
    seen: list = []

    async def fake(muice=None):  # noqa: ANN001 - 替身，签名跟 list_upstream 一致
        seen.append(muice)
        return ("relay", ["gpt-5.6-terra"])

    old = pref.list_upstream
    pref.list_upstream = fake
    try:
        reply = asyncio.run(pref.command_reply(["首选", "gpt-5.6-terra"]))
    finally:
        pref.list_upstream = old
    assert seen == [None], f"指令里该问过网关（不带参数=用当前生效那条）：{seen}"
    assert pref.state()["primary_model"] == "gpt-5.6-terra", reply


def t_the_command_log_line_names_the_real_model():
    """「她今天到底用哪个模型」要能从日志一行读出来：条目 + 覆盖的真名。"""
    seen: list[str] = []

    class Recorder:
        def info(self, m):
            seen.append(str(m))

        warning = debug = success = info

    use_temp_state(primary="relay")
    old = pref.logger
    pref.logger = Recorder()
    try:
        pref.run_command(["首选", "gpt-5.6-terra"], upstream=("relay", ["gpt-5.6-terra"]))
    finally:
        pref.logger = old
    assert any("首选 relay（gpt-5.6-terra）" in m for m in seen), f"日志要写清条目和真名：{seen}"


def t_an_unreachable_upstream_returns_none_instead_of_raising():
    hero = FakeModel(words=["嗯"], client=FakeClient(boom=RuntimeError("网关没起")))
    fake = FakeMuice(hero, name="relay")
    use_temp_state(primary="relay")
    assert asyncio.run(pref.list_upstream(fake)) is None


def t_a_provider_without_a_client_just_says_so():
    """`_echo` 那种 provider 根本没有客户端 —— 只能按条目名选，别把指令噎住。"""
    fake = FakeMuice(FakeModel(words=["嗯"]), name="echo-test")
    use_temp_state(primary="relay")
    assert asyncio.run(pref.list_upstream(fake)) is None


def t_the_command_accepts_a_real_model_name_and_persists_it():
    path = use_temp_state(primary="relay")
    reply = pref.run_command(["首选", "gpt-5.6-terra"], upstream=("relay", ["gpt-5.6-terra"]))
    assert "gpt-5.6-terra" in reply, reply
    back = mp.load_state(path)
    assert back["primary"] == "relay" and back["primary_model"] == "gpt-5.6-terra", back


def t_the_command_refuses_a_model_name_when_the_list_did_not_arrive():
    use_temp_state(primary="relay")
    before = dict(pref.state())
    reply = pref.run_command(["首选", "gpt-5.6-terra"], upstream=None)
    assert "gpt-5.6-terra" not in str(pref.state().get("primary_model")), pref.state()
    assert pref.state()["primary"] == before["primary"], "拉不到清单就不许改状态"
    assert "条目" in reply, f"至少要把本地条目报出来：{reply}"


def t_the_last_good_upstream_list_is_shown_when_the_gateway_goes_dark():
    """上次明明拉到过，这次挂了 —— 回执要让人看见那些名字，不然主人只能干瞪眼。"""
    use_temp_state(primary="relay")
    pref._UPSTREAM_CACHE[:] = ["gpt-5.6-terra", "grok-4.7"]
    try:
        reply = pref.run_command(["首选", "gpt-5.6-terra"], upstream=None)
        assert "grok-4.7" in reply and "上次" in reply, f"要把上次拉到的名字报出来：{reply}"
        assert pref.state().get("primary_model") is None, "只看清单，不该顺手写进去"
    finally:
        pref._UPSTREAM_CACHE.clear()


def t_an_enforce_failure_never_kills_the_turn():
    """覆盖真名这一步炸了（provider 字段只读/形状变了），也不能让这条消息发不出去。"""

    class ReadOnlyModel:
        is_running = True

        def __init__(self, words):
            self.words, self.calls = list(words), 0

        def __setattr__(self, key, value):
            if key == "model":  # 当成一个不接受写动的 provider
                raise AttributeError("这个 provider 的 model 是只读的")
            object.__setattr__(self, key, value)

        async def ask(self, request, *, stream=True):  # noqa: ANN001, ARG002
            self.calls += 1
            for w in self.words:
                yield Chunk(w)

        def load(self):
            return True

    prov = ReadOnlyModel(["照常说"])
    fake = FakeMuice(prov)
    use_temp_state(primary="relay", primary_model="gpt-5.6-terra")
    assert collect(pref.wrapped_stream(fake, None, None)) == "照常说", "炸在补名字这一步也不该带走这条消息"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
