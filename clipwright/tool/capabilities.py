"""语义能力解析层（capability → 实时工具/技能）。

## 为什么需要这一层

Agent 里写死 ``ToolRegistry.execute("frame_validator", ...)`` 等于把"用哪个工具"
固化进代码：插件新装/卸载、依赖缺失（``is_configured() == False``）、同名实现换人，
Agent 全都感知不到——它只会在运行期拿到一个 NOT_FOUND。

所以调用方应表达**它要做什么**（能力名），由本模块在**调用时刻**从注册表实时解析：

1. **声明式优先**：任何工具（含插件工具）设置 ``capability = "frame_check"`` 即自动成为
   该能力的候选，``capability_priority`` 大的优先（同档按注册顺序）。
   插件因此可以"接管"一个能力，而不需要改 Agent 源码。
2. **内置别名兜底**：``CAPABILITY_ALIASES`` 给出历史工具名，保证没有任何插件时行为
   与改造前一致（这是硬约束：本次改造不得改变既有产线行为）。
3. **可用性过滤**：候选必须 ``is_available()`` 且 ``is_configured()``（若声明）为真；
   被过滤掉的候选会出现在 ``explain()`` 里，便于排查"为什么这次没工具"。

能力名不是工具名。能力名是稳定契约，工具名是实现细节。
"""

from __future__ import annotations

from typing import Any, Iterable

#: 能力名 → 内置工具名（按优先级）。仅在内核/插件都没有声明该能力时使用。
CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    # 素材 / 质检
    "frame_check": ("frame_validator",),
    "vision_score": ("vision_llm",),
    "material_search": ("material_filter",),
    "material_match": ("semantic_match",),
    "scene_detect": ("scene_detect",),
    "media_probe": ("media_probe",),
    # 剪辑
    "video_trim": ("video_trim",),
    "video_concat": ("video_concat",),
    "video_speed": ("video_speed",),
    "text_placeholder": ("generate_text_video",),
    "transition": ("transition_apply",),
    "watermark": ("watermark",),
    # 动画 / 文字
    "list_animations": ("list_animations",),
    "describe_mg": ("describe_llm_mg",),
    "typewriter": ("typewriter_animation",),
    "tracking_text": ("tracking_text",),
    "text_diagram": ("text_diagram",),
    "subtitle_burn": ("subtitle_burn",),
    "subtitle_overflow": ("subtitle_overflow",),
    # 音频
    "bpm_detect": ("bpm_detect",),
    "audio_extract": ("audio_extract",),
    "audio_mix": ("audio_mix",),
    "tts": ("text_to_speech",),
    "voice_clone": ("voice_clone",),
    "transcribe": ("whisper_transcribe",),
    # PV 引擎（插件贡献；无插件时下面这些能力就是"不可用"，调用方必须降级）
    "pv_authoring": ("pv_engine_init",),
    "pv_audit": ("pv_engine_audit",),
    "pv_preview": ("pv_engine_stills",),
    "pv_render": ("pv_engine_render",),
}

#: 技能名本身就是稳定的（内置 4 个 + 插件技能），这里只声明"能力 → 技能名候选"。
SKILL_CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "captions": ("generate_captions",),
    "dub": ("dub_script",),
    "structure_analysis": ("analyze_video_structure",),
    "rhythm_analysis": ("analyze_audio_rhythm",),
    "pv_review": ("pv_engine_review",),
    "pv_authoring_skill": ("pv_engine_authoring",),
}


def _configured(tool: Any) -> bool:
    """``is_configured`` 契约（与 ToolRegistry.list_agent_callable 同语义）。"""
    checker = getattr(tool, "is_configured", None)
    if not callable(checker):
        return True
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001 — 配置检查失败按未配置处理
        return False


def is_usable(tool: Any) -> bool:
    """可用 = 依赖满足 + （若声明）配置就绪。"""
    try:
        if not tool.is_available():
            return False
    except Exception:  # noqa: BLE001
        return False
    return _configured(tool)


def _registry():
    from clipwright.tool.registry import ToolRegistry

    return ToolRegistry


def _all_tools() -> list[Any]:
    reg = _registry()
    # 注册表内部顺序 = 注册顺序（dict 保序）；能力同档时按此顺序
    return list(reg._tools.values()) if hasattr(reg, "_tools") else []


def candidates(capability: str) -> list[Any]:
    """该能力的全部候选（已按优先级排序，**包含不可用项**，供 explain）。"""
    declared: list[Any] = []
    for tool in _all_tools():
        if str(getattr(tool, "capability", "") or "") != capability:
            continue
        declared.append(tool)
    # 优先级降序；stable sort 保留注册顺序
    declared.sort(key=lambda t: -int(getattr(t, "capability_priority", 0) or 0))

    seen = {getattr(t, "name", "") for t in declared}
    alias_names = CAPABILITY_ALIASES.get(capability, ())
    reg = _registry()
    for name in alias_names:
        tool = reg.get(name)
        if tool is not None and getattr(tool, "name", "") not in seen:
            declared.append(tool)
            seen.add(getattr(tool, "name", ""))
    return declared


def resolve(capability: str, *, prefer: Iterable[str] = ()) -> Any | None:
    """返回该能力当前可用的第一个工具；没有则 None。

    ``prefer`` 给调用方一次"点名"的机会（例如 Agent 配置里指定了某个实现），
    但只有当它确实注册且可用时才生效——点了名却不可用会静默退回候选顺序，
    因为"配置写错"不该让整条产线停摆（调用方会从返回值看到实际用了谁）。
    """
    reg = _registry()
    for name in prefer:
        tool = reg.get(name)
        if tool is not None and is_usable(tool) and _matches(tool, capability):
            return tool
    for tool in candidates(capability):
        if is_usable(tool):
            return tool
    return None


def _matches(tool: Any, capability: str) -> bool:
    """点名工具是否"算"这个能力（声明匹配、或名字在别名表里）。"""
    if str(getattr(tool, "capability", "") or "") == capability:
        return True
    return getattr(tool, "name", "") in CAPABILITY_ALIASES.get(capability, ())


def resolve_name(capability_or_name: str, *, prefer: Iterable[str] = ()) -> Any | None:
    """先按能力解析；失败再当作工具名直取（向后兼容显式名字调用）。"""
    tool = resolve(capability_or_name, prefer=prefer)
    if tool is not None:
        return tool
    return _registry().get(capability_or_name)


def skill_resolve(capability_or_name: str, *, prefer: Iterable[str] = ()) -> Any | None:
    """技能解析：先按技能能力别名，再按技能名直取。"""
    from clipwright.skill.registry import SkillRegistry

    for name in (*prefer, *SKILL_CAPABILITY_ALIASES.get(capability_or_name, ())):
        skill = SkillRegistry.get(name)
        if skill is not None and skill.is_available():
            return skill
    skill = SkillRegistry.get(capability_or_name)
    if skill is not None and skill.is_available():
        return skill
    return None


def fallback_name(capability: str) -> str:
    """该能力的内置别名（用于"注册表暂时没有实例、但有历史工具名"的兼容路径）。"""
    names = CAPABILITY_ALIASES.get(capability) or ()
    return names[0] if names else ""


def explain(capability: str) -> dict[str, Any]:
    """诊断：候选、是否可用、被过滤原因。供 /api/tool/capabilities 与排障使用。"""
    rows: list[dict[str, Any]] = []
    for tool in candidates(capability):
        reason = ""
        try:
            if not tool.is_available():
                reason = "依赖缺失：" + ", ".join(getattr(tool, "dependencies", []) or [])
            elif not _configured(tool):
                reason = "未配置（is_configured=False）"
        except Exception as e:  # noqa: BLE001
            reason = f"可用性检查异常：{e}"
        rows.append({
            "name": getattr(tool, "name", ""),
            "declared": str(getattr(tool, "capability", "") or "") == capability,
            "priority": int(getattr(tool, "capability_priority", 0) or 0),
            "owner": str(getattr(tool, "_plugin_id", "") or "builtin"),
            "usable": not reason,
            "reason": reason,
        })
    chosen = next((r["name"] for r in rows if r["usable"]), None)
    return {"capability": capability, "chosen": chosen, "candidates": rows}


def catalog() -> dict[str, Any]:
    """全部能力 → 当前选中的实现（供前端/诊断页一次性拉取）。"""
    return {
        "tools": {cap: explain(cap) for cap in sorted(CAPABILITY_ALIASES)},
        "skills": {
            cap: [n for n in names if _skill_available(n)]
            for cap, names in sorted(SKILL_CAPABILITY_ALIASES.items())
        },
    }


def _skill_available(name: str) -> bool:
    from clipwright.skill.registry import SkillRegistry

    skill = SkillRegistry.get(name)
    return bool(skill is not None and skill.is_available())


__all__ = [
    "CAPABILITY_ALIASES",
    "SKILL_CAPABILITY_ALIASES",
    "catalog",
    "candidates",
    "explain",
    "is_usable",
    "resolve",
    "resolve_name",
    "skill_resolve",
]
