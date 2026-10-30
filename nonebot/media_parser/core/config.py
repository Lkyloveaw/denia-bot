from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zoneinfo
from collections.abc import Mapping, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from nonebot import logger


@contextmanager
def config_lock(path: Path):
    """Serialize dashboard and bot read-modify-write operations across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path.with_name('.config-lock.sqlite3'), timeout=5)
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield
    finally:
        connection.rollback()
        connection.close()


class ConfigNode:
    """
    配置节点, 把 dict 变成强类型对象。

    规则：
    - schema 来自子类类型注解
    - 声明字段：读写，写回底层 dict
    - 未声明字段和下划线字段：仅挂载属性，不写回
    - 支持 ConfigNode 多层嵌套（lazy + cache）
    """

    _SCHEMA_CACHE: dict[type, dict[str, type]] = {}
    _FIELDS_CACHE: dict[type, set[str]] = {}

    @classmethod
    def _schema(cls) -> dict[str, type]:
        return cls._SCHEMA_CACHE.setdefault(cls, get_type_hints(cls))

    @classmethod
    def _fields(cls) -> set[str]:
        return cls._FIELDS_CACHE.setdefault(
            cls,
            {k for k in cls._schema() if not k.startswith("_")},
        )

    @staticmethod
    def _is_optional(tp: type) -> bool:
        if get_origin(tp) in (Union, UnionType):
            return type(None) in get_args(tp)
        return False

    def __init__(self, data: MutableMapping[str, Any]):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_children", {})
        for key, tp in self._schema().items():
            if key.startswith("_"):
                continue
            if key in data:
                continue
            if hasattr(self.__class__, key):
                continue
            if self._is_optional(tp):
                continue
            logger.warning(f"[config:{self.__class__.__name__}] 缺少字段: {key}")

    def __getattr__(self, key: str) -> Any:
        if key in self._fields():
            value = self._data.get(key)
            tp = self._schema().get(key)

            if isinstance(tp, type) and issubclass(tp, ConfigNode):
                children: dict[str, ConfigNode] = self.__dict__["_children"]
                if key not in children:
                    if not isinstance(value, MutableMapping):
                        raise TypeError(
                            f"[config:{self.__class__.__name__}] "
                            f"字段 {key} 期望 dict，实际是 {type(value).__name__}"
                        )
                    children[key] = tp(value)
                return children[key]

            return value

        if key in self.__dict__:
            return self.__dict__[key]

        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in self._fields():
            self._data[key] = value
            return
        object.__setattr__(self, key, value)

    def raw_data(self) -> Mapping[str, Any]:
        """
        底层配置 dict 的只读视图
        """
        return MappingProxyType(self._data)

    def save_config(self) -> None:
        """
        保存配置到磁盘（仅允许在根节点调用）
        """
        raise RuntimeError('only the root configuration can be saved')


class ConfigNodeContainer:
    """
    配置节点容器, 把 list 的 dict 变成 dict 的对象集合。

    - nodes: list[dict[str, Any]]
    - item_cls 用于包装 dict 成强类型节点
    - key_name 作为属性名访问, 默认为 "__template_key"
    """

    def __init__(
        self,
        nodes: list[dict[str, Any]],
        item_cls: type[ConfigNode],
        key_name="__template_key",
    ):
        self._nodes: dict[str, ConfigNode] = {}
        for node in nodes:
            key = node.get(key_name)
            if not key:
                logger.warning(f"[node] 缺少 {key_name}，已跳过")
                continue
            if key in self._nodes:
                logger.warning(f"[node] {key} 重复配置，已覆盖")
            self._nodes[key] = item_cls(node)

    def __getattr__(self, name: str) -> ConfigNode:
        if name in self._nodes:
            return self._nodes[name]
        raise AttributeError(name)

    def __iter__(self):
        return iter(self._nodes.values())

    def keys(self):
        return self._nodes.keys()

    def items(self):
        return self._nodes.items()


# ================ 插件自定义配置 ==================


class ParserItem(ConfigNode):
    __template_key: str
    enable: bool
    use_proxy: bool
    cookies: str | None
    show_body_text: bool | None
    use_original_image: bool | None
    mixed_layout: bool | None
    show_work_content: bool | None
    text_max_length: int | None
    video_send_mode: str | None
    video_codec_list: list | None
    video_quality: str | None
    video_codec: str | None
    video_format: str | None
    block_pcdn: bool | None
    metube_url: str | None
    wait_timeout: int | None
    delete_after_fetch: bool | None
    nsfw: str | None
    max_page: int | None
    image_send_mode: str | None
    send_blue_links: bool | None
    qzone_credential_source: str | None
    snowluma_http_url: str | None
    snowluma_access_token: str | None
    snowluma_credential_cache_seconds: int | None

    @property
    def name(self) -> str:
        return self._data.get("__template_key")


class ParserConfig(ConfigNodeContainer):
    acfun: ParserItem
    allcpp: ParserItem
    bilibili: ParserItem
    douyin: ParserItem
    instagram: ParserItem
    kuaishou: ParserItem
    metube: ParserItem
    ncm: ParserItem
    nga: ParserItem
    tiktok: ParserItem
    twitter: ParserItem
    weibo: ParserItem
    xiaoheihe: ParserItem
    zhihu: ParserItem
    xhs: ParserItem
    youtube: ParserItem
    shipinhao: ParserItem
    qzone: ParserItem

    def __init__(self, nodes: list[dict[str, Any]]):
        super().__init__(nodes, item_cls=ParserItem)

    def platforms(self) -> list[str]:
        return list(self._nodes.keys())

    def enabled_platforms(self) -> list[str]:
        return [k for k, v in self._nodes.items() if getattr(v, "enable", True)]


class PluginConfig(ConfigNode):
    media_tools_paths: list[str]
    snowluma_container_cache: bool
    whitelist: list[str]
    blacklist: list[str]

    arbiter: bool
    require_at_in_group: bool
    enable_reply_parse: bool
    debounce_interval: int

    source_max_size: int
    source_max_minute: int

    audio_to_file: bool
    single_heavy_render_card: bool
    forward_threshold: int

    show_download_fail_tip: bool
    download_timeout: int
    download_retry_times: int
    common_timeout: int

    proxy: str | None

    clean_cron: str

    parsers_template: list[dict[str, Any]]

    _plugin_name = "astrbot_plugin_parser"
    def __init__(self, config_path: Path | None = None):
        self.plugin_dir = Path(__file__).resolve().parent.parent
        self.data_dir = Path(__file__).resolve().parents[2] / 'data' / 'media_parser'
        self.config_path = config_path or self.data_dir / 'config.json'
        schema = json.loads((self.plugin_dir / '_conf_schema.json').read_text(encoding='utf-8'))
        defaults = {k: v['default'] for k, v in schema.items() if 'default' in v}
        defaults['media_tools_paths'] = []
        defaults['snowluma_container_cache'] = False
        templates = json.loads((self.plugin_dir / 'default_template.json').read_text(encoding='utf-8'))
        platform_schema = schema['parsers_template']['templates']
        defaults['parsers_template'] = []
        template_by_name = {v.get('__template_key'): v for v in templates}
        for name, spec in platform_schema.items():
            values = {k: v['default'] for k, v in spec['items'].items() if 'default' in v}
            values['enable'] = False
            defaults['parsers_template'].append({'__template_key': name, **values, **template_by_name.get(name, {})})
        if self.config_path.exists():
            saved = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
            defaults.update(saved)
        super().__init__(defaults)
        self.admins_id = []

        # ---------- 内置配置 ----------
        self.emoji_cdn = "https://cdn.jsdelivr.net/npm/emoji-datasource-facebook@14.0.0/img/facebook/64/"
        self.emoji_style = "FACEBOOK"  # 可选：APPLE、FACEBOOK、GOOGLE、TWITTER

        # ---------- 派生字段 ----------
        self.proxy = self.proxy or None
        self.max_duration = self.source_max_minute * 60
        self.max_size = self.source_max_size * 1024 * 1024

        self.timezone = zoneinfo.ZoneInfo("Asia/Shanghai")

        # ---------- 路径 ----------
        self.cache_dir = self.data_dir / "cache"
        self.container_cache_dir = None
        if self.snowluma_container_cache:
            self.cache_dir = Path(__file__).resolve().parents[3] / 'snowluma' / 'parser-cache'
            self.container_cache_dir = '/app/data/parser-cache'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_dir = self.data_dir / "cookies"
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.default_template_file = self.plugin_dir / "default_template.json"

        # ---------- Parser ----------
        self.parser = ParserConfig(self.parsers_template)

    def save_config(self):
        with config_lock(self.config_path):
            self._write_config()

    def _write_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.config_path.parent, prefix='.parser-', suffix='.tmp', delete=False) as f:
                temp = f.name
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(temp, self.config_path)
        finally:
            if temp and os.path.exists(temp): os.unlink(temp)

    @staticmethod
    def load_parser_template(file: Path) -> list[dict[str, Any]]:
        try:
            with file.open(encoding="utf-8-sig") as f:
                template = json.loads(f.read())
                logger.info(f"[parser] 加载模板成功: {file}")
                return template
        except Exception as e:
            logger.error(f"[parser] 加载模板失败: {e}")
            return []

    def add_blacklist(self, umo: str):
        with config_lock(self.config_path):
            latest = PluginConfig(self.config_path)
            if umo not in latest.blacklist:
                latest.blacklist.append(umo)
                latest._write_config()
            self.blacklist = latest.blacklist

    def remove_blacklist(self, umo: str):
        with config_lock(self.config_path):
            latest = PluginConfig(self.config_path)
            if umo in latest.blacklist:
                latest.blacklist.remove(umo)
                latest._write_config()
            self.blacklist = latest.blacklist
