"""Provider 传输层共性：超时 / 代理 / 自定义请求头（方案 B4）。

`isobase` 的客户端只把 `api_key`/`base_url` 传给内层 SDK，超时/代理/自定义头
在构造时被丢弃（`providers/openai_chat.py` 里 `OpenAI(api_key=..., base_url=...)`）。
本模块在客户端构造后按需重建内层 SDK 客户端，把三者真正落到传输层——
「代理地址」是 Provider 源的一等配置项，不能只存不用。

任何一步不可用（SDK 不支持、属性缺失）都原样返回客户端并告警，不阻断调用。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from clipwright.config import logger


def build_http_client(proxy: str = "") -> Any | None:
    """构造带代理的 httpx 客户端（无代理 → None）。"""
    if not proxy:
        return None
    try:
        import httpx
    except Exception as e:  # noqa: BLE001 — 缺依赖不应阻断（回退无代理）
        logger.warning("代理不可用（httpx 缺失）: %s", e)
        return None
    try:
        return httpx.Client(proxy=proxy, timeout=900.0)
    except Exception as e:  # noqa: BLE001 — 代理串非法时不阻断
        logger.warning("代理配置无效，已忽略: %s", e)
        return None


def apply_transport(
    client: Any,
    *,
    timeout_sec: float = 0.0,
    proxy: str = "",
    headers: Optional[Mapping[str, Any]] = None,
) -> Any:
    """把超时/代理/自定义头应用到 isobase 客户端的内层 SDK 客户端。

    三者皆空 → 直接返回（零开销，既有测试的打桩客户端不受影响）。
    """
    if not timeout_sec and not proxy and not headers:
        return client
    inner = getattr(client, "client", None)
    if inner is None or not callable(inner.__class__):
        return client

    kwargs: dict[str, Any] = {}
    if timeout_sec:
        kwargs["timeout"] = float(timeout_sec)
    if headers:
        kwargs["default_headers"] = {str(k): str(v) for k, v in dict(headers).items()}
    http_client = build_http_client(proxy)
    if http_client is not None:
        kwargs["http_client"] = http_client

    api_key = getattr(inner, "api_key", None)
    base_url = getattr(inner, "base_url", None)
    try:
        client.client = inner.__class__(
            api_key=api_key,
            base_url=str(base_url) if base_url else None,
            **kwargs,
        )
    except Exception as e:  # noqa: BLE001 — 传输覆盖失败回退原客户端
        logger.warning("传输层覆盖（超时/代理/头）失败，沿用原客户端: %s", e)
    return client
