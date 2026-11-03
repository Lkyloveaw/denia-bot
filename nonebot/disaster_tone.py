# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""首报/后续口吻判定。

规则由 `docs/research/disaster-v1-ux-spec.md` 定稿：
- 首报：中性官方；
- 后续更新：达妮娅；
- 地震 EEW 第 1 报中性，第 2 报起达妮娅；CENC/JMA 正式测定/情报中性。
"""
from __future__ import annotations

from typing import Any, Mapping

NEUTRAL = 'neutral'
CHARACTER = 'character'


def earthquake_tone(event: Any) -> str:
    source = str(getattr(event, 'source', '') or '')
    if source.endswith('_eew'):
        try:
            report = int(getattr(event, 'report_num', 1) or 1)
        except (TypeError, ValueError):
            report = 1
        if report > 1:
            return CHARACTER
    return NEUTRAL


def identity_key(kind: str, item: Any) -> str:
    if kind == 'weather':
        return f'voice:weather:{getattr(item, "alert_id", "")}'
    if kind == 'tsunami':
        return f'voice:tsunami:{getattr(item, "alert_id", "")}'
    if kind == 'typhoon':
        return f'voice:typhoon:{getattr(item, "typhoon_id", "")}'
    return f'voice:{kind}:{id(item)}'


def tone_for(kind: str, item: Any, seen: Mapping[str, Any]) -> tuple[str, str | None]:
    """返回 (tone, identity)。identity 由调用方在首次处理后标记。"""
    if kind == 'earthquake':
        return earthquake_tone(item), None
    key = identity_key(kind, item)
    return (CHARACTER if key in seen else NEUTRAL), key


def subsequent_tone() -> str:
    return CHARACTER
