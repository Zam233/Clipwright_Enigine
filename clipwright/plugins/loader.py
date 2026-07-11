"""第三方插件发现与加载器。

支持从目录发现插件、解析 plugin.yaml 清单、解析 config.yaml 配置、
动态导入 Python 模块。

插件目录规范：
    plugins/{plugin_id}/
    ├── plugin.yaml       # 必需：插件清单（id/name/version/kind/entry_point）
    ├── config.yaml       # 可选：插件配置（独立于代码，运行时通过 plugin.config 访问）
    ├── main.py           # 推荐：入口模块（export 插件类到 __all__）
    └── __init__.py       # 可选：备选入口
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from clipwright.config import logger
from clipwright.plugins.base import BasePlugin
from clipwright.schema.plugin import PluginManifest, PluginMetadata, PluginKind


class PluginLoadError(Exception):
    """插件加载失败时抛出。"""


class PluginLoader:
    """插件加载器，支持从目录发现和动态加载第三方插件。"""

    def __init__(self, plugin_dir: Optional[Path] = None) -> None:
        self.plugin_dir = (plugin_dir or Path("plugins")).resolve()
        self._plugins: dict[str, BasePlugin] = {}
        self._metadatas: dict[str, PluginMetadata] = {}

    # ── 发现 ──

    def discover(self) -> list[str]:
        """发现插件目录中的所有可用插件 ID。"""
        if not self.plugin_dir.exists():
            return []
        return sorted([
            d.name for d in self.plugin_dir.iterdir()
            if d.is_dir() and self._has_manifest(d)
        ])

    @staticmethod
    def _has_manifest(directory: Path) -> bool:
        return (directory / "plugin.yaml").exists() or (directory / "__init__.py").exists()

    # ── 加载 ──

    def load(self, plugin_id: str) -> Optional[BasePlugin]:
        """加载并实例化指定插件。

        流程：
        1. 读取 plugin.yaml 清单（如存在）
        2. 读取 config.yaml 配置（如存在）
        3. 通过 importlib 动态导入入口模块
        4. 实例化入口模块中的插件类，注入 manifest + config
        5. 调用 plugin.initialize()
        6. 自动标记插件注册的 Tool / Skill / MaterialSource
        """
        if plugin_id in self._plugins:
            return self._plugins[plugin_id]

        plugin_path = self.plugin_dir / plugin_id
        if not plugin_path.exists():
            return None

        # 1. 解析清单
        manifest = self._parse_manifest(plugin_id, plugin_path)

        # 2. 解析配置
        config = self._parse_config(plugin_path)

        # 3. 动态导入
        try:
            module = self._import_plugin(plugin_id, plugin_path, manifest)
        except Exception as e:
            raise PluginLoadError(
                f"Failed to import plugin '{plugin_id}': {e}"
            ) from e

        # 4. 实例化插件类，注入 manifest + config
        plugin = self._instantiate_plugin(module, manifest)
        if plugin is None:
            raise PluginLoadError(
                f"Plugin '{plugin_id}' has no exported class in its entry module"
            )
        plugin.manifest = manifest
        plugin.config = config

        # 5. 注册表快照（用于后续追踪插件注册的内容）
        from clipwright.skill.registry import SkillRegistry
        from clipwright.tool.registry import ToolRegistry

        _tools_before = set(ToolRegistry._tools.keys())
        _skills_before = set(SkillRegistry._skills.keys())

        try:
            plugin.initialize()
        except Exception as e:
            raise PluginLoadError(
                f"Plugin '{plugin_id}' initialize() failed: {e}"
            ) from e

        # 自动标记插件注册的 Tool 和 Skill
        for name in set(ToolRegistry._tools.keys()) - _tools_before:
            ToolRegistry._tools[name]._plugin_id = plugin_id  # type: ignore[attr-defined]
        for name in set(SkillRegistry._skills.keys()) - _skills_before:
            SkillRegistry._skills[name]._plugin_id = plugin_id  # type: ignore[attr-defined]

        self._plugins[plugin_id] = plugin
        self._metadatas[plugin_id] = PluginMetadata(manifest=manifest)

        return plugin

    def load_all(self) -> list[str]:
        """发现并加载所有可用插件，返回成功加载的 ID 列表。"""
        loaded: list[str] = []
        for pid in self.discover():
            try:
                self.load(pid)
                loaded.append(pid)
            except PluginLoadError as e:
                logger.warning("插件加载失败 (skipped): %s", e)
        return loaded

    # ── 管理 ──

    def register(self, plugin: BasePlugin) -> None:
        """直接注册一个已实例化的插件。"""
        self._plugins[plugin.manifest.id] = plugin
        self._metadatas[plugin.manifest.id] = PluginMetadata(
            manifest=plugin.manifest
        )

    def unload(self, plugin_id: str) -> None:
        """卸载指定插件。"""
        plugin = self._plugins.pop(plugin_id, None)
        self._metadatas.pop(plugin_id, None)
        if plugin:
            try:
                plugin.shutdown()
            except Exception as e:
                logger.warning("插件 %s shutdown 异常: %s", plugin_id, e)

    def get(self, plugin_id: str) -> Optional[BasePlugin]:
        return self._plugins.get(plugin_id)

    def list_loaded(self) -> list[PluginMetadata]:
        return list(self._metadatas.values())

    def clear(self) -> None:
        for pid in list(self._plugins.keys()):
            self.unload(pid)

    # ── 内部 ──

    def _parse_config(self, plugin_path: Path) -> dict[str, Any]:
        """解析 config.yaml（可选），不存在则返回空字典。"""
        config_path = plugin_path / "config.yaml"
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    data: dict[str, Any] = yaml.safe_load(f) or {}
                return data
            except Exception as e:
                logger.warning("插件 %s config.yaml 解析失败: %s", plugin_path.name, e)
        return {}

    def _parse_manifest(self, plugin_id: str, plugin_path: Path) -> PluginManifest:
        """解析 plugin.yaml，如不存在则创建默认清单。"""
        manifest_path = plugin_path / "plugin.yaml"
        if manifest_path.exists():
            with open(manifest_path, encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f) or {}
            # 去除可能重复的 id 字段
            data.pop("id", None)
            if "kind" in data and isinstance(data["kind"], str):
                try:
                    data["kind"] = PluginKind(data["kind"])
                except ValueError:
                    data["kind"] = PluginKind.CAPABILITY
            return PluginManifest(id=plugin_id, **data)

        return PluginManifest(
            id=plugin_id,
            name=plugin_id,
            kind=PluginKind.CAPABILITY,
        )

    def _import_plugin(
        self, plugin_id: str, plugin_path: Path, manifest: PluginManifest
    ) -> object:
        """动态导入插件的入口模块。"""
        # 将插件目录加入 sys.path
        plugin_dir_str = str(self.plugin_dir)
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)

        # 从 manifest 的 entry_point 或默认位置导入
        if manifest.entry_point:
            mod_path = manifest.entry_point
        else:
            mod_path = f"{plugin_id}.main"

        try:
            return importlib.import_module(mod_path)
        except ModuleNotFoundError:
            # 回退：从 __init__.py 导入
            try:
                return importlib.import_module(plugin_id)
            except ModuleNotFoundError as e:
                raise PluginLoadError(
                    f"Cannot import '{plugin_id}': no main.py or __init__.py found"
                ) from e

    def _instantiate_plugin(
        self, module: object, manifest: PluginManifest
    ) -> Optional[BasePlugin]:
        """在导入的模块中查找并实例化插件类。"""

        def _is_concrete_plugin(attr: object) -> bool:
            """判断是否是 BasePlugin 的非抽象子类。"""
            return (
                isinstance(attr, type)
                and issubclass(attr, BasePlugin)
                and attr is not BasePlugin
                and not bool(getattr(attr, "__abstractmethods__", []))
            )

        # 优先扫描 __all__ 导出
        if hasattr(module, "__all__"):
            for export_name in getattr(module, "__all__", []):
                attr = getattr(module, export_name, None)
                if _is_concrete_plugin(attr):
                    instance = attr()
                    instance.manifest = manifest
                    return instance

        # 回退：扫描模块属性
        for attr_name in dir(module):
            attr = getattr(module, attr_name, None)
            if _is_concrete_plugin(attr):
                instance = attr()
                instance.manifest = manifest
                return instance

        return None
