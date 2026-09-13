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

    async def close(self) -> None:
        """释放源持有的资源（HTTP 客户端等）。默认无事可做。"""
        return None


def close_source_sync(source: object) -> bool:
    """在**同步上下文**安全地关闭素材源（A3 修复）。

    插件的 `shutdown()` 是同步契约，此前各插件写 `asyncio.create_task(src.close())`：
    没有运行中的事件循环时该协程永远不会被 await（RuntimeWarning: coroutine
    ... was never awaited），httpx 连接一直不释放。这里统一处理：

    - 有运行中的循环 → 交给该循环调度（当前线程内有 loop 的情况）；
    - 没有 → `asyncio.run()` 直接跑完（同步路径/线程池卸载路径）。

    返回是否真的执行了关闭。
    """
    import asyncio

    closer = getattr(source, "close", None)
    if not callable(closer):
        return False
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(closer())
        else:
            asyncio.run(closer())
        return True
    except Exception:  # noqa: BLE001 — 关闭失败不阻断卸载
        return False
