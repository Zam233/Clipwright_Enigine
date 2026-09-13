"""模型列表拉取（B2「获取模型列表」）——按 provider 类型调用其 models 端点。

- openai / ollama（OpenAI 兼容）：GET {base_url}/models
- anthropic：GET {base_url or https://api.anthropic.com}/v1/models
- 失败一律返回空列表（调用方给出可读 note，前端可改用「自定义模型」）
"""
from __future__ import annotations

from typing import Any

from clipwright.config import logger


async def list_models_for_source(provider: str, resolved: dict[str, Any]) -> list[str]:
    """返回模型 id 列表（失败/不支持 → 空列表）。"""
    name = (provider or "").strip().lower()
    base = str(resolved.get("base_url") or "").rstrip("/")
    key = resolved.get("api_key") or ""
    headers = {"Accept": "application/json", **dict(resolved.get("headers") or {})}
    timeout = float(resolved.get("timeout_sec") or 120)

    if name in ("", "anthropic"):
        url = f"{base or 'https://api.anthropic.com'}/v1/models"
        if key:
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
    elif name in ("openai", "ollama"):
        default_base = "http://localhost:11434/v1" if name == "ollama" else "https://api.openai.com/v1"
        url = f"{base or default_base}/models"
        if key:
            headers["Authorization"] = f"Bearer {key}"
    else:
        logger.info("provider %s 无内置模型列表实现", name)
        return []

    try:
        import httpx
        kwargs: dict[str, Any] = {"timeout": timeout, "headers": headers}
        if resolved.get("proxy"):
            kwargs["proxy"] = str(resolved["proxy"])
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                logger.warning("模型列表拉取失败 %s: HTTP %s", url, resp.status_code)
                return []
            data = resp.json()
    except Exception as e:  # noqa: BLE001 — 拉取失败由调用方降级为「自定义模型」
        logger.warning("模型列表拉取异常 %s: %s", name, e)
        return []

    items: Any = None
    if isinstance(data, dict):
        items = data.get("data") or data.get("models")
    out: list[str] = []
    for it in items or []:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("name") or it.get("model")
            if mid:
                out.append(str(mid))
        elif isinstance(it, str):
            out.append(it)
    return sorted(set(out))
