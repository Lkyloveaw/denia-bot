r"""一次性验证：`.模型 首选 gpt-5.6-terra`（上游真模型名）这条路在真机上通不通。

    cd D:\Software\qqbot\nonebot
    .venv\Scripts\python.exe ..\tools\临时-验证上游模型名.py

全程只读、不发消息、不碰 `data/model_pref.json`（状态文件重定向到临时目录）：
拿 `configs/models.yml` 里 `relay` 那条**真实造一个 provider 对象**，用它自带的 openai
客户端去问 AxonHub 的 `/v1/models`，再把主人想点的真名按到 `provider.model` 上读回来。
Key 和 api_host 都不打印（打印了也没意义，只会污染日志）。

为什么值得单独跑一次：`tests\test_model_pref_wiring.py` 里的 provider 全是本地造的替身，
「真机那条路通不通」这件事替身证不了 —— 尤其是 `gpt-5.6-terra` 到底在不在上游清单里，
这正是主人这次被卡住的地方。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

NB = Path(__file__).resolve().parent.parent / "nonebot"
os.chdir(NB)
sys.path.insert(0, str(NB))

import nonebot  # noqa: E402

nonebot.init()
nonebot.load_plugin("muicebot")

import model_pref_logic as mp  # noqa: E402
import plugins.model_pref as pref  # noqa: E402

FAIL = 0


def check(name: str, cond, detail: str = "") -> None:
    global FAIL
    if cond:
        print(f"  ok    {name}" + (f"｜{detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"｜{detail}" if detail else ""))


class Holder:
    """最小一点的「像 Muice 的对象」：套壳层只用到 model / model_config_name 这两样。"""

    def __init__(self, model, name):
        self.model = model
        self.model_config_name = name


def main() -> int:
    # 状态文件挪走，别拿真的 data/model_pref.json 做实验
    pref.STATE_PATH = Path(tempfile.mkdtemp()) / "model_pref.json"
    pref._state_cache = None

    names = pref.entry_names()
    check("models.yml 的条目读得到", bool(names), f"条目：{'、'.join(names) or '（空）'}")

    entry = next((n for n in names if n.lower() == "relay"), (names[0] if names else None))
    if not entry:
        check("有条目可用", False, "models.yml 是空的，后面的都跑不了")
        return 1

    cfg, provider = pref.build_pair(entry)
    check(f"{entry} 的 provider 造出来了", provider is not None,
          f"provider={type(provider).__name__} models.yml 写的名字={getattr(provider, 'model', '?')}")

    got = asyncio.run(pref.list_upstream(Holder(provider, entry)))
    check("上游清单拉到了（复用 provider 自带的客户端）", got is not None,
          f"返回 {type(got).__name__}")
    if got is None:
        check("拉不到时指令仍能按条目名选", True, "见 tests\\test_model_pref_wiring.py 的对应断言")
        print("\n网关没答：确认 AxonHub（:8090）起着、`relay` 那条的 Key 有效，再重跑这个脚本。")
        return 1

    src, ids = got
    print(f"  来源条目 {src}｜上游 {len(ids)} 个模型：{'、'.join(ids[:12])}"
          + ("…" if len(ids) > 12 else ""))
    check("缓存里留住了这份清单", pref._UPSTREAM_CACHE == ids, f"{len(pref._UPSTREAM_CACHE)} 个")

    for want in ("gpt-5.6-terra",):
        hit = want in ids
        check(f"上游认识 {want}", hit, "不在清单里就只能按条目名选" if not hit else "能点")
        if not hit:
            continue

        # 主人那句话在真机上的完整一趟：解析 → 落状态 → 按名字
        target = mp.resolve_target(want, names, (src, ids))
        check("resolve_target 把它认成「真模型名 + 挂在来源条目」",
              target.get("kind") == "model" and target.get("model") == want
              and target.get("entry") == src, str(target))

        state = mp.new_state()
        changed, reply = mp.apply_command(state, ["首选", want], names,
                                          details=pref.entry_details(), current=entry,
                                          upstream=(src, ids))
        check("apply_command 改得动状态", changed and state["primary"] == src
              and state["primary_model"] == want,
              f"首选 {state['primary']}／真名 {state['primary_model']}")
        check("回执里说清了用的是哪个真名", want in reply, "、".join(reply.splitlines()[:2]))

        before = getattr(provider, "model", None)
        mp.save_state(pref.STATE_PATH, state)
        pref._state_cache = None
        h = Holder(provider, src)
        pref.ensure_primary(h)  # 每回合开头那一步：条目已对 → 只补真名
        check("ensure_primary 把真名按到了 provider 上",
              getattr(provider, "model", None) == want, f"{before} → {getattr(provider, 'model', None)}")
        check("发请求用的还是同一条目（中转/Key/人设没被牵动）",
              h.model_config_name == src, h.model_config_name)

    print(f"\n{'全部通过' if FAIL == 0 else f'{FAIL} 项没通过'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
