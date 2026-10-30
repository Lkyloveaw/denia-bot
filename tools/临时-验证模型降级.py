r"""临时验证脚本（不发给 QQ、不动生产配置、不调真模型额度）：
用**真实的 MuiceBot 对象**跑一遍 `model_pref` 的换人管线。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe -X utf8 ..\tools\临时-验证模型降级.py

覆盖的是接线测试覆盖不到的那半截：真的 `ModelConfig` / 真的 provider 类 / 真的 `_echo`，
以及「首选坏了」在真实 provider 上到底长成什么样（连接被拒 → 我们的判据认不认）。
`_echo` 不调外网，dead 那条指向没人监听的端口，所以一分钱额度都不花。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent / "nonebot")
sys.path.insert(0, os.getcwd())

import nonebot  # noqa: E402

nonebot.init()
nonebot.load_plugin("muicebot")

import model_pref_logic as mp  # noqa: E402
import plugins.model_pref as pref  # noqa: E402
from muicebot.llm import ModelRequest, load_model  # noqa: E402
from muicebot.muice import Muice  # noqa: E402

FAILED: list[str] = []


def check(label, cond, extra=""):
    print(("  ok    " if cond else "  FAIL  ") + label + (f" → {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(label)


async def main():
    muice = Muice.get_instance()
    print(f"\n真机对象：当前生效条目 {muice.model_config_name}"
          f"｜人设 {muice.template}｜provider {muice.model_provider}")

    check("Muice.ask_stream 被套壳", getattr(Muice.ask_stream, "_model_pref", False))
    check("Muice.ask 被套壳", getattr(Muice.ask, "_model_pref", False))
    check("原始实现留得住", getattr(Muice.ask_stream, "__wrapped__", None) is not None)
    check("再装一次是空操作", pref.install() == 0)

    # 1) 真的 build_pair：models.yml 里那条 _echo，不调外网
    cfg, model = pref.build_pair("echo-test")
    check("build_pair(echo-test) 拿到 provider", type(model).__name__.lower().startswith("echo"),
          type(model).__name__)
    snap = pref._apply(muice, cfg, model)
    check("换上去之后 model_config 是那条 _echo", muice.model_config is cfg)
    check("人设没被换掉（沿用首选的 Denia）", muice.template == "Denia", muice.template)
    check("条目名不由 _apply 偷偷改（恢复交得出去）", muice.model_config_name == "relay",
          muice.model_config_name)
    pref._restore(muice, {"snap": snap})
    check("恢复回原来的 provider", muice.model_config_name == "relay" and muice.model_provider == "openai",
          f"{muice.model_config_name}/{muice.model_provider}")

    # 2) 真的 _echo 跑一趟：证明确实「有产出」，我们的 usable() 认它是成功
    got = {"text": "", "streamed": False, "error": ""}
    src = await model.ask(ModelRequest(prompt="降级管线自检"), stream=True)
    async for _ in mp.drive(src, got, timeout=10):
        pass
    check("_echo 那一趟被判成成功", got["streamed"] and not got["error"], got)

    # 3) 真的坏首选：没人监听的端口 → 真实 provider 会怎么坏？（这就是线上「已读不回」的长相）
    from muicebot.llm import ModelConfig

    dead_cfg = ModelConfig(provider="openai", model_name="no-such-model",
                           api_key="sk-invalid", api_host="http://127.0.0.1:59999/v1",
                           template="Denia", stream=True)
    dead = load_model(dead_cfg)
    got2 = {"text": "", "streamed": False, "error": ""}
    src2 = await dead.ask(ModelRequest(prompt="这句话不该到模型"), stream=True)
    async for _ in mp.drive(src2, got2, timeout=10):
        pass
    print(f"  真机坏法：error={got2['error'][:70]!r} streamed={got2['streamed']} "
          f"chunks={len(got2['text'])} 字")
    # 连接被拒时 provider 把异常吞了、一个 chunk 都不给（error 是空串），
    # 所以这里只认「判据说要降级」，不认它长的是哪一种理由 —— 两种坏法都得救。
    ok2, why2 = mp.need_failover({"primary": "dead-test", "secondary": "echo-test"},
                                 error=got2["error"], streamed=got2["streamed"], text=got2["text"])
    check("连接被拒被判成没产出（会走降级）", ok2 and not got2["streamed"], f"{ok2}/{got2}")
    check("need_failover 给得出理由", bool(why2.strip()), why2)
    print(f"  降级理由：{why2}")

    # 4) 挂住不报错那种：本地开一个「只收连接、永远不答」的端口，超时必须掐得断
    #    （不打真网关，免得白烧额度 —— 线上最常见的坏法恰恰是「有连接、没回音」）
    import socket

    sink = socket.socket()
    sink.bind(("127.0.0.1", 0))
    sink.listen(8)
    port = sink.getsockname()[1]
    accepted = []

    def soak():
        while True:
            try:
                conn, _ = sink.accept()
                accepted.append(conn)      # 收下连接，然后一句话都不回
            except OSError:
                return

    import threading

    threading.Thread(target=soak, daemon=True).start()
    got3 = {"text": "", "streamed": False, "error": ""}
    hang_cfg = ModelConfig(provider="openai", model_name="no-such-model",
                           api_key="sk-invalid", api_host=f"http://127.0.0.1:{port}/v1",
                           template="Denia", stream=True)
    hang = load_model(hang_cfg)
    src3 = await hang.ask(ModelRequest(prompt="这句不会有回音"), stream=True)
    async for _ in mp.drive(src3, got3, timeout=1.5):
        pass
    sink.close()
    for c in accepted:
        c.close()
    print(f"  首字超时分支：error={got3['error'][:60]!r} streamed={got3['streamed']}")
    check("超时能掐断并留下理由", bool(got3["error"]) and "首字超时" in got3["error"], got3)

    # 5) 指令读的是真条目表（不落盘：只发「查看」）
    reply = pref.run_command(["查看"])
    check("模型 查看 能读到真 models.yml 的条目", "relay" in reply and "echo-test" in reply, reply[:120])
    check("模型 查看 认不出假条目", "可选条目" in pref.run_command(["首选", "并不存在"]))

    print(f"\n{'全绿：真机对象上这套管线成立' if not FAILED else str(len(FAILED)) + ' 项没通过'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
