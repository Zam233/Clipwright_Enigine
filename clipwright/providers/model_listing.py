"""模型列表拉取（B2「获取模型列表」；2026-10-28 PRD B6 契约收口）。

- openai / ollama（OpenAI 兼容）：GET {base_url}/models
- anthropic：GET {base_url | https://api.anthropic.com}/v1/models
- 解析兼容 OpenAI `data[].id`、Ollama `models[].name`、Anthropic `data[].id`

失败语义（B6）：**不抛异常**，返回 `ModelListingResult`：
- `status` = `ok` | `empty` | `unsupported` | `error`
- `error_code` = `provider_unsupported` | `http_error` | `invalid_response` |
  `credential_error` | `timeout`
调用方（API 层）按 status 决定是否覆盖已存模型：只有 `ok` 才覆盖，其余保留旧清单。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clipwright.config import logger

#: 支持自动拉取模型列表的 provider（其余类型返回 unsupported + 提示手动添加）
SUPPORTED_LISTING_PROVIDERS: tuple[str, ...] = ("", "anthropic", "openai", "ollama")

LISTING_STATUSES: tuple[str, ...] = ("ok", "empty", "unsupported", "error")
LISTING_ERROR_CODES: tuple[str, ...] = (
    "provider_unsupported", "http_error", "invalid_response", "credential_error", "timeout",
)


@dataclass(frozen=True)
class ModelListingResult:
    """一次「获取模型列表」的结果（可读、可判、不假装成功）。"""

    status: str = "empty"
    models: list[str] = field(default_factory=list)
    note: str = ""
    error_code: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_public(self) -> dict[str, Any]:
        out = {"status": self.status, "models": list(self.models), "note": self.note}
        if self.error_code:
            out["error_code"] = self.error_code
        return out


def _endpoint(provider: str, base: str) -> str:
    name = provider
    if name in ("", "anthropic"):
        return f"{base or 'https://api.anthropic.com'}/v1/models"
    default_base = "http://localhost:11434/v1" if name == "ollama" else "https://api.openai.com/v1"
    return f"{base or default_base}/models"


def _parse_items(data: Any) -> list[str]:
    """兼容 OpenAI/Anthropic `data[].id`、Ollama `models[].name` 与纯字符串列表。"""
    items: Any = None
    if isinstance(data, dict):
        items = data.get("data") or data.get("models")
    elif isinstance(data, list):
        items = data
    out: list[str] = []
    for it in items or []:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("name") or it.get("model")
            if mid:
                out.append(str(mid))
        elif isinstance(it, str):
            out.append(it)
    return sorted(set(out))


async def list_models_result(provider: str, resolved: dict[str, Any]) -> ModelListingResult:
    """拉取某 provider 的模型清单，返回带状态/错误码的结果（B6）。"""
    name = (provider or "").strip().lower()
    if name not in SUPPORTED_LISTING_PROVIDERS:
        logger.info("provider %s 无内置模型列表实现", name)
        return ModelListingResult(
            status="unsupported", error_code="provider_unsupported",
            note=(f"provider「{name}」不支持自动获取模型列表（本地/自建端点常见），"
                  "请用「自定义模型」手动添加"),
        )

    base = str(resolved.get("base_url") or "").rstrip("/")
    key = resolved.get("api_key") or ""
    headers = {"Accept": "application/json", **dict(resolved.get("headers") or {})}
    timeout = float(resolved.get("timeout_sec") or 120)
    if key:
        if name in ("", "anthropic"):
            headers["x-api-key"] = str(key)
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {key}"

    url = _endpoint(name, base)
    try:
        import httpx

        kwargs: dict[str, Any] = {"timeout": timeout, "headers": headers}
        if resolved.get("proxy"):
            kwargs["proxy"] = str(resolved["proxy"])
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(url)
    except Exception as e:  # noqa: BLE001 — 超时/网络错误要能区分出来
        kind = type(e).__name__.lower()
        is_timeout = "timeout" in kind
        logger.warning("模型列表拉取异常 %s: %s", name, e)
        return ModelListingResult(
            status="error",
            error_code="timeout" if is_timeout else "http_error",
            note=(f"获取失败：{'请求超时' if is_timeout else e}；"
                  "可检查 Base URL / 代理，或改用「自定义模型」"),
        )

    if resp.status_code in (401, 403):
        return ModelListingResult(
            status="error", error_code="credential_error",
            note=f"获取失败：鉴权被拒绝（HTTP {resp.status_code}），请检查 API Key",
        )
    if resp.status_code >= 400:
        return ModelListingResult(
            status="error", error_code="http_error",
            note=f"获取失败：HTTP {resp.status_code}（{url}）",
        )

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return ModelListingResult(
            status="error", error_code="invalid_response",
            note="获取失败：返回内容不是 JSON（可能被网关/登录页拦截）",
        )

    models = _parse_items(data)
    if not models:
        return ModelListingResult(
            status="empty", error_code="invalid_response",
            note="未获取到模型（返回里没有 data[].id / models[].name），请手动添加",
        )
    return ModelListingResult(status="ok", models=models)


async def list_models_for_source(provider: str, resolved: dict[str, Any]) -> list[str]:
    """兼容旧调用方的列表形态（内部走 `list_models_result`）。"""
    result = await list_models_result(provider, resolved)
    return list(result.models)
