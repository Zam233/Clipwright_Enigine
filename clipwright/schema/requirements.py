"""需求采集相关数据模型。

A-01（开发计划）：修正 **字段漂移**——旧实现把 brief 字段表写死在
``requirements_agent.CREATIVE_BRIEF_SYSTEM`` 里（10 项），而本模型只有 7 项，
且名字对不上（``core_message / structure_suggestion / key_elements /
special_requirements`` 被静默丢弃，``key_points / references`` 永远为空）。

现在以**本模型为唯一真相**：提示词里的字段表与 JSON 骨架由
``brief_prompt_template()`` 从模型自动生成，结构上不可能再漂。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class SessionStatus(str, Enum):
    INIT = "init"
    GATHERING = "gathering"
    BRIEF_READY = "brief_ready"
    BRIEF_CONFIRMED = "brief_confirmed"
    PLAN_READY = "plan_ready"
    PLAN_CONFIRMED = "plan_confirmed"
    PIPELINE_RUNNING = "pipeline_running"
    PIPELINE_DONE = "pipeline_done"
    EXPIRED = "expired"


class RequirementsMessage(BaseModel):
    """单条对话消息。"""

    role: MessageRole
    content: str
    timestamp: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreativeBrief(BaseModel):
    """创作方案摘要（创意简报）。

    字段集与 ``requirements_agent`` 的提示词由 ``brief_prompt_template()`` 保持同步，
    **新增字段只需改这里**。
    """

    title: str = Field(default="", description="视频标题")
    overview: str = Field(default="", description="一句话概述")
    target_audience: str = Field(default="", description="目标受众")
    core_message: str = Field(default="", description="核心信息（观众要记住的一句话）")
    style_direction: str = Field(default="", description="风格方向")
    structure_suggestion: str = Field(default="", description="结构建议")
    duration_estimate: str = Field(default="", description="预估时长")
    key_elements: list[str] = Field(default_factory=list, description="必须出现的关键元素")
    special_requirements: list[str] = Field(
        default_factory=list, description="特殊要求与红线"
    )
    key_points: list[str] = Field(default_factory=list, description="要点（可空）")
    references: list[str] = Field(default_factory=list, description="参考片/参考素材")
    animation_intents: list[dict[str, Any]] = Field(
        default_factory=list, description="动画需求意图（结构同 AnimationIntent）"
    )
    #: 展示 spec 容器（`spec_id -> spec`）：**只用于展示**，权威数据仍是上面这些字段。
    #: 契约级清洗在服务层装配点（`services/display_spec/assemble.py`）——那里才拿得到
    #: 运行时插件注册表；模型层只做**形状收口**，不 import 任何插件知识。
    display_specs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="展示 spec 容器（spec_id -> spec；仅展示，权威数据在其它字段）",
    )

    @field_validator("display_specs", mode="before")
    @classmethod
    def _shape_display_specs(cls, value: Any) -> dict[str, dict[str, Any]]:
        return _shape_only_specs(value)


class ProductionPlan(BaseModel):
    """成片规划书（轻量兼容视图）。

    v1.2 起规划以 ``schema.category_spec.PlanDocument`` 为准；本模型保留是为了
    兼容历史会话与前端字段，字段与 PlanDocument 的汇总视图同构。
    """

    scene_count: int = 0
    total_duration_sec: float = 0
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    music_suggestion: str = ""
    transition_style: str = ""
    #: v1.2：结构化规划（存在时优先于 scenes 的即席结构）
    plan: Optional[dict[str, Any]] = None
    #: 展示 spec 容器（只做展示，确认与执行仍以 plan / markdown 为准）
    display_specs: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="展示 spec 容器（spec_id -> spec）"
    )

    @field_validator("display_specs", mode="before")
    @classmethod
    def _shape_display_specs(cls, value: Any) -> dict[str, dict[str, Any]]:
        return _shape_only_specs(value)


#: 展示容器最多几个 spec（防御性上限；当前能力只有 brief/plan 两个）。
_MAX_DISPLAY_SPECS = 8
#: 整个容器的 JSON 字符上限（防把模型输出原样塞进来撑爆落库文档）。
_MAX_DISPLAY_SPECS_BYTES = 128 * 1024


def _shape_only_specs(value: Any) -> dict[str, dict[str, Any]]:
    """只做**形状收口**（不按契约清洗——契约是运行时插件注册表的东西，模型层拿不到）。

    违规抛 `ValueError`（pydantic 会转成校验错误）；合法则原样保留内容，
    真正的组件/上限/动作校验在服务层装配点完成。
    """
    if value in (None, "", [], {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError("display_specs 必须是对象（spec_id -> spec）")
    if len(value) > _MAX_DISPLAY_SPECS:
        raise ValueError(f"display_specs 最多 {_MAX_DISPLAY_SPECS} 项")
    out: dict[str, dict[str, Any]] = {}
    for key, spec in value.items():
        name = str(key).strip()
        if not name:
            raise ValueError("display_specs 的 spec_id 不能为空")
        if not isinstance(spec, dict):
            raise ValueError(f"display_specs[{name}] 必须是对象")
        out[name] = spec
    try:
        import json

        size = len(json.dumps(out, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"display_specs 无法序列化：{exc}") from exc
    if size > _MAX_DISPLAY_SPECS_BYTES:
        raise ValueError(f"display_specs 超过 {_MAX_DISPLAY_SPECS_BYTES} 字符")
    return out


# ── 提示词模板（由模型生成，杜绝字段漂移）────────────────────────────────────


def _placeholder(name: str) -> Any:
    """按字段类型给一个 JSON 占位值（列表给 []，其余给 ""）。"""
    info = CreativeBrief.model_fields[name]
    factory = getattr(info, "default_factory", None)
    if factory is not None:
        try:
            value = factory()  # type: ignore[call-arg]
        except TypeError:
            value = None
        if isinstance(value, (list, dict)):
            return [] if isinstance(value, list) else {}
    return ""


def brief_field_names() -> list[str]:
    """字段名（保持模型定义顺序，输出稳定）。"""
    return list(CreativeBrief.model_fields)


def brief_prompt_template() -> str:
    """生成提示词里的字段表与 JSON 骨架（两段都用模型字段，顺序固定）。"""
    table = ["| 字段 | 类型 | 说明 |", "|---|---|---|"]
    properties = CreativeBrief.model_json_schema().get("properties", {})
    for name in brief_field_names():
        info = CreativeBrief.model_fields[name]
        schema = properties.get(name, {})
        type_name = schema.get("type") or (
            "array" if schema.get("items") is not None else "string"
        )
        table.append(f"| `{name}` | {type_name} | {info.description or ''} |")

    skeleton = {
        "reply": "对用户的自然语言回复",
        "brief_draft": {name: _placeholder(name) for name in brief_field_names()},
        "is_ready": False,
        "missing_info": ["还未了解的信息"],
    }
    body = json.dumps(skeleton, ensure_ascii=False, indent=2)
    return "\n".join(table) + "\n\n```json\n" + body + "\n```"


__all__ = [
    "CreativeBrief",
    "MessageRole",
    "ProductionPlan",
    "RequirementsMessage",
    "SessionStatus",
    "brief_field_names",
    "brief_prompt_template",
]
