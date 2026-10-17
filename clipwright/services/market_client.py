"""市场客户端（P4-4B）— 调用 ClipWright Server 的市场 API。

仅封装搜索/详情/下载（发布由前端直连 Server）。Server 地址取自
settings.account_url（默认 http://localhost:8090）。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from clipwright.config import settings


def _base() -> str:
    return (settings.account_url or "http://localhost:8090").rstrip("/")


async def _get(path: str, **params: Any) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{_base()}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def search_plugins(q: str = "", tag: str = "", page: int = 1) -> dict:
    return await _get("/api/market/plugins", q=q, tag=tag, page=page)


async def search_personas(q: str = "", tag: str = "", page: int = 1) -> dict:
    return await _get("/api/market/personas", q=q, tag=tag, page=page)


async def get_plugin(package_id: str) -> dict:
    return await _get(f"/api/market/plugins/{package_id}")


async def get_persona(package_id: str) -> dict:
    return await _get(f"/api/market/personas/{package_id}")


async def download_plugin(package_id: str, version: str = "") -> tuple[bytes, Optional[str]]:
    """返回 (包内容, sha256)。

    轮 80（红线）：对实际下载内容计算 sha256 并交由安装侧比对——此前恒返回
    None，install_service 的完整性校验形同虚设。
    """
    import hashlib

    url = f"{_base()}/api/market/plugins/{package_id}/download"
    if version:
        url += f"?version={version}"
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content, hashlib.sha256(resp.content).hexdigest()


async def download_persona(package_id: str, version: str = "") -> tuple[bytes, Optional[str]]:
    import hashlib

    url = f"{_base()}/api/market/personas/{package_id}/download"
    if version:
        url += f"?version={version}"
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content, hashlib.sha256(resp.content).hexdigest()
