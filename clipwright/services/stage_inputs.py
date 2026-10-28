"""阶段输入装配（PRD §3.4）：把老编排器 `_dispatch` 里的隐性知识搬成显式函数。

管线时代这段逻辑藏在 `PipelineOrchestratorV2._dispatch`（约 70 行 if/elif）里，
只有走管线才能用。主 Agent 派阶段时必须能复用同一套装配规则，否则两侧会漂移
（同一个 `MaterialInput`，一处填 `script_skeleton`、另一处填 `scenes`）。

**缺键不猜**：必需的键缺失时抛 `StageInputMissing`，由调用方回报
`failure.category="input_insufficient"` 并告诉主 Agent 该先派哪一步。
"""

from __future__ import annotations

from typing import Any, Iterable

from clipwright.schema.agent import AgentContext

#: 各阶段的**必需**黑板键（缺则 input_insufficient）
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "requirements": (),
    "structure": (),
    "material": ("script_skeleton",),
    "edit": ("script_skeleton", "candidate_clips"),
    "animation": ("timeline",),
    "audio": ("timeline",),
    "quality": ("timeline",),
}

#: 缺某个键时该先派哪个阶段（给主 Agent 的"下一步建议"，不是一句报错）
KEY_PRODUCER: dict[str, str] = {
    "script_skeleton": "structure",
    "scenes": "structure",
    "candidate_clips": "material",
    "timeline": "edit",
}


class StageInputMissing(RuntimeError):
    """阶段输入不全（缺黑板键）。"""

    def __init__(self, stage: str, missing: list[str], suggestion: list[str]) -> None:
        self.stage = stage
        self.missing = missing
        self.suggestion = suggestion
        detail = "、".join(missing)
        hint = "、".join(suggestion) or "structure"
        super().__init__(f"阶段 {stage} 缺输入：{detail}（建议先派 {hint}）")


def build_context(task: Any, *, extra: dict[str, Any] | None = None) -> AgentContext:
    """从 Task 造 `AgentContext`（阶段 Agent 的已知签名，字段与原管线一致）。"""
    request = getattr(task, "request", task)
    extra_params: dict[str, Any] = dict(getattr(request, "extra", {}) or {})
    extra_params.update(extra or {})
    task_id = str(getattr(task, "id", "") or extra_params.get("task_id") or "")
    return AgentContext(
        pipeline_id=task_id,                 # 兼容字段：Agent 内部仍按 pipeline_id 记账
        persona_id=str(getattr(request, "persona_id", "") or ""),
        category_plugin_id=str(getattr(request, "category_plugin_id", "") or ""),
        topic=str(getattr(request, "topic", "") or ""),
        extra_params=extra_params,
    )


def missing_keys(stage: str, board: dict[str, Any], override: dict[str, Any] | None = None) -> list[str]:
    available = set(board or {}) | set(override or {})
    return [k for k in REQUIRED_KEYS.get(stage, ()) if k not in available]


def validate(stage: str, inputs: dict[str, Any],
             board: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """校验主 agent 显式给的 inputs；返回 `needs[]`（空列表 = 齐了）。

    与 `assemble` 的区别：**只校验、不填值**。缺什么就报什么，由主 agent 决定补什么。

    `board` 传入时，**黑板里已有的键也算满足**：这些是大对象（分镜/候选素材/
    时间线），由上游阶段产出、`assemble` 会从黑板补齐。E2E 2026-10-28 实测反例：
    严格只认显式键时，模型为了通过校验会把 43 个场景**内联进 tool_call 参数**，
    参数被截断 → `JSONDecodeError: Unterminated string` → 整个任务 failed。
    """
    given = dict(inputs or {})
    have = set(given) | {k for k, v in (board or {}).items() if v not in (None, "", [], {})}
    missing = [k for k in REQUIRED_KEYS.get(stage, ()) if k not in have]
    needs: list[dict[str, Any]] = []
    for key in missing:
        needs.append({
            "field": key,
            "why": f"{stage} 阶段必需输入",
            "from_stage": KEY_PRODUCER.get(key, "structure"),
        })
    return needs


def suggestions_for(missing: Iterable[str]) -> list[str]:
    out: list[str] = []
    for key in missing:
        producer = KEY_PRODUCER.get(key, "structure")
        if producer not in out:
            out.append(producer)
    return out


def assemble(stage: str, *, task: Any, board: dict[str, Any],
             context: AgentContext | None = None,
             override: dict[str, Any] | None = None) -> Any:
    """按阶段构造 `XInput`；构造规则与老编排器 `_dispatch` 逐条对应。"""
    ctx = context or build_context(task)
    data: dict[str, Any] = {**(board or {}), **(override or {})}
    # 显式传参里"给了键但是空值"（`scenes: []` / `script_skeleton: {}`）等于没给：
    # 大对象（分镜、候选素材、时间线）本来就该由上游阶段产出、从黑板流下来，
    # 让主 agent 内联 43 个场景既不现实也容易传空。实测（E2E 2026-10-28）：
    # 主 agent 传了空 script_skeleton → `EditAgent: 0 个场景, 0 候选素材`
    # → edit 产出空时间线 → 收尾守门判失败。空值一律回退到黑板。
    _flow_keys = ("script_skeleton", "candidate_clips", "scenes", "timeline")
    for key in _flow_keys:
        if not data.get(key) and (board or {}).get(key):
            data[key] = board[key]
    extra = ctx.extra_params
    # override 也要能覆盖 brief/plan：它们平时来自 request.extra，
    # 但主 Agent 需要能在某一阶段临时改写（例如把用户新要求塞进 constraints/brief）
    brief = data.get("creative_brief", extra.get("creative_brief"))
    plan = data.get("production_plan", extra.get("production_plan"))

    miss = [k for k in REQUIRED_KEYS.get(stage, ()) if k not in data]
    if miss:
        raise StageInputMissing(stage, miss, suggestions_for(miss))

    if stage == "requirements":
        # 需求阶段只做"规划/改写"，输入是原始需求与已有简报，不产出时间线
        return {
            "topic": ctx.topic,
            "creative_brief": brief,
            "production_plan": plan,
            **data,
        }

    if stage == "structure":
        from clipwright.schema.agent import StructureInput

        return StructureInput(
            context=ctx,
            persona_config=data.get("persona_config", {}) or {},
            persona_prompt=data.get("persona_prompt"),
            vision_prompt=data.get("vision_prompt"),
            rag_context=data.get("rag_context", "") or "",
            creative_brief=brief,
            production_plan=plan,
        )

    if stage in ("material", "edit"):
        # 兜底：material/edit 只读 `script_skeleton["scenes"]`，而黑板里 scenes 是
        # **独立的 key**（stage_merge 白名单）。老 StructureOutput 的 script_skeleton
        # 里没有 scenes 时，下游拿到的是 scenes=[] → 「候选素材 0 条」。
        # 这里把黑板上的 scenes 补进去（有则补、无则零变化）。
        skeleton = dict(data.get("script_skeleton", {}) or {})
        if not skeleton.get("scenes") and isinstance(data.get("scenes"), list):
            skeleton["scenes"] = data["scenes"]
        data["script_skeleton"] = skeleton

    if stage == "material":
        from clipwright.schema.agent import MaterialInput

        return MaterialInput(
            context=ctx,
            script_skeleton=data.get("script_skeleton", {}) or {},
            persona_config=data.get("persona_config", {}) or {},
            material_plugin_config=data.get("material_plugin_config", {}) or {},
            creative_brief=brief,
            production_plan=plan,
        )

    if stage == "edit":
        from clipwright.schema.agent import EditInput

        return EditInput(
            context=ctx,
            script_skeleton=data.get("script_skeleton", {}) or {},
            candidate_clips=data.get("candidate_clips", []) or [],
            creative_brief=brief,
            production_plan=plan,
        )

    if stage == "animation":
        from clipwright.schema.agent import AnimationInput
        from clipwright.plugins.generated_source import generated_image_entries
        from clipwright.schema.timeline import Timeline

        return AnimationInput(
            context=ctx,
            timeline=_as_timeline(data.get("timeline")),
            visual_config=data.get("visual_config", {}) or {},
            persona_prompt=data.get("persona_prompt"),
            vision_prompt=data.get("vision_prompt"),
            image_assets=data.get("image_assets") or generated_image_entries(),
            creative_brief=brief,
            production_plan=plan,
        )

    if stage == "audio":
        from clipwright.schema.agent import AudioInput

        return AudioInput(
            context=ctx,
            timeline=_as_timeline(data.get("timeline")),
            audio_config=data.get("audio_config", {}) or {},
            creative_brief=brief,
            production_plan=plan,
        )

    if stage == "quality":
        from clipwright.schema.agent import QualityInput

        return QualityInput(
            context=ctx,
            timeline=_as_timeline(data.get("timeline")),
            constraints=data.get("constraints", {}) or {},
            creative_brief=brief,
            production_plan=plan,
        )

    raise ValueError(f"未知阶段: {stage}")


def _as_timeline(value: Any):
    from clipwright.schema.timeline import Timeline

    if isinstance(value, Timeline):
        return value
    if isinstance(value, dict) and value.get("path"):
        # 黑板存的是文件指针（超大时间线）：读回来
        from pathlib import Path

        import json

        raw = Path(str(value["path"])).read_text(encoding="utf-8")
        return Timeline(**json.loads(raw))
    if isinstance(value, dict) and value.get("artifact") and not value.get("tracks"):
        # 主 agent 传的是产物引用（S2）：按 artifact 读回真实内容
        artifact_id = str(value["artifact"])
        path = str(value.get("path") or "")
        if not path:
            raise ValueError(f"artifact 引用缺少 path：{artifact_id}")
        from pathlib import Path

        import json

        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("artifact"):
            raw = Path(str(data["path"])).read_text(encoding="utf-8")
            data = json.loads(raw)
        return Timeline(**data) if isinstance(data, dict) else Timeline()
    if isinstance(value, dict):
        return Timeline(**value)
    raise ValueError("timeline 必须是 Timeline / dict / 文件指针")


__all__ = [
    "KEY_PRODUCER",
    "REQUIRED_KEYS",
    "StageInputMissing",
    "assemble",
    "build_context",
    "missing_keys",
    "suggestions_for",
]
