"""运行时配置 API — 把 `.env` 配置项搬到「设置」页管理。

- GET  /api/settings        分组字段元数据 + 当前生效值（secret 只回 has_value）
- PUT  /api/settings        部分更新（逐字段校验；null/"" = 清除覆盖）
- POST /api/settings/reset  清除覆盖（keys=[] 或缺省 = 全部）

优先级 store(settings.json) > 环境变量/.env > 默认值。所有端点走
``require_admin``（jwt 模式下仅 admin；off/token 模式放行，与本地部署语义一致）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, TypeAdapter, field_validator
from pydantic.fields import FieldInfo

from clipwright.authz import require_admin
from clipwright.config import Settings, logger, settings
from clipwright.config_schema import FIELD_META, GROUPS
from clipwright.services import settings_runtime, settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"])

# PUT 请求里 secret 字段的「保持不变」占位（前端不知道明文时回传它）
_MASK = "••••••••"


class SettingsPatch(BaseModel):
    """部分更新：{字段: 新值}；null 或空串表示清除该键的 store 覆盖。"""

    values: dict[str, Any]

    @field_validator("values")
    @classmethod
    def _non_empty(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v:
            raise ValueError("values 不能为空")
        return v


class ResetRequest(BaseModel):
    keys: list[str] = []


def _display_value(value: Any) -> Any:
    """Path → str，其余原样（保证 JSON 可序列化）。"""
    from pathlib import Path

    if isinstance(value, Path):
        return str(value)
    return value


def _field_payload(key: str) -> dict[str, Any]:
    meta = FIELD_META[key]
    info: FieldInfo = Settings.model_fields[key]
    current = getattr(settings, key)
    payload: dict[str, Any] = {
        "key": key,
        "group": meta.group,
        "label": meta.label,
        "description": meta.description,
        "kind": meta.kind,
        "choices": list(meta.choices),
        "secret": meta.secret,
        "hot": meta.hot,
        "requires_restart": not meta.hot,
        "source": settings_store.source_of(key),
        "default": _display_value(info.get_default(call_default_factory=True)),
        "has_value": bool(current) if isinstance(current, (str, list)) else current is not None,
    }
    if meta.secret:
        # 绝不回显明文（含掩码形式也不回显，掩码仅用于前端占位展示）
        payload["value"] = None
        payload["masked"] = _MASK if payload["has_value"] else ""
    else:
        payload["value"] = _display_value(current)
    return payload


@router.get("")
async def get_settings(request: Request) -> dict:
    """分组返回全部配置字段元数据与当前生效值。"""
    require_admin(request)
    fields = {g["id"]: [] for g in GROUPS}
    for key, meta in FIELD_META.items():
        fields[meta.group].append(_field_payload(key))
    groups = [
        {
            "id": g["id"],
            "label": g["label"],
            "fields": sorted(fields[g["id"]], key=lambda f: f["key"]),
        }
        for g in GROUPS
    ]
    return {
        "groups": groups,
        "has_encryption_key": _encryption_key_ready(),
        "note": "secret 字段不回显明文；requires_restart=true 的字段需重启服务生效",
    }


def _encryption_key_ready() -> bool:
    from clipwright.services import webhook_crypto

    return webhook_crypto.has_key()


def _validate_value(key: str, value: Any) -> Any:
    """逐字段校验（TypeAdapter 直查注解，不构造整个 Settings 触发 env 副作用）。"""
    if value is None or value == "":
        return value  # 清除语义，跳过类型校验
    info = Settings.model_fields[key]
    annotation = info.annotation
    # list[str] 的环境变量形式是逗号分隔字符串，这里同样宽容
    meta = FIELD_META[key]
    if meta.kind == "list" and isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    try:
        return TypeAdapter(annotation).validate_python(value)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"字段 {key} 校验失败: {e}") from e


def _maybe_coerce(key: str, value: Any) -> Any:
    """宽松转换：前端表单常把数字/布尔传成字符串。转换失败交由 TypeAdapter 报 422。"""
    if value is None or value == "":
        return value
    meta = FIELD_META[key]
    try:
        if meta.kind == "int":
            return int(value)
        if meta.kind == "float":
            return float(value)
    except (TypeError, ValueError):
        return value  # 保留原值，由 _validate_value 统一报 422
    if meta.kind == "bool" and isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return value


@router.put("")
async def put_settings(request: Request, patch: SettingsPatch = Body(...)) -> dict:
    """部分更新配置。返回 applied/cleared/requires_restart 与触发的失效钩子。"""
    require_admin(request)
    applied: list[str] = []
    cleared: list[str] = []
    warnings: list[str] = []
    store_patch: dict[str, Any] = {}

    for key, raw in patch.values.items():
        if key not in FIELD_META:
            raise HTTPException(status_code=400, detail=f"未知的配置字段: {key}")
        meta = FIELD_META[key]
        if meta.secret and isinstance(raw, str) and raw == _MASK:
            continue  # 掩码占位 = 保持现状
        value = _maybe_coerce(key, raw)
        value = _validate_value(key, value)
        store_patch[key] = value

    if not store_patch:
        return {"ok": True, "applied": [], "cleared": [], "requires_restart": [], "warnings": []}

    try:
        applied, cleared = settings_store.save(store_patch)
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # 应用到运行中的 settings 单例：applied 写新值，cleared 还原 env/默认
    for key in applied:
        setattr(settings, key, store_patch[key])
    settings_store.restore_to_settings(cleared)

    # 未热生效的字段提示重启
    requires_restart = sorted(
        {k for k in applied + cleared if not FIELD_META[k].hot}
    )
    if requires_restart:
        warnings.append("以下字段需重启服务后完全生效: " + ", ".join(requires_restart))

    hooks = settings_runtime.apply_hooks(applied + cleared)
    logger.info("配置更新: applied=%s cleared=%s hooks=%s", applied, cleared, hooks)
    return {
        "ok": True,
        "applied": applied,
        "cleared": cleared,
        "requires_restart": requires_restart,
        "warnings": warnings,
    }


@router.post("/reset")
async def reset_settings(request: Request, body: ResetRequest = Body(default=ResetRequest())) -> dict:
    """清除 store 覆盖并还原为 env/.env 值（或默认值）。keys 为空 = 全部。"""
    require_admin(request)
    if body.keys:
        unknown = [k for k in body.keys if k not in FIELD_META]
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知的配置字段: {', '.join(unknown)}")
    cleared = settings_store.reset(body.keys or None)
    settings_store.restore_to_settings(cleared)
    requires_restart = sorted({k for k in cleared if not FIELD_META[k].hot})
    settings_runtime.apply_hooks(cleared)
    return {
        "ok": True,
        "cleared": cleared,
        "requires_restart": requires_restart,
    }
