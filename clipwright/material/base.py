"""素材源基类 — 统一素材检索接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from clipwright.schema.material import MaterialAsset


class MaterialSource(ABC):
    """素材源基类。

    不同类型的素材源（JSON 目录、远程 API、本地文件系统）都继承此类。
    """

    source_id: str = ""
    source_name: str = ""

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        """语义/关键词搜索素材。

        Returns:
            [(MaterialAsset, relevance_score), ...]
            score 范围 0-1，越高越相关
        """
        ...

    async def get_asset(self, asset_id: str) -> MaterialAsset | None:
        """按 ID 获取单个素材。子类可覆盖以实现精确查找。"""
        return None

    async def count(self) -> int:
        """返回此源中的素材总数（用于仪表盘）。"""
        return 0

    async def list_all(self) -> list[MaterialAsset]:
        """列出所有素材（用于管理界面）。"""
        return []
