"""视频类型插件基类。

每个视频类型插件需要继承 BaseCategoryPlugin 并实现其接口。
Persona 配置层不直接调用原子能力，必须经过类型插件层翻译。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.schema.category_spec import (
    ParamRule,
    PromptBundle,
    PromptSlot,
    SignatureTrait,
    TypeDeclares,
)
from clipwright.schema.persona import ParameterLayer
from clipwright.schema.timeline import Clip, Timeline


class BaseCategoryPlugin(BasePlugin):
    """视频类型插件基类。"""

    # 插件标识
    plugin_id: str = ""
    display_name: str = ""
    description: str = ""

    def initialize(self) -> None:
        """默认初始化（注册到 CategoryRegistry）。子类可覆盖。"""
        pass

    def shutdown(self) -> None:
        """默认关闭。子类可覆盖。"""
        pass

    @abstractmethod
    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        """将 Persona 参数翻译为具体的剪辑参数。

        Persona 层的配置是 UP 主维度的（如 cut_density: high），
        此方法将其翻译为该视频类型的具体剪辑参数（如 min_shot_ms: 800）。
        """
        ...

    @abstractmethod
    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        """获取镜头级别剪辑参数。"""
        ...

    def post_process_timeline(self, timeline: Timeline) -> Timeline:
        """对生成的时间线做类型特定的后处理。

        基类提供默认实现（直接返回），子类可覆盖。
        """
        return timeline

    def validate_clip(self, clip: Clip) -> bool:
        """验证单个片段是否符合本类型的规范。"""
        return True

    def get_structure_template(self) -> str:
        """返回本视频类型的场景结构模板（轮 84：原死能力接线）。

        结构 Agent 会把模板注入 system prompt（"视频类型结构模板"段），
        引导场景划分遵循该骨架。基类返回空串（不注入，零行为变化）；
        子类可覆盖，返回多行文本：每行"段落名（时长占比）：内容要点"。
        """
        return ""

    def get_annotation_templates(self) -> list[str]:
        """返回本类型的节奏/标注模板名（轮 87：原死能力接线）。

        结构 Agent 将其作为"段落标注参考"注入 prompt，帮助 LLM 在场景中
        落位 hook/高潮/收束等节奏标注。基类返回空列表（不注入）。
        """
        return []

    def recommended_extra_params(self) -> dict[str, Any]:
        """返回本类型一键出片的推荐管线参数（轮 94）。

        api 层在启动管线时做「用户显式值优先」的合并：request.extra_params
        里没有的键才注入推荐值。基类返回空 dict（不注入，零行为变化）。
        常用键：enable_visual_llm / enable_color_match / auto_render。
        """
        return {}

    def get_mg_style_guidance(self) -> str:
        """返回本视频类型的 MG 动画风格指引。

        该指引会被注入 LLM MG 生成器（llm_mg）的 system prompt，
        使生成的动画符合视频类型的视觉气质。
        基类返回空字符串（不注入）；子类可覆盖，返回自然语言风格描述。
        """
        return ""

    # ── 类型规范（PRD Part I F1/F4，A-04）──────────────────────────────
    #
    # 新契约：类型用 ``get_prompt_bundle()`` 给 10 个槽位；旧钩子由基类适配成
    # ``structure`` / ``animation`` 槽位——**第三方插件一行不改也有提示词**。

    def get_prompt_bundle(self) -> PromptBundle:
        """返回提示词包。默认由三个旧钩子合成（适配器，F4）。"""
        slots: dict[str, PromptSlot] = {}
        identity: list[str] = []
        structure = _render_legacy(self.get_structure_template())
        if structure:
            annotations = _render_legacy(self.get_annotation_templates())
            if annotations:
                structure = f"{structure}\n{annotations}"
            slots["structure"] = PromptSlot(text=structure, priority=80)
        animation = _render_legacy(self.get_mg_style_guidance())
        if animation:
            slots["animation"] = PromptSlot(text=animation, priority=70)
        if identity:
            slots["genre"] = PromptSlot(text="\n".join(identity), priority=90)
        return PromptBundle(slots=slots)

    def get_param_rules(self) -> list[ParamRule]:
        """机器可读参数规则；基类为空（零行为变化）。"""
        return []

    def get_signature_traits(self) -> list[SignatureTrait]:
        """同一性的可测代理指标；基类为空。"""
        return []

    def get_declares(self) -> TypeDeclares:
        """类型声明块；基类把旧钩子/推荐参数转成声明（零行为变化）。"""
        return TypeDeclares(
            required_animation_markers=[],
            semantic_qa_default=None,
            search_hint="",
        )


def _render_legacy(value: Any) -> str:
    """把旧钩子的三种返回形态归一成可读文本（修 ``list[dict]`` 被 repr 的 bug）。

    - ``str``：原样（去空白）；
    - ``list[str]``：逐行 ``- item``；
    - ``list[dict]``：渲染成 ``- {role}（权重 {weight}）：{desc}``，取常见键名。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    lines.append(text if text.startswith("-") else f"- {text}")
                continue
            if isinstance(item, dict):
                lines.append(_render_legacy_dict(item))
        return "\n".join(line for line in lines if line)
    return str(value).strip()


def _render_legacy_dict(item: dict[str, Any]) -> str:
    role = item.get("role") or item.get("type") or item.get("name") or ""
    desc = item.get("desc") or item.get("description") or item.get("content") or ""
    weight = item.get("weight")
    position = item.get("position")
    style = item.get("style")
    parts: list[str] = []
    if role:
        parts.append(str(role))
    if weight is not None:
        parts.append(f"（权重 {weight}）")
    if position:
        parts.append(f"[{position}]")
    if style:
        parts.append(f"[{style}]")
    head = "".join(parts).strip()
    if head and desc:
        return f"- {head}：{desc}"
    if head:
        return f"- {head}"
    if desc:
        return f"- {desc}"
    return ""
