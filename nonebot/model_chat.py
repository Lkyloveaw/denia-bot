"""One-shot chat completion against the default OpenAI-compatible provider.

Each call reads ``configs/models.yml`` afresh and talks to the provider with a
short, bounded request: no global configuration is changed, nothing is written
to disk, and the API key never appears in errors.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx
import yaml

import gateway_session_logic
import model_publication
import persona_settings

MODELS_PATH = persona_settings.MODELS_PATH
MAX_MESSAGES = 20
MAX_CONTENT_CHARS = 12_000
MAX_TOKENS = 512
DEFAULT_TIMEOUT = 60.0
ROLES = frozenset({"system", "user", "assistant"})


class ChatError(RuntimeError):
    """A validated chat request could not be completed."""


def _base_url(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChatError("模型提供商未配置 base URL")
    text = value.strip()
    try:
        parsed = urlsplit(text)
        _ = parsed.port
    except ValueError:
        raise ChatError("模型提供商的 base URL 无效") from None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ChatError("模型提供商的 base URL 必须是 http:// 或 https:// 地址")
    if parsed.username or parsed.password:
        raise ChatError("模型提供商的 base URL 不能包含账号密码")
    if parsed.query or parsed.fragment:
        raise ChatError("模型提供商的 base URL 不能包含查询参数或片段")
    return text.rstrip("/")


def _api_key(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChatError("尚未配置 API Key")
    text = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ChatError("API Key 格式无效")
    return text


def _model_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChatError("模型名称不能为空")
    return value.strip()


def _normalized_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        raise ChatError("消息必须是列表")
    if not messages:
        raise ChatError("消息不能为空")
    if len(messages) > MAX_MESSAGES:
        raise ChatError(f"消息最多 {MAX_MESSAGES} 条")

    normalized = []
    total = 0
    for index, item in enumerate(messages, 1):
        if not isinstance(item, Mapping):
            raise ChatError(f"第 {index} 条消息格式无效")
        role = item.get("role")
        content = item.get("content")
        if not isinstance(role, str) or role not in ROLES:
            raise ChatError(f"第 {index} 条消息的 role 必须是 system、user 或 assistant")
        if not isinstance(content, str) or not content.strip():
            raise ChatError(f"第 {index} 条消息的 content 必须是非空文本")
        total += len(content)
        if total > MAX_CONTENT_CHARS:
            raise ChatError(f"消息内容合计不能超过 {MAX_CONTENT_CHARS} 字符")
        normalized.append({"role": role, "content": content})
    return normalized


def _provider() -> tuple[str, str, Mapping, Mapping]:
    """Read the published default entry; return (base_url, api_key, entry, configs)."""
    try:
        raw = model_publication.read_published(MODELS_PATH)
    except model_publication.UnpublishedConfig as exc:
        raise ChatError('模型配置尚未发布') from exc
    except OSError as exc:
        raise ChatError('无法读取模型配置') from exc
    try:
        data = yaml.safe_load(raw.decode('utf-8'))
    except (UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise ChatError('模型配置格式无效') from exc
    if not isinstance(data, dict) or not data:
        raise ChatError('模型配置格式无效')
    try:
        default = persona_settings._default_model(data)
    except (KeyError, StopIteration, TypeError) as exc:
        raise ChatError('模型配置格式无效') from exc
    entry = data.get(default)
    if not isinstance(entry, Mapping):
        raise ChatError('模型配置格式无效')
    return _base_url(entry.get('api_host')), _api_key(entry.get('api_key')), entry, data


def _content(payload) -> str:
    if not isinstance(payload, Mapping):
        raise ChatError("模型提供商返回格式不正确")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ChatError("模型提供商没有返回 choices")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise ChatError("模型提供商没有返回内容")
    return content


def _timeout(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ChatError("超时时间无效") from None
    if seconds <= 0:
        raise ChatError("超时时间必须大于 0")
    return seconds


async def chat(messages: list[dict], model: str | None = None, *,
               timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Ask the default provider for one non-streaming completion.

    ``model`` only overrides the model id; base URL and key still come from
    ``configs/models.yml``.  Returns the assistant content, the model the
    provider reports (or the requested id), usage when supplied, and the round
    trip time in milliseconds.
    """
    normalized = _normalized_messages(messages)
    base_url, api_key, entry, configs = _provider()
    model_id = _model_name(model) if model is not None else _model_name(entry.get("model_name"))
    seconds = _timeout(timeout)
    headers = {"Authorization": f"Bearer {api_key}"}
    relay = configs.get("relay")
    if isinstance(relay, Mapping) and gateway_session_logic.same_endpoint(
            base_url, relay.get("api_host")):
        headers["x-opencode-session"] = gateway_session_logic.anonymous_session()

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=seconds, follow_redirects=False) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": normalized,
                    "stream": False,
                    "max_tokens": MAX_TOKENS,
                },
            )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise ChatError("无法连接模型提供商") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)

    if not 200 <= response.status_code < 300:
        raise ChatError(f"模型提供商返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ChatError("模型提供商返回的不是 JSON") from exc
    content = _content(payload)

    reported = payload.get("model")
    usage = payload.get("usage")
    return {
        "content": content,
        "model": reported.strip() if isinstance(reported, str) and reported.strip() else model_id,
        "usage": dict(usage) if isinstance(usage, Mapping) else None,
        "latency_ms": latency_ms,
    }


__all__ = ["ChatError", "MODELS_PATH", "chat"]
