"""Provider 源配置 API（B2）——「模型提供商」页的后端。

端点（全部 admin 门禁，密钥只回显掩码）：
- GET    /api/providers?kind=            源列表（脱敏）
- POST   /api/providers                  新建源
- GET    /api/providers/roles            角色绑定总览
- PUT    /api/providers/roles            绑定/解绑角色 {role, source_id, model}
- GET    /api/providers/{id}             源详情（脱敏）
- PUT    /api/providers/{id}             更新源（api_keys 传掩码=保留旧值）
- DELETE /api/providers/{id}             删除源（自动解绑角色）
- POST   /api/providers/{id}/models      覆盖模型清单
- POST   /api/providers/{id}/models/upsert  自定义模型（新增/覆盖）
- DELETE /api/providers/{id}/models/{model_id} 删除模型（解绑引用它的角色）
- POST   /api/providers/{id}/models/fetch     获取模型列表（调用 provider 实现，失败静默返回 []）
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from clipwright.api.plugin import _require_admin  # 复用同一 admin 门禁语义
from clipwright.config import logger
from clipwright.services import provider_sources as ps

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
async def list_providers(kind: str = "", request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    return {"sources": ps.list_sources(kind), "roles": ps.get_roles()}


@router.post("")
async def create_provider(payload: dict[str, Any] = Body(...), request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    try:
        return ps.create_source(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/available")
async def available_providers(request: Request = None) -> dict[str, Any]:
    """各类型可选的 provider 名（**门面驱动**，含插件贡献）。

    P4/P5：前端下拉的选项来源必须是 ContributionRegistry，而不是硬编码列表——
    插件注册的 llm_provider/tts_provider/embedding_provider/rerank_provider
    在这里自动出现（启停插件即时生效）。
    """
    _require_admin(request)
    from clipwright.providers.embedding import embedding_provider_names
    from clipwright.providers.llm import ensure_llm_providers_registered
    from clipwright.providers.rerank import rerank_provider_names
    from clipwright.providers.tts import ensure_tts_providers_registered, tts_provider_names
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_llm_providers_registered()
    ensure_tts_providers_registered()
    out: dict[str, list[str]] = {}
    for kind, names in (
        (ContributionKind.LLM_PROVIDER, None),
        (ContributionKind.TTS_PROVIDER, None),
        (ContributionKind.EMBEDDING_PROVIDER, embedding_provider_names),
        (ContributionKind.RERANK_PROVIDER, rerank_provider_names),
    ):
        listed = names() if names else [c.id for _, c in ContributionRegistry.list(kind)]
        out[kind.value] = sorted(set(listed))
    # 角色 ←→ 期望的 provider 类型（前端把「角色分配」渲染到对应 tab）
    out["role_kinds"] = {
        "main": "llm", "flash": "llm", "vision": "llm",
        "embed": "embedding", "rerank": "rerank", "tts": "tts",
    }
    # tts_provider 的别名（历史 id → 规范 id）也要给前端做兼容显示
    out["tts_aliases"] = {"qwen_tts": "qwen-tts"}
    return out


@router.get("/roles")
async def get_roles(request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    return {"roles": ps.get_roles(), "available": list(ps.ROLES)}


@router.put("/roles")
async def put_role(payload: dict[str, Any] = Body(...), request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    try:
        return ps.set_role(
            str(payload.get("role") or ""),
            payload.get("source_id"),
            str(payload.get("model") or ""),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{source_id}")
async def get_provider(source_id: str, request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    src = ps.get_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"源不存在: {source_id}")
    return src


@router.put("/{source_id}")
async def update_provider(source_id: str, payload: dict[str, Any] = Body(...),
                          request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    try:
        return ps.update_source(source_id, payload)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{source_id}")
async def delete_provider(source_id: str, request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    try:
        return ps.delete_source(source_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{source_id}/models")
async def set_models(source_id: str, payload: dict[str, Any] = Body(...),
                     request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    try:
        return ps.set_models(source_id, payload.get("models"))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{source_id}/models/upsert")
async def upsert_model(source_id: str, payload: dict[str, Any] = Body(...),
                       request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    try:
        return ps.upsert_model(source_id, payload)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{source_id}/models/{model_id}")
async def delete_model(source_id: str, model_id: str, request: Request = None) -> dict[str, Any]:
    _require_admin(request)
    try:
        return ps.delete_model(source_id, model_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{source_id}/models/fetch")
async def fetch_models(source_id: str, request: Request = None) -> dict[str, Any]:
    """「获取模型列表」：调用 provider 实现的 list_models；失败返回空列表 + 原因。"""
    _require_admin(request)
    src = ps.get_source(source_id, public=False)
    if src is None:
        raise HTTPException(status_code=404, detail=f"源不存在: {source_id}")
    resolved = ps.resolve_for_source(source_id) if hasattr(ps, "resolve_for_source") else None
    provider = str(src.get("provider") or "")
    try:
        from clipwright.providers.model_listing import list_models_for_source
        models = await list_models_for_source(provider, resolved or {})
        if not models:
            return {"models": [], "note": "provider 未返回模型列表（可手动添加自定义模型）"}
        ps.set_models(source_id, [{"id": m, "name": m, "enabled": True} for m in models])
        return {"models": ps.get_source(source_id)["models"]}
    except Exception as e:  # noqa: BLE001 — 拉取失败不阻断（前端可自定义）
        logger.warning("获取模型列表失败 (%s): %s", provider, e)
        return {"models": [], "note": f"获取失败：{e}"}
