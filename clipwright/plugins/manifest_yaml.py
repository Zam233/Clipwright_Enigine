"""插件 manifest 的严格 YAML 解析。

PyYAML ``safe_load`` 默认会静默用后一个值覆盖重复键。插件清单中的
重复键（例如同时写 ``api_version: "1"`` 与 ``api_version: "2"``）会让
宿主 Loader 与 worker 看到不同契约，却没有任何诊断。本模块在构造对象
前检查所有 MappingNode，发现重复标量键就抛可读错误。

Loader 与子进程 worker **必须共用**本函数，避免一条路径严格、另一条吞键。
"""

from __future__ import annotations

from typing import Any

import yaml


class PluginManifestError(ValueError):
    """manifest 内容不合格，调用方应转成 PluginLoadError / RpcError。"""


def _reject_duplicate_keys(node: yaml.Node, source: str, path: str) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                # 复杂键无法用文本稳定报告；构造阶段由 SafeLoader 自行拒绝。
                continue
            key = str(key_node.value)
            here = f"{path}.{key}" if path else key
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            if key in seen:
                raise PluginManifestError(
                    f"{source}: 重复的 YAML 键 {here!r}；请删除旧值，只保留一个声明"
                )
            seen.add(key)
            _reject_duplicate_keys(value_node, source, here)
    elif isinstance(node, yaml.SequenceNode):
        for index, child in enumerate(node.value):
            _reject_duplicate_keys(child, source, f"{path}[{index}]")


def load_manifest_dict(text: str, *, source: str = "plugin.yaml") -> dict[str, Any]:
    """解析插件 manifest；重复键、非对象根一律拒绝。

    ``PluginManifestError`` 是 ``ValueError`` 的子类，Loader 现有的
    ``except yaml.YAMLError`` 之外应显式捕获并转成 ``PluginLoadError``。
    """
    if not isinstance(text, str):
        raise PluginManifestError(f"{source}: manifest 内容必须是文本")
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as e:
        raise PluginManifestError(f"{source}: YAML 解析失败: {e}") from e
    if root is None:
        return {}
    _reject_duplicate_keys(root, source, "")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PluginManifestError(f"{source}: YAML 解析失败: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise PluginManifestError(f"{source}: 顶层必须是 YAML mapping")
    return data


__all__ = ["PluginManifestError", "load_manifest_dict"]
