"""视频类型制作 API — 自定义视频类型的 CRUD + 预览。

用户可通过此 API 创建、编辑、删除自定义视频类型定义，
定义以 YAML 格式存储在 user_types/ 目录下。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from clipwright.category.registry import CategoryRegistry
from clipwright.config import logger
from clipwright.paths import anchor
from clipwright.schema.category_spec import (
    SLOT_ALIASES,
    SLOT_KEYS,
    ParamRule,
    PromptBundle,
    PromptSlot,
    SignatureTrait,
    TypeDeclares,
    validate_bundle,
)
from clipwright.security import validate_id

router = APIRouter(prefix="/api/type-maker", tags=["type-maker"])


async def _guard_type_id(type_id: str | None = None) -> None:
    """路由级守卫：type_id 出现在路径中时校验合法性（防路径遍历）。"""
    if type_id is not None:
        validate_id(type_id, "type_id")


router.dependencies = [Depends(_guard_type_id)]

# 用户自定义类型存储目录
_USER_TYPES_DIR = anchor("user_types")

# P3-14: 转场类型白名单——/preview 与 create 共用（旧实现两处字面量不一致）
_ALLOWED_TRANSITIONS = frozenset({"cut", "fade", "dissolve", "wipe", "glitch", "pixel_dissolve", "slide"})


# ── 请求/响应模型 ──────────────────────────────


class ShotParams(BaseModel):
    """镜头级剪辑参数。"""
    min_shot_sec: float = Field(default=1.0, description="最短镜头时长(秒)")
    max_shot_sec: float = Field(default=10.0, description="最长镜头时长(秒)")
    transition_type: str = Field(default="cut", description="默认转场: cut/fade/dissolve/wipe")
    transition_duration_sec: float = Field(default=0.5, description="转场时长(秒)")
    cut_on_beat: bool = Field(default=False, description="是否踩点剪辑")


class PersonaMappingItem(BaseModel):
    """Persona 参数映射规则。"""
    source: str = Field(description="Persona 参数路径")
    transform: str = Field(default="direct", description="转换方式: direct/scale/lookup")
    scale_factor: float = Field(default=1.0, description="缩放因子(scale模式)")


class TypeDefinition(BaseModel):
    """视频类型定义。"""
    model_config = {"extra": "forbid"}  # A-06：未知字段直接 400，不再静默丢弃

    id: str = Field(description="类型唯一标识 (英文, 如 my_knowledge)")
    name: str = Field(description="显示名称")
    description: str = Field(default="", description="类型描述")
    shot_params: ShotParams = Field(default_factory=ShotParams)
    persona_mapping: dict[str, PersonaMappingItem] = Field(default_factory=dict)
    post_process: dict[str, Any] = Field(default_factory=dict, description="后处理配置")
    tags: list[str] = Field(default_factory=list)
    # ── 类型规范（PRD Part I F1/F3/F14，A-06）──
    prompts: dict[str, PromptSlot] = Field(default_factory=dict, description="10 槽位提示词")
    param_rules: list[ParamRule] = Field(default_factory=list, description="机器可读参数规则")
    signature_traits: list[SignatureTrait] = Field(default_factory=list, description="同一性指标")
    declares: TypeDeclares = Field(default_factory=TypeDeclares, description="类型声明块")
    waivers: list[str] = Field(default_factory=list, description="不可执行数字的豁免理由")

    @property
    def prompt_bundle(self) -> PromptBundle:
        return PromptBundle(slots=dict(self.prompts))

    @field_validator("prompts")
    @classmethod
    def _known_slots_only(cls, value: dict[str, PromptSlot]) -> dict[str, PromptSlot]:
        unknown = [key for key in value if key not in SLOT_KEYS and key not in SLOT_ALIASES]
        if unknown:
            raise ValueError(f"未知提示词槽位: {unknown}；合法槽位: {list(SLOT_KEYS)}")
        return value


class ValidateResult(BaseModel):
    ok: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class TypeListItem(BaseModel):
    """类型列表项。"""
    id: str
    name: str
    description: str
    builtin: bool
    tags: list[str] = Field(default_factory=list)


# ── API 端点 ───────────────────────────────────


@router.get("/list", response_model=list[TypeListItem])
async def list_types() -> list[TypeListItem]:
    """列出所有视频类型（内置 + 自定义）。"""
    result: list[TypeListItem] = []

    # 内置类型
    builtin_ids = {"knowledge_longform", "kichiku_fastcut", "digital_review", "vlog_daily"}
    for item in CategoryRegistry.list():
        result.append(TypeListItem(
            id=item["id"],
            name=item["name"],
            description=item["description"],
            builtin=item["id"] in builtin_ids,
        ))

    # 自定义类型（从 YAML 文件读取 tags）
    if _USER_TYPES_DIR.exists():
        for f in sorted(_USER_TYPES_DIR.glob("*.yaml")) + sorted(_USER_TYPES_DIR.glob("*.yml")):
            try:
                config = yaml.safe_load(f.read_text(encoding="utf-8"))
                if config and config.get("id") and config["id"] not in builtin_ids:
                    if not any(r.id == config["id"] for r in result):
                        result.append(TypeListItem(
                            id=config["id"],
                            name=config.get("name", config["id"]),
                            description=config.get("description", ""),
                            builtin=False,
                            tags=config.get("tags", []),
                        ))
            except Exception:
                # P1-16: 引用未定义的 config_path 会从 except 里抛 NameError → 整个列表 500
                logger.warning("Failed to parse type config: %s", f.name, exc_info=True)
                continue

    return result


@router.get("/{type_id}")
async def get_type(type_id: str) -> dict:
    """获取视频类型详细定义。"""
    plugin = CategoryRegistry.get(type_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Type '{type_id}' not found")

    # 尝试读取 YAML 源文件
    yaml_path = _find_yaml(type_id)
    if yaml_path:
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        config["_source_file"] = str(yaml_path)
        return config

    # 内置类型返回基本信息
    return {
        "id": plugin.plugin_id,
        "name": plugin.display_name,
        "description": plugin.description,
        "builtin": True,
    }


@router.post("/create", response_model=dict)
async def create_type(definition: TypeDefinition) -> dict:
    """创建自定义视频类型。"""
    validate_id(definition.id, "type_id")
    _validate_definition(definition)  # B23: 引用/取值检查
    _USER_TYPES_DIR.mkdir(parents=True, exist_ok=True)
    yaml_path = _USER_TYPES_DIR / f"{definition.id}.yaml"

    if yaml_path.exists():
        raise HTTPException(status_code=409, detail=f"Type '{definition.id}' already exists")

    if CategoryRegistry.get(definition.id) is not None:
        raise HTTPException(status_code=409, detail=f"Type '{definition.id}' conflicts with existing type")

    data = definition.model_dump(mode="json")
    yaml_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # 热注册到 CategoryRegistry
    try:
        _hot_register(yaml_path)
    except Exception as e:
        # 热注册失败回滚 — 删除刚写的文件，避免半注册残留
        yaml_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"类型注册失败，已回滚: {e}") from e

    logger.info("创建自定义视频类型: %s", definition.id)
    return {"status": "created", "id": definition.id, "file": str(yaml_path)}


@router.put("/{type_id}", response_model=dict)
async def update_type(type_id: str, definition: TypeDefinition) -> dict:
    """更新自定义视频类型。"""
    yaml_path = _find_yaml(type_id)
    if yaml_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Custom type '{type_id}' not found (builtin types cannot be modified)",
        )

    if definition.id != type_id:
        raise HTTPException(status_code=400, detail="Cannot change type ID after creation")

    _validate_definition(definition)  # B23
    data = definition.model_dump(mode="json")
    backup = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else None
    yaml_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # 热更新注册表（B24: 失败回滚旧配置）
    CategoryRegistry.unregister(type_id)
    try:
        _hot_register(yaml_path)
    except Exception as e:
        if backup is not None:
            yaml_path.write_text(backup, encoding="utf-8")
        try:
            _hot_register(yaml_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"类型更新注册失败，已回滚: {e}") from e

    logger.info("更新自定义视频类型: %s", type_id)
    return {"status": "updated", "id": type_id}


@router.delete("/{type_id}")
async def delete_type(type_id: str) -> dict:
    """删除自定义视频类型。"""
    yaml_path = _find_yaml(type_id)
    if yaml_path is None:
        raise HTTPException(status_code=404, detail=f"Custom type '{type_id}' not found")

    CategoryRegistry.unregister(type_id)
    yaml_path.unlink()
    logger.info("删除自定义视频类型: %s", type_id)
    return {"status": "deleted", "id": type_id}


@router.post("/preview")
async def preview_type(definition: TypeDefinition) -> dict:
    """预览类型定义 — 验证配置合法性并返回翻译示例。"""
    errors: list[str] = []

    if not definition.id or not definition.id.replace("_", "").replace("-", "").isalnum():
        errors.append("id 只能包含字母、数字、下划线和连字符")

    sp = definition.shot_params
    if sp.min_shot_sec <= 0:
        errors.append("min_shot_sec 必须大于 0")
    if sp.max_shot_sec < sp.min_shot_sec:
        errors.append("max_shot_sec 不能小于 min_shot_sec")
    if sp.transition_type not in _ALLOWED_TRANSITIONS:
        errors.append(f"不支持的转场类型: {sp.transition_type}")

    # 模拟翻译
    sample_persona = {
        "rhythm": {"cut_density_tier": "high"},
        "visual": {"animation_style": "smooth_fade"},
    }
    translated: dict[str, Any] = {}
    for key, mapping in definition.persona_mapping.items():
        parts = mapping.source.split(".")
        val: Any = sample_persona
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                val = None
                break
        if val is not None:
            translated[key] = val

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "shot_params": sp.model_dump(),
        "sample_translation": translated,
    }


# ── 辅助函数 ───────────────────────────────────


def _validate_definition(definition: TypeDefinition) -> None:
    """B23: 类型定义引用/取值检查（创建/更新前）。"""
    errors: list[str] = []
    sp = definition.shot_params
    if sp.transition_type not in _ALLOWED_TRANSITIONS:
        errors.append(f"不支持的转场类型: {sp.transition_type}")
    if sp.min_shot_sec <= 0:
        errors.append("min_shot_sec 必须 > 0")
    if sp.max_shot_sec < sp.min_shot_sec:
        errors.append("max_shot_sec 不能小于 min_shot_sec")
    if sp.transition_duration_sec < 0:
        errors.append("transition_duration_sec 不能为负")
    for key, item in definition.persona_mapping.items():
        if not item.source:
            errors.append(f"映射项 {key} 缺少 source")
        if item.transform not in ("direct", "scale", "lookup"):
            errors.append(f"映射项 {key} 的 transform 不支持: {item.transform}")
    # A-06：规范契约校验（悬空数字 / 规则自洽 / 指标与 genre 矛盾 / 槽位合法）
    for issue in spec_issues(definition):
        if issue.severity == "error":
            errors.append(f"[{issue.code}] {issue.message}")
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))


def spec_issues(definition: TypeDefinition) -> list[Any]:
    """把 TypeDefinition 转成规范校验问题列表（供 create/update 与 /validate 共用）。

    向后兼容（PRD F6）：**完全没写 prompts 的老类型**视为"未启用新契约"，
    不强制 ``genre``（否则所有历史 YAML 与第三方定义会一夜之间失效）。
    一旦写了任何一个槽位，就按新契约校验（含 genre 必填、悬空数字等）。
    """
    issues = validate_bundle(
        definition.prompt_bundle,
        definition.param_rules,
        definition.signature_traits,
    )
    if not (definition.prompts or {}):
        issues = [issue for issue in issues if issue.code != "genre_required"]
    return issues


def _coerce_definition(payload: dict[str, Any]) -> TypeDefinition:
    """宽松构造（/validate 用）：允许前端只提交部分字段。"""
    return TypeDefinition(**payload)


@router.post("/validate", response_model=ValidateResult)
async def validate_type(payload: dict[str, Any]) -> ValidateResult:
    """干跑校验：不落盘，返回全部错误与告警。"""
    try:
        definition = _coerce_definition(payload)
    except Exception as exc:  # noqa: BLE001 - 结构错误一律 400 语义
        return ValidateResult(
            ok=False,
            errors=[{"code": "invalid_definition", "message": str(exc), "where": ""}],
        )
    issues = spec_issues(definition)
    return ValidateResult(
        ok=not any(i.severity == "error" for i in issues),
        errors=[
            {"code": i.code, "message": i.message, "where": i.where}
            for i in issues
            if i.severity == "error"
        ],
        warnings=[
            {"code": i.code, "message": i.message, "where": i.where}
            for i in issues
            if i.severity != "error"
        ],
    )


@router.post("/switch", response_model=dict)
async def switch_type(payload: dict[str, Any]) -> dict:
    """类型切换（V19）：``type_deformation`` 时的唯一合法出路之一。

    只记录 ``from_type → to_type`` 与理由，**不修改任何类型定义**——
    切换的语义是"换一套规范重新出片"。
    """
    try:
        return run_switch(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def run_switch(payload: dict[str, Any]) -> dict[str, Any]:
    """纯逻辑实现（便于单测）。"""
    from clipwright.services.type_switch import append_switch, plan_switch, switch_note

    record = plan_switch(
        str(payload.get("from_type") or ""),
        str(payload.get("to_type") or ""),
        reason=str(payload.get("reason") or ""),
        require_known=not bool(payload.get("allow_unknown")),
    )
    path = payload.get("log_path")
    if path:
        append_switch(str(path), record)
    return {"ok": True, **record.as_dict(), "note": switch_note(record)}


@router.post("/{type_id}/genre-check", response_model=dict)
async def genre_check(type_id: str, payload: dict[str, Any]) -> dict:
    """干跑类型同一性校验（A-10 / F12）：给时间线，返回指标与逐条判定。

    L1 恒开；L2/L3 裁判由 ``payload.enable_genre_judge`` 或服务端配置
    ``CLIPWRIGHT_GENRE_JUDGE_ENABLED`` 控制（D7 默认关）。
    """
    try:
        return await run_genre_check_async(type_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _prepare_genre_check(type_id: str, payload: dict[str, Any]):
    """把 payload 变成 (spec, timeline, measured)；非法输入抛 ValueError。"""
    from clipwright.schema.timeline import Timeline
    from clipwright.services.mv_cues import build_grid
    from clipwright.services.trait_collector import collect_traits
    from clipwright.services.type_spec_runtime import load_spec

    raw_timeline = payload.get("timeline")
    if not isinstance(raw_timeline, dict):
        raise ValueError("payload.timeline 必须是时间线 JSON 对象")
    try:
        timeline = Timeline(**raw_timeline)
    except Exception as exc:  # noqa: BLE001 - 结构错误一律 400 语义
        raise ValueError(f"时间线非法: {exc}") from exc

    spec = load_spec(type_id)
    grid = None
    bpm = float(payload.get("bpm") or 0)
    if bpm > 0:
        grid = build_grid(bpm, float(payload.get("beat_zero_sec") or 0))

    measured = collect_traits(
        timeline,
        scenes=payload.get("scenes"),
        grid=grid,
    )
    return spec, timeline, measured


def _genre_result(type_id: str, spec, measured, report) -> dict[str, Any]:
    return {
        "type_id": type_id,
        "traits_declared": len(spec.traits),
        "measured": measured,
        "ok": report.ok,
        "judge": report.judge,
        "judge_detail": report.judge_detail,
        "summary": report.summary(),
        "traits": [item.model_dump() for item in report.traits],
        "mismatches": report.mismatches,
        "warnings": report.warnings,
    }


def run_genre_check(type_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """纯逻辑实现（**只跑 L1**，便于单测）：时间线 + 类型 → 指标 + 判定。"""
    from clipwright.services.genre_conformance import check_conformance

    spec, _timeline, measured = _prepare_genre_check(type_id, payload)
    report = check_conformance(spec.traits, measured, enable_genre_judge=False)
    return _genre_result(type_id, spec, measured, report)


async def run_genre_check_async(
    type_id: str,
    payload: dict[str, Any],
    *,
    llm: Any = None,
) -> dict[str, Any]:
    """含 LLM 裁判的完整实现（V17）：开关关时行为与 ``run_genre_check`` 一致。"""
    from clipwright.services.genre_conformance import acheck_conformance
    from clipwright.services.genre_judge import judge_enabled, llm_judge

    spec, timeline, measured = _prepare_genre_check(type_id, payload)
    enabled = judge_enabled(payload)
    judge_fn: Any = None
    if enabled:

        async def _judge(traits, measured_now, report):
            return await llm_judge(
                traits,
                measured_now,
                report,
                type_id=type_id,
                timeline=timeline,
                llm=llm,
            )

        judge_fn = _judge

    report = await acheck_conformance(
        spec.traits,
        measured,
        enable_genre_judge=enabled,
        judge=judge_fn,
    )
    return _genre_result(type_id, spec, measured, report)
@router.post("/resolve", response_model=dict)
async def resolve_type(payload: dict[str, Any]) -> dict:
    """干跑裁决：给定用户参数，返回最终参数 + 冲突 + 变形判定。"""
    from clipwright.services.prompt_authority import PromptAuthority

    try:
        definition = _coerce_definition(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user_params = payload.get("user_params") or {}
    authority = PromptAuthority(
        prompts=definition.prompt_bundle,
        rules=definition.param_rules,
        traits=definition.signature_traits,
        category_id=definition.id,
    )
    resolution = authority.resolve(user=user_params)
    return {
        "params": resolution.params,
        "trace": resolution.trace,
        "conflicts": [c.model_dump() for c in resolution.conflicts],
        "traits": [t.model_dump() for t in resolution.traits],
        "deformation": resolution.deformation,
        "prompts": resolution.prompts,
        "dropped_slots": resolution.dropped_slots,
    }


def _find_yaml(type_id: str) -> Optional[Path]:
    """查找类型对应的 YAML 文件。"""
    if not _USER_TYPES_DIR.exists():
        return None
    for ext in (".yaml", ".yml"):
        p = _USER_TYPES_DIR / f"{type_id}{ext}"
        if p.exists():
            return p
    return None


def _hot_register(yaml_path: Path) -> None:
    """热注册单个 YAML 类型到 CategoryRegistry。"""
    try:
        from clipwright.category.dynamic import DynamicCategoryPlugin
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if config and "id" in config:
            if CategoryRegistry.get(config["id"]) is None:
                CategoryRegistry.register(DynamicCategoryPlugin(config))
    except Exception as e:
        logger.warning("热注册类型失败 %s: %s", yaml_path.name, e)
