"""Persona 数据模型 — 四层复合数字人格体系。

对应架构文档中的 Persona 系统：
- 参数层 (Parameter Layer): 显式 YAML 规则
- 示例层 (Exemplar Layer): 带标注的视频片段参考
- 嵌入层 (Embedding Layer): 高维特征向量
- 模型层 (Model Layer): LoRA / Adapter 微调权重
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ──────────────────────────────────────────────
# 类型一：参数层
# ──────────────────────────────────────────────


class IdentityConfig(BaseModel):
    """创作者身份定义。"""
    model_config = {"populate_by_name": True}

    tone: str = Field(default="neutral", description="整体语调")
    position: Optional[list[float]] = Field(
        default=None, description="政治光谱二维坐标 [经济轴, 文化轴]"
    )
    # P0-5: 前端 UI 使用字符串定位描述（与 numeric position 并存，保证往返不丢失）
    positioning: Optional[str] = Field(default=None, description="定位描述（前端 UI 字符串）")
    class_perspective: Optional[str] = Field(default=None)
    knowledge_domains: list[str] = Field(default_factory=list)


class LanguageConfig(BaseModel):
    """语言风格参数。"""
    model_config = {"populate_by_name": True}

    academic_density: float = Field(default=0.1, ge=0, le=1)
    slang_ratio: float = Field(default=0.05, ge=0, le=1)
    max_sentence_len: int = Field(default=30, gt=0)
    variance_target: float = Field(default=0.6, ge=0, le=1)
    forbidden_patterns: list[str] = Field(default_factory=list)
    allowed_patterns: list[str] = Field(default_factory=list)


class RhythmConfig(BaseModel):
    """剪辑节奏参数。"""
    model_config = {"populate_by_name": True}

    cut_profile: str = Field(default="even_flow", description="命名节奏配置")
    surge_sections: list[str] = Field(default_factory=list)
    pause_sections: list[str] = Field(default_factory=list)
    base_shot_duration_ms: int = Field(default=5000, ge=100)
    cut_density_tier: str = Field(default="medium")
    # P0-5: 前端 UI 字段（保留往返）
    pause_frequency: Optional[float] = Field(default=None, ge=0)


class VisualConfig(BaseModel):
    """视觉风格参数。"""
    model_config = {"populate_by_name": True}

    palette: str = Field(default="neutral", description="配色方案名称")
    primary_color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")
    accent_color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")
    # P0-5: 前端 UI 字段（保留往返）
    background_color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")
    animation_style: Optional[str] = Field(default=None, description="动画风格描述（前端 UI 字符串）")
    animation_styles: dict[str, str] = Field(
        default_factory=dict,
        description="动画风格映射，如 {text_intro: typewriter_glitch}",
    )
    transition_weights: dict[str, float] = Field(
        default_factory=dict,
        description="转场类型权重，如 {hard_cut: 0.7, dissolve: 0.2}",
    )


class AudioConfig(BaseModel):
    """音频风格参数。"""
    model_config = {"populate_by_name": True}

    bgm_slots: dict[str, list[str]] = Field(
        default_factory=dict,
        description="BGM 槽位映射，如 {theory_backing: [kraftwerk_pool]}",
    )
    voice_model: Optional[str] = Field(default=None)
    target_loudness_lufs: float = Field(default=-16, ge=-30, le=-10)


class ConstraintsConfig(BaseModel):
    """硬约束参数。"""
    model_config = {"populate_by_name": True}

    max_duration_sec: int = Field(default=900, gt=0)
    min_duration_sec: int = Field(default=30, ge=0)
    require_source_citation: bool = Field(default=False)
    forbidden_topics: list[str] = Field(default_factory=list)


class ParameterLayer(BaseModel):
    """参数层 — 显式的、人可读的风格约束。"""
    layer_type: str = Field(default="parameter", alias="_layer_type")
    persona_id: str
    version: str = Field(default="1.0.0")
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    language: LanguageConfig = Field(default_factory=LanguageConfig)
    rhythm: RhythmConfig = Field(default_factory=RhythmConfig)
    visual: VisualConfig = Field(default_factory=VisualConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _map_frontend_shape(cls, data):
        """P0-5: 兼容前端 UI 字段名/单位/结构（修复保存静默重置参数的 B1）。

        前端形状 → 后端规范形状的映射：
        - max_sentence_length → max_sentence_len
        - sentence_variance_target → variance_target
        - base_shot_duration_sec(秒) → base_shot_duration_ms(毫秒)
        - color_palette{primary,accent,background} → palette/primary_color/accent_color/background_color
        - loudness_target_lufs → target_loudness_lufs
        - voice_clone_model_id → voice_model
        - source_citation_required → require_source_citation
        - bgm_slots 列表 → {"default": [...]}
        """
        if not isinstance(data, dict):
            return data
        d = dict(data)

        lang = d.get("language")
        if isinstance(lang, dict):
            l = dict(lang)
            if "max_sentence_length" in l and "max_sentence_len" not in l:
                l["max_sentence_len"] = l.pop("max_sentence_length")
            if "sentence_variance_target" in l and "variance_target" not in l:
                l["variance_target"] = l.pop("sentence_variance_target")
            d["language"] = l

        rhythm = d.get("rhythm")
        if isinstance(rhythm, dict):
            r = dict(rhythm)
            if "base_shot_duration_sec" in r and "base_shot_duration_ms" not in r:
                try:
                    r["base_shot_duration_ms"] = int(float(r["base_shot_duration_sec"]) * 1000)
                except (TypeError, ValueError):
                    pass
            d["rhythm"] = r

        visual = d.get("visual")
        if isinstance(visual, dict):
            v = dict(visual)
            cp = v.get("color_palette")
            if isinstance(cp, dict):
                if cp.get("primary") and "primary_color" not in v:
                    v["primary_color"] = cp["primary"]
                if cp.get("accent") and "accent_color" not in v:
                    v["accent_color"] = cp["accent"]
                if cp.get("background") and "background_color" not in v:
                    v["background_color"] = cp["background"]
                if "palette" not in v:
                    v["palette"] = "custom"
            d["visual"] = v

        audio = d.get("audio")
        if isinstance(audio, dict):
            a = dict(audio)
            if "loudness_target_lufs" in a and "target_loudness_lufs" not in a:
                a["target_loudness_lufs"] = a.pop("loudness_target_lufs")
            if "voice_clone_model_id" in a and "voice_model" not in a:
                a["voice_model"] = a.pop("voice_clone_model_id") or None
            if isinstance(a.get("bgm_slots"), list):
                a["bgm_slots"] = {"default": [str(x) for x in a["bgm_slots"] if x]}
            d["audio"] = a

        cons = d.get("constraints")
        if isinstance(cons, dict):
            c = dict(cons)
            if "source_citation_required" in c and "require_source_citation" not in c:
                c["require_source_citation"] = bool(c.pop("source_citation_required"))
            d["constraints"] = c

        return d


# ──────────────────────────────────────────────
# 类型二：示例层
# ──────────────────────────────────────────────


class ExemplarAnnotation(BaseModel):
    """单个示例片段的标注。"""
    what: str = Field(description="片段分类标签")
    cut_count: int = Field(default=0, ge=0)
    avg_shot_ms: int = Field(default=5000, gt=0)
    audio_treatment: str = Field(default="")
    text_overlay_style: str = Field(default="")
    note: str = Field(default="")
    auto_generated: bool = Field(default=False)


class Exemplar(BaseModel):
    """带标注的参考片段。"""
    exemplar_id: str
    source_video: str
    time_range: list[int] = Field(min_length=2, max_length=2)
    annotation: ExemplarAnnotation


class ExemplarLayer(BaseModel):
    """示例层 — 带标注的视频片段作为风格参考。"""
    layer_type: str = Field(default="exemplar", alias="_layer_type")
    persona_id: str
    version: str = Field(default="1.0.0")
    exemplars: list[Exemplar] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────
# 类型三：嵌入层
# ──────────────────────────────────────────────


class RhythmStats(BaseModel):
    shot_duration_distribution: str = Field(default="log_normal")
    shot_duration_mu_ms: float = Field(default=6000)
    shot_duration_sigma_ms: float = Field(default=2000)
    pacing_variance_per_minute: float = Field(default=0.3)


class VisualStats(BaseModel):
    dominant_color_cluster: list[list[int]] = Field(default_factory=list)
    saturation_median: float = Field(default=0.3)
    contrast_median: float = Field(default=0.7)
    motion_magnitude_median: float = Field(default=0.2)


class LanguageStats(BaseModel):
    avg_sentence_complexity: float = Field(default=0.5)
    rhetorical_question_ratio: float = Field(default=0.05)
    first_person_ratio: float = Field(default=0.05)
    second_person_ratio: float = Field(default=0.05)
    imperative_ratio: float = Field(default=0.02)


class ArgumentStats(BaseModel):
    hook_to_body_ratio: float = Field(default=0.1)
    theory_density_peak_position: float = Field(default=0.5)
    real_world_return_position: float = Field(default=0.75)


class EmbeddingLayer(BaseModel):
    """嵌入层 — 模型自动提取的隐性特征向量。"""
    layer_type: str = Field(default="embedding", alias="_layer_type")
    persona_id: str
    version: str = Field(default="1.0.0")
    source_videos_count: int = Field(default=0)
    extraction_model: str = Field(default="clipwright-embed-v1")

    rhythm_embedding_path: Optional[str] = Field(default=None)
    rhythm_stats: RhythmStats = Field(default_factory=RhythmStats)

    visual_embedding_path: Optional[str] = Field(default=None)
    visual_stats: VisualStats = Field(default_factory=VisualStats)

    language_embedding_path: Optional[str] = Field(default=None)
    language_stats: LanguageStats = Field(default_factory=LanguageStats)

    argument_embedding_path: Optional[str] = Field(default=None)
    argument_stats: ArgumentStats = Field(default_factory=ArgumentStats)

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────
# 类型四：模型层
# ──────────────────────────────────────────────


class ModelWeights(BaseModel):
    """模型权重引用。"""
    structure_agent_lora: Optional[str] = Field(default=None)
    edit_agent_adapter: Optional[str] = Field(default=None)
    animation_agent_lora: Optional[str] = Field(default=None)
    voice_clone: Optional[str] = Field(default=None)
    material_selection_adapter: Optional[str] = Field(default=None)


class ModelLayer(BaseModel):
    """模型层 — 微调过的模型权重。"""
    layer_type: str = Field(default="model", alias="_layer_type")
    persona_id: str
    version: str = Field(default="1.0.0")

    base_models: dict[str, str] = Field(
        default_factory=lambda: {
            "llm": "gpt-4o",
            "vision": "clip-vit-l-14",
            "tts": "gpt-sovits",
        }
    )
    weights: ModelWeights = Field(default_factory=ModelWeights)
    training_samples: dict[str, int] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────
# Persona 复合体
# ──────────────────────────────────────────────


class PersonaOverride(BaseModel):
    """Persona 继承时的覆盖声明。"""
    parameter: Optional[dict] = Field(default=None)
    exemplar: Optional[dict] = Field(default=None)


class PersonaComposition(BaseModel):
    """Persona 组合声明。"""
    persona: str
    layers: list[str]
    weight: float = Field(default=0.5, ge=0, le=1)


class KnowledgeDoc(BaseModel):
    """知识库文档条目 — Persona 的 RAG 知识源。"""
    id: str = Field(default="", description="文档唯一 ID")
    title: str = Field(default="", description="文档标题/文件名")
    content: str = Field(default="", description="文档正文内容")
    source: str = Field(default="", description="来源（本地文件/上传/自动生成）")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PersonaManifest(BaseModel):
    """Persona 复合体 — 包含三部分（YAML / Prompt / RAG）+ 四层引用。

    每个 Persona 由三大组成部分：
    - YAML（parameter）：结构化参数配置，定义风格约束
    - Prompt（prompt）：系统指令文本，定义 AI 行为和角色的引导
    - RAG（knowledge）：知识库文档集合，作为引用素材和风格参考
    """
    persona_id: str
    persona_name: str = Field(default="")
    version: str = Field(default="1.0.0")
    description: str = Field(default="")
    # P3-3B: 创建者账号 ID（jwt 模式）；空串 = 遗留/公共 persona（off/token 模式）
    owner_id: str = Field(default="")

    # 继承与组合
    inherits: Optional[str] = Field(default=None)
    overrides: Optional[PersonaOverride] = Field(default=None)
    compose: list[PersonaComposition] = Field(default_factory=list)

    # 三大组成部分
    parameter: Optional[ParameterLayer] = Field(
        default=None, description="YAML 参数层：结构化风格参数"
    )
    prompt: Optional[str] = Field(
        default=None, description="Prompt 指令：系统提示词/行为引导文本"
    )
    vision_prompt: Optional[str] = Field(
        default=None, description="视觉需求提示词：贯穿结构/动画/MG 生成的画面风格与视觉约束"
    )
    knowledge: Optional[list[KnowledgeDoc]] = Field(
        default=None, description="RAG 知识库：参考文档集合"
    )

    # 四层数据（内联或引用，用于深度定制）
    exemplar: Optional[ExemplarLayer] = Field(default=None)
    embedding: Optional[EmbeddingLayer] = Field(default=None)
    model: Optional[ModelLayer] = Field(default=None)

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _map_frontend_shape(cls, data):
        """P0-5: parent_id（前端字段名）→ inherits（后端规范字段名）。"""
        if isinstance(data, dict):
            d = dict(data)
            if "parent_id" in d and "inherits" not in d:
                d["inherits"] = d.pop("parent_id") or None
            return d
        return data
