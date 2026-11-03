"""本地素材文件夹插件（local_material）——把用户自己的素材目录接进多源检索。

**替换掉原来的 `my_material_lib`**：那是个示例插件，catalog 指向不存在的文件、
URL 指向 `cdn.example.com`，启用只会让管线"检索成功但素材不可用"（旧注释自己
也写了这一点）。本插件做的是真事：扫描真实目录、返回真实文件路径。

配置（二选一）：

- 插件配置 `material_dir`（config.yaml 或后台插件配置）；
- 环境变量 `CLIPWRIGHT_LOCAL_MATERIAL_DIR`。

未配置或目录不存在时**零贡献并说明原因**，不假装有素材。
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.material.local_source import LocalFolderSource
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind

ENV_VAR = "CLIPWRIGHT_LOCAL_MATERIAL_DIR"


class LocalMaterialPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="local_material",
        name="本地素材文件夹",
        version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE,
        description="把本地素材目录接入多源检索（扫描真实文件，不依赖网络）",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        self._source: LocalFolderSource | None = None

    def _configured_dir(self) -> str:
        cfg = self.config or {}
        return str(cfg.get("material_dir") or "").strip()

    def contributions(self) -> list[Any]:
        import os

        root = self._configured_dir() or os.environ.get(ENV_VAR, "").strip()
        if not root:
            logger.info(
                "[LocalMaterial] 未配置素材目录（插件配置 material_dir 或环境变量 %s），零贡献",
                ENV_VAR,
            )
            return []
        source = LocalFolderSource(root, source_id="local_material", source_name="本地素材文件夹")
        count = source.refresh()
        if count == 0:
            logger.info("[LocalMaterial] 目录 %s 没有可用媒体文件，零贡献", root)
            return []
        self._source = source
        logger.info("[LocalMaterial] 贡献素材源 local_material（%d 个文件，%s）", count, root)
        return [Contribution(
            ContributionKind.MATERIAL_SOURCE, source.source_id, source,
        )]


__all__ = ["ENV_VAR", "LocalMaterialPlugin"]
