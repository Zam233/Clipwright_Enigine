"""Agent 数据模型 — Agent 输入/输出契约。

每个 Agent 是一个 LangGraph 节点，输入和输出都有严格的类型定义。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .timeline import Timeline


class AgentDecision(str, Enum):
    """Agent 执行后的决策结果。"""
    PASS = "pass"
    FAIL = "fail"
    RETRY = "retry"
    SKIP = "skip"


class AgentContext(BaseModel):
    """Agent 执行的上下文信息。"""
    pipeline_id: str
    persona_id: str
    category_plugin_id: str
    topic: str
    extra_params: dict[str, Any] = Field(default_factory=dict)


class StructureInput(BaseModel):
    """结构 Agent 的输入。"""
    context: AgentContext
    persona_config: dict[str, Any] = Field(default_factory=dict)
    persona_prompt: Optional[str] = Field(
        default=None, description="Persona 的 Prompt 指令，注入 LLM system prompt"
    )
    rag_context: Optional[str] = Field(
        default=None, description="RAG 检索结果上下文"
    )


class StructureOutput(BaseModel):
    """结构 Agent 的输出 — 脚本骨架。"""
    agent_name: str = "structure_agent"
    decision: AgentDecision = AgentDecision.PASS
    script_skeleton: dict[str, Any] = Field(
        default_factory=dict,
        description="脚本骨架，包含段落结构、关键论点",
    )
    scenes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="分镜列表 [{title, description, keywords, ...}]",
    )
    error: Optional[str] = Field(default=None)


class MaterialInput(BaseModel):
    """素材 Agent 的输入。"""
    context: AgentContext
    script_skeleton: dict[str, Any]
    persona_config: dict[str, Any] = Field(default_factory=dict)


class MaterialOutput(BaseModel):
    """素材 Agent 的输出 — 候选素材集合。"""
    agent_name: str = "material_agent"
    decision: AgentDecision = AgentDecision.PASS
    candidate_clips: list[dict[str, Any]] = Field(
        default_factory=list,
        description="候选素材列表 [{asset_id, scene_index, score, ...}]",
    )
    error: Optional[str] = Field(default=None)
    material_notes: list[str] = Field(default_factory=list, exclude=True)


class EditInput(BaseModel):
    """剪辑 Agent 的输入。"""
    context: AgentContext
    script_skeleton: dict[str, Any]
    candidate_clips: list[dict[str, Any]]


class EditOutput(BaseModel):
    """剪辑 Agent 的输出 — 粗剪时间线。"""
    agent_name: str = "edit_agent"
    decision: AgentDecision = AgentDecision.PASS
    timeline: Optional[Timeline] = Field(default=None)
    edit_notes: list[str] = Field(default_factory=list)
    error: Optional[str] = Field(default=None)


class AnimationInput(BaseModel):
    """动画 Agent 的输入。"""
    context: AgentContext
    timeline: Timeline
    visual_config: dict[str, Any] = Field(default_factory=dict)


class AnimationOutput(BaseModel):
    """动画 Agent 的输出 — 带动画的时间线。"""
    agent_name: str = "animation_agent"
    decision: AgentDecision = AgentDecision.PASS
    timeline: Optional[Timeline] = Field(default=None)
    animation_plan: Optional[dict[str, Any]] = Field(
        default=None,
        description="动画编排计划：含 onscreen 和 transition 动画的实例化序列",
    )
    generated_mg_count: int = Field(default=0, description="LLM 本次生成的 MG 动画数量")
    error: Optional[str] = Field(default=None)


class AudioInput(BaseModel):
    """音效 Agent 的输入。"""
    context: AgentContext
    timeline: Timeline
    audio_config: dict[str, Any] = Field(default_factory=dict)


class AudioOutput(BaseModel):
    """音效 Agent 的输出 — 混音后的时间线。"""
    agent_name: str = "audio_agent"
    decision: AgentDecision = AgentDecision.PASS
    timeline: Optional[Timeline] = Field(default=None)
    audio_notes: list[str] = Field(default_factory=list, description="音频处理记录")
    error: Optional[str] = Field(default=None)
    audio_config_internal: dict[str, Any] = Field(default_factory=dict, exclude=True)


class QualityInput(BaseModel):
    """质检 Agent 的输入。"""
    context: AgentContext
    timeline: Timeline
    constraints: dict[str, Any] = Field(default_factory=dict)


class QualityIssue(BaseModel):
    """质检发现的问题。"""
    severity: str = Field(description="error / warning / info")
    category: str = Field(description="问题类别")
    message: str
    location: Optional[str] = Field(default=None)


class RequirementsInput(BaseModel):
    """需求 Agent 的输入。"""
    context: AgentContext
    topic: str = ""
    script_text: str = ""
    reference_materials: list[str] = Field(default_factory=list)
    persona_id: str = ""
    category_plugin_id: str = ""


class RequirementsOutput(BaseModel):
    """需求 Agent 的输出 — 创作方案。"""
    agent_name: str = "requirements_agent"
    decision: AgentDecision = AgentDecision.PASS
    creative_brief: dict[str, Any] = Field(default_factory=dict)
    animation_intents: list[AnimationIntent] = Field(
        default_factory=list,
        description="RequirementsAgent 识别的动画需求意图",
    )
    error: Optional[str] = Field(default=None)


class QualityOutput(BaseModel):
    """质检 Agent 的输出。"""
    agent_name: str = "quality_agent"
    decision: AgentDecision = AgentDecision.PASS
    passed: bool = Field(default=True, serialization_alias="pass")
    issues: list[QualityIssue] = Field(default_factory=list)
    fix_suggestions: list[str] = Field(default_factory=list)
    error: Optional[str] = Field(default=None)
    redo_agent: str = Field(default="", description="建议重做的 Agent，空表示不需要重做")


class AnimationIntent(BaseModel):
    """动画需求意图 — RequirementsAgent → StructureAgent → AnimationAgent。"""
    scene_index: Optional[int] = Field(default=None, description="目标场景索引，未确定时 null")
    type: str = Field(default="mg", description="动画类型: mg / text / logic")
    description: str = Field(default="", description="自然语言动画需求描述")
    text_content: str = Field(default="", description="动画中要显示的文字，用 | 分隔多个内容")
    style_hint: str = Field(default="", description="风格提示: tech_dark / minimal_clean / bold_vibrant / retro")
    suggested_template: str = Field(default="", description="建议的已有模板 ID，不确定则留空")
