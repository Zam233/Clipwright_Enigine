"""Agent 数据模型 — Agent 输入/输出契约。

每个 Agent 是一个 LangGraph 节点，输入和输出都有严格的类型定义。
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from .timeline import Timeline


class AgentDecision(str, Enum):
    """Agent 执行后的决策结果。"""
    PASS = "pass"
    FAIL = "fail"
    RETRY = "retry"
    SKIP = "skip"


# ── F9 失败回报契约（PRD v0.2 §F9）─────────────────────────────────────────
#
# 过去每个 Agent 只回一句 `error: Optional[str]`，而且大量降级（LLM 超时走
# fallback、素材无结果用占位、类型 transform 失败保留原时间线）是
# decision=PASS + error=None —— 编排层与质检都看不到，占位图于是被当成正常成片。
# 这里把「失败」与「降级」变成结构化契约。

FAILURE_CATEGORIES: tuple[str, ...] = (
    "resource_missing",      # 素材源没结果 / 资产缺失
    "input_insufficient",    # 上游给的输入不足（缺 material_refs 等）
    "model_incapable",       # 模型能力不足（反复解析失败、产出不合 schema）
    "timeout",               # 超时
    "external_unavailable",  # 外部服务不可用（素材源/TTS/渲染）
    "constraint_conflict",   # Persona/简报/类型约束互相冲突
    "internal_defect",       # 自身代码缺陷
    "unknown",
)

RETRY_MODES: tuple[str, ...] = (
    "retry_same",       # 同参重试有意义
    "needs_upstream",   # 需要上游补数据
    "needs_human",      # 需要人工介入
    "permanent",        # 不可修复（如资源确实不存在）
)


class Degradation(BaseModel):
    """一次「退而求其次」的登记。

    与 failure 的区别：failure 表示这一步没能完成；degradation 表示这一步
    **完成了，但产物打了折扣**。后者更危险——它伪装成成功。
    """

    kind: str = Field(description="降级类型: llm_timeout_reasoning / material_placeholder / "
                                 "tts_default_voice / plugin_transform_failed / rag_unavailable ...")
    detail: str = Field(default="", description="人话说明（发生了什么）")
    scope: str = Field(default="", description="影响范围: scene:3 / clip:c_12 / track:video_main / global")
    impact: str = Field(default="", description="对成品的影响（该段将出现占位图…）")


class FailureReport(BaseModel):
    """一次失败的完整交代（向上传给编排层/调度中枢）。"""

    category: str = Field(default="unknown", description="归因类别，取值见 FAILURE_CATEGORIES")
    blocking_point: str = Field(default="", description="阻塞点（人话）：第 3 段无可用素材")
    location: dict[str, Any] = Field(default_factory=dict,
                                     description="{scene_id, clip_id, track, ...} 结构定位")
    attempts: list[dict[str, Any]] = Field(default_factory=list,
                                           description="已尝试 [{n, changed}]")
    retryable: str = Field(default="retry_same", description="可重试性，取值见 RETRY_MODES")
    needs: list[dict[str, str]] = Field(
        default_factory=list,
        description="请求上游提供什么 [{from_stage, field, why}] —— 让归因从「推断」变成「申报」",
    )
    evidence: list[dict[str, Any]] = Field(default_factory=list,
                                           description="证据引用 [{kind, ref}]")


class AgentContext(BaseModel):
    """Agent 执行的上下文信息。"""
    pipeline_id: str
    persona_id: str
    category_plugin_id: str
    topic: str
    extra_params: dict[str, Any] = Field(default_factory=dict)


# 契约自检计数：FAIL + error 却没有 FailureReport 的次数（生产观测用）
_UNKNOWN_FAILURE_TOTAL = 0


def _contract_strict() -> bool:
    """严格模式开关（测试用）：缺 FailureReport 直接抛错而非补 unknown。"""
    return (os.getenv("CLIPWRIGHT_AGENT_CONTRACT_STRICT", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def agent_contract_stats() -> dict[str, int]:
    """F9 契约可观测：缺省的 FailureReport 计数（>0 说明某条失败路径没接线）。"""
    return {"unknown_failure_total": _UNKNOWN_FAILURE_TOTAL}


class AgentOutputBase(BaseModel):
    """所有 Agent 输出的公共基座（F9）。

    约定（刻意区分两种 FAIL）：
    - **执行失败**：``decision=FAIL`` 且 ``error`` 非空 —— 必须给 ``failure``。
      宽松模式（默认）自动补 ``category=unknown`` 并计数，严格模式直接抛错，
      避免新 Agent 忘接线。
    - **判定失败**：``decision=FAIL`` 且 ``error`` 为空 —— 质检这类「片子不合格
      但我自己没出错」的判定，``failure`` 保持 None（它不是执行故障，是症状）。
    """

    decision: AgentDecision = AgentDecision.PASS
    error: Optional[str] = Field(default=None, description="人话错误摘要（兼容旧消费方）")
    failure: Optional[FailureReport] = Field(
        default=None, description="结构化失败回报：归因/阻塞点/已尝试/可重试性/向上需求/证据",
    )
    degradations: list[Degradation] = Field(
        default_factory=list, description="本次执行中「完成了但打了折扣」的全部登记",
    )

    @model_validator(mode="after")
    def _require_failure_on_execution_fail(self) -> "AgentOutputBase":
        if self.decision != AgentDecision.FAIL or not self.error:
            return self
        if self.failure is not None:
            return self
        if _contract_strict():
            raise ValueError(
                "decision=FAIL 且 error 非空时必须提供 FailureReport（F9 契约）；"
                "若这是「判定失败」而非「执行失败」，请不要设置 error。"
            )
        global _UNKNOWN_FAILURE_TOTAL
        _UNKNOWN_FAILURE_TOTAL += 1
        self.failure = FailureReport(
            category="unknown",
            blocking_point=str(self.error)[:200],
        )
        return self


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
    creative_brief: Optional[dict[str, Any]] = Field(
        default=None, description="用户审阅确认的创作简报（来自需求对话）"
    )
    production_plan: Optional[dict[str, Any]] = Field(
        default=None, description="用户确认的制作规划书（含 markdown/scenes）"
    )
    vision_prompt: Optional[str] = Field(
        default=None, description="Persona 的视觉需求 Prompt，注入 LLM system prompt"
    )


class StructureOutput(AgentOutputBase):
    """结构 Agent 的输出 — 脚本骨架。"""
    agent_name: str = "structure_agent"
    script_skeleton: dict[str, Any] = Field(
        default_factory=dict,
        description="脚本骨架，包含段落结构、关键论点",
    )
    scenes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="分镜列表 [{title, description, keywords, ...}]",
    )


class MaterialInput(BaseModel):
    """素材 Agent 的输入。"""
    context: AgentContext
    script_skeleton: dict[str, Any]
    persona_config: dict[str, Any] = Field(default_factory=dict)
    material_plugin_config: dict[str, Any] = Field(
        default_factory=dict,
        description="素材库插件的配置（含 enable_visual_llm, visual_llm_frame_count 等）",
    )
    creative_brief: Optional[dict[str, Any]] = Field(
        default=None, description="用户审阅确认的创作简报（素材偏好/类型/来源）"
    )
    production_plan: Optional[dict[str, Any]] = Field(
        default=None, description="用户确认的制作规划书"
    )


class MaterialOutput(AgentOutputBase):
    """素材 Agent 的输出 — 候选素材集合。"""
    agent_name: str = "material_agent"
    candidate_clips: list[dict[str, Any]] = Field(
        default_factory=list,
        description="候选素材列表 [{asset_id, scene_index, score, ...}]",
    )
    material_notes: list[str] = Field(default_factory=list, exclude=True)


class EditInput(BaseModel):
    """剪辑 Agent 的输入。"""
    context: AgentContext
    script_skeleton: dict[str, Any]
    candidate_clips: list[dict[str, Any]]
    creative_brief: Optional[dict[str, Any]] = Field(
        default=None, description="用户审阅确认的创作简报"
    )
    production_plan: Optional[dict[str, Any]] = Field(
        default=None, description="用户确认的制作规划书（时长/结构）"
    )


class EditOutput(AgentOutputBase):
    """剪辑 Agent 的输出 — 粗剪时间线。"""
    agent_name: str = "edit_agent"
    timeline: Optional[Timeline] = Field(default=None)
    edit_notes: list[str] = Field(default_factory=list)


class AnimationInput(BaseModel):
    """动画 Agent 的输入。"""
    context: AgentContext
    timeline: Timeline
    visual_config: dict[str, Any] = Field(default_factory=dict)
    persona_prompt: Optional[str] = Field(
        default=None, description="Persona 的 Prompt 指令，注入 LLM system prompt"
    )
    vision_prompt: Optional[str] = Field(
        default=None, description="Persona 的视觉需求 Prompt，注入 LLM system prompt"
    )
    creative_brief: Optional[dict[str, Any]] = Field(
        default=None, description="用户审阅确认的创作简报（动画风格/字体/图标）"
    )
    production_plan: Optional[dict[str, Any]] = Field(
        default=None, description="用户确认的制作规划书"
    )
    image_assets: list[dict[str, Any]] = Field(
        default_factory=list,
        description="素材库图片资产 [{path, tags, description}]，Agent 解析语义供 LLM 选图",
    )


class AnimationOutput(AgentOutputBase):
    """动画 Agent 的输出 — 带动画的时间线。"""
    agent_name: str = "animation_agent"
    timeline: Optional[Timeline] = Field(default=None)
    animation_plan: Optional[dict[str, Any]] = Field(
        default=None,
        description="动画编排计划：含 onscreen 和 transition 动画的实例化序列",
    )
    generated_mg_count: int = Field(default=0, description="LLM 本次生成的 MG 动画数量")


class AudioInput(BaseModel):
    """音效 Agent 的输入。"""
    context: AgentContext
    timeline: Timeline
    audio_config: dict[str, Any] = Field(default_factory=dict)
    creative_brief: Optional[dict[str, Any]] = Field(
        default=None, description="用户审阅确认的创作简报（BGM 需求）"
    )
    production_plan: Optional[dict[str, Any]] = Field(
        default=None, description="用户确认的制作规划书"
    )


class AudioOutput(AgentOutputBase):
    """音效 Agent 的输出 — 混音后的时间线。"""
    agent_name: str = "audio_agent"
    timeline: Optional[Timeline] = Field(default=None)
    audio_notes: list[str] = Field(default_factory=list, description="音频处理记录")
    audio_config_internal: dict[str, Any] = Field(default_factory=dict, exclude=True)


class QualityInput(BaseModel):
    """质检 Agent 的输入。"""
    context: AgentContext
    timeline: Timeline
    constraints: dict[str, Any] = Field(default_factory=dict)
    creative_brief: Optional[dict[str, Any]] = Field(
        default=None, description="用户审阅确认的创作简报（特殊要求/约束）"
    )
    production_plan: Optional[dict[str, Any]] = Field(
        default=None, description="用户确认的制作规划书"
    )


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


class RequirementsOutput(AgentOutputBase):
    """需求 Agent 的输出 — 创作方案。"""
    agent_name: str = "requirements_agent"
    creative_brief: dict[str, Any] = Field(default_factory=dict)
    animation_intents: list[AnimationIntent] = Field(
        default_factory=list,
        description="RequirementsAgent 识别的动画需求意图",
    )


class QualityOutput(AgentOutputBase):
    """质检 Agent 的输出。"""
    model_config = ConfigDict(populate_by_name=True)  # C7: 允许按字段名构造

    agent_name: str = "quality_agent"
    # 输入兼容 passed/pass 两种键（历史别名），输出统一序列化为 "passed"——
    # 原 serialization_alias="pass" 使读取 result["passed"] 的消费方拿不到值
    passed: bool = Field(default=True, validation_alias=AliasChoices("passed", "pass"))
    issues: list[QualityIssue] = Field(default_factory=list)
    fix_suggestions: list[str] = Field(default_factory=list)
    redo_agent: str = Field(default="", description="建议重做的 Agent，空表示不需要重做")
    # 轮 84（评分闭环）：0-100 成片质量分——确定性权重（error -25 / warning -8 /
    # info -2）由 issue 列表推导；passed 判定不变（分数仅供审阅与可选门槛）
    score: int = Field(default=100, ge=0, le=100, description="成片质量分（0-100）")


class PluginAgentInput(BaseModel):
    """插件 Agent 的通用输入（SA-3）。

    data 为上游 Agent 产物合并后的共享数据 dict（与 _merge_agent_result
    语义一致，timeline 为其中的时间线快捷引用）；插件按需读取。
    """
    context: AgentContext
    data: dict[str, Any] = Field(default_factory=dict)
    timeline: Optional[dict[str, Any]] = Field(default=None)


class PluginAgentOutput(AgentOutputBase):
    """插件 Agent 的通用输出（SA-3）。

    payload 中的业务键会被 _merge_agent_result 合并进共享数据供下游
    （含 timeline 键——插件可整体替换/加工时间线）。
    """
    agent_name: str = "plugin_agent"
    payload: dict[str, Any] = Field(default_factory=dict)


class AnimationIntent(BaseModel):
    """动画需求意图 — RequirementsAgent → StructureAgent → AnimationAgent。"""
    scene_index: Optional[int] = Field(default=None, description="目标场景索引，未确定时 null")
    type: str = Field(default="mg", description="动画类型: mg / text / logic")
    description: str = Field(default="", description="自然语言动画需求描述")
    text_content: str = Field(default="", description="动画中要显示的文字，用 | 分隔多个内容")
    style_hint: str = Field(default="", description="风格提示: tech_dark / minimal_clean / bold_vibrant / retro")
    suggested_template: str = Field(default="", description="建议的已有模板 ID，不确定则留空")
