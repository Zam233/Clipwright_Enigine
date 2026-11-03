"""需求 Agent（RequirementsAgent）— 创作方案对话 + 规划书翻译。

在 StructureAgent 之前执行，负责：
1. 与用户对话收集创作需求，逐步完善创作方案
2. 用户确认后调用 StructureAgent 生成场景规划
3. 将 StructureAgent 的纯场景列表翻译为用户友好的 Markdown 规划书
4. 管理用户确认/修改循环，最终启动 Pipeline
"""

from __future__ import annotations

from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AnimationIntent, RequirementsInput, RequirementsOutput
from clipwright.schema.requirements import (
    CreativeBrief,
    brief_field_names,
    brief_prompt_template,
)
from clipwright.services.llm import LLMService
from clipwright.config import logger

# PRD W4（2026-11-02）：提示词统一在 clipwright/prompts.py 维护；这里保留同名别名，
# 调用点一行都不用改。文案与原实现逐字一致（见 prompts.py 的迁移对照表）。
from clipwright.prompts import CREATIVE_BRIEF_SYSTEM as CREATIVE_BRIEF_SYSTEM, PLAN_TRANSLATE_SYSTEM as PLAN_TRANSLATE_SYSTEM, build_plan_translate_system  # noqa: E402


def _brief_default(name: str) -> Any:
    """取模型字段的默认值（缺省键补齐用，避免下游到处 ``.get(key, "")``）。"""
    field = CreativeBrief.model_fields.get(name)
    if field is None:
        return ""
    factory = getattr(field, "default_factory", None)
    if factory is not None:
        try:
            return factory()  # type: ignore[call-arg]
        except TypeError:
            return None
    return field.default



#: A-01：字段表从模型生成，杜绝"提示词 10 字段 / 模型 7 字段"的漂移。
CREATIVE_BRIEF_SYSTEM = CREATIVE_BRIEF_SYSTEM.replace(
    "{{BRIEF_TEMPLATE}}", brief_prompt_template()
)




class RequirementsAgent(BaseAgent[RequirementsInput, RequirementsOutput]):
    """需求 Agent：对话收集需求 → 生成方案 → 翻译规划书 → 确认启动。"""

    agent_name = "requirements_agent"

    def __init__(self) -> None:
        super().__init__()
        self._llm = LLMService()

    async def execute(
        self, input_data: RequirementsInput, context: AgentContext
    ) -> RequirementsOutput:
        logger.info("RequirementsAgent 开始 pipeline=%s", context.pipeline_id[:12])
        try:
            brief = await self._generate_brief(input_data, context)
            animation_intents = self._extract_animation_intents(brief)
            return RequirementsOutput(
                decision=AgentDecision.PASS,
                creative_brief=brief,
                animation_intents=animation_intents,
            )
        except Exception as e:
            logger.exception("RequirementsAgent 失败: %s", e)
            return self.build_error_output(str(e), RequirementsOutput)

    async def _generate_brief(
        self, input_data: RequirementsInput, context: AgentContext,
    ) -> dict[str, Any]:
        """根据用户输入生成创作方案。"""
        topic = input_data.topic or context.topic or ""
        script = input_data.script_text or ""
        references = input_data.reference_materials or []

        user_context = f"选题: {topic}\n"

        # F-04 落点：用户创作提示词优先于类型/Persona 默认风格
        from clipwright.services.creative_prompt import resolve_creative_prompt

        creative_prompt, creative_avoid = resolve_creative_prompt(context.extra_params)
        if creative_prompt:
            user_context += f"用户创作提示词（优先遵守）: {creative_prompt}\n"
        if creative_avoid:
            user_context += "用户明确不要的: " + "；".join(creative_avoid) + "\n"
        if script:
            user_context += f"文稿全文:\n{script}\n"
        if references:
            user_context += f"参考素材: {len(references)} 个文件\n"

        # 注入素材库概览（空素材库 → 无额外段落，零变化）。
        # 延迟导入避免模块级循环依赖（requirements_service 不反向依赖本模块）。
        from clipwright.services.requirements_service import _material_library_overview
        overview = _material_library_overview()
        if overview:
            user_context += f"\n素材库概览: {overview}"

        # R4：注入**已建索引**素材的内容摘要（"手上这段素材里有什么"）。
        # 没有索引时返回 ""，行为与接线前完全一致（零变化）。
        # 刻意只给摘要不塞全量索引：细节由 Agent 用 `content_query` 工具现查。
        # 项目 id 取 `extra_params.project_id`（管线注入），缺失时退化为公共素材库。
        from clipwright.services.content_query import index_context_text
        project_id = str((context.extra_params or {}).get("project_id") or "") or None
        content_context = index_context_text(project_id)
        if content_context:
            user_context += (
                "\n\n已建索引的素材内容（据此判断「素材里有没有」，细节可用 content_query 工具查）：\n"
                + content_context
            )

        system_prompt = CREATIVE_BRIEF_SYSTEM
        from clipwright.plugins.prompt_registry import PluginPromptRegistry
        plugin_prompts = PluginPromptRegistry.get_for_agent("requirements")
        if plugin_prompts:
            system_prompt += "\n\n## 插件能力扩展\n" + "\n\n".join(plugin_prompts)
        # V15：需求阶段先吃"这是什么片子"（身份层），再谈字段；无槽位时零变化
        from clipwright.services.type_prompts import compose as compose_type_prompt

        system_prompt = compose_type_prompt(
            system_prompt, getattr(context, "category_plugin_id", "") or "", ""
        )

        result = await self.llm_or_fallback(lambda: self._llm.structured_output(system_prompt=system_prompt, user_prompt=user_context, pipeline_id=context.pipeline_id), fallback={}, retries=2)
        if isinstance(result, dict) and "brief_draft" in result:
            return self._normalize_brief(result["brief_draft"])
        return {}

    @staticmethod
    def _normalize_brief(draft: Any) -> dict[str, Any]:
        """A-01：按模型字段收口——未知键记 warning 后丢弃，缺省键补空。

        旧实现把模型输出原样透传，``core_message`` 等字段在落库时被静默丢弃，
        出了事没人知道。这里显式登记"模型多给了什么"。
        """
        if not isinstance(draft, dict):
            return {}
        known = set(brief_field_names())
        unknown = [key for key in draft if key not in known]
        if unknown:
            logger.warning("CreativeBrief 收到未知字段（已丢弃）: %s", unknown)
        normalized = {key: value for key, value in draft.items() if key in known}
        for name in known:
            normalized.setdefault(name, _brief_default(name))
        return normalized

    @staticmethod
    def _extract_animation_intents(brief: dict[str, Any]) -> list[AnimationIntent]:
        """从 brief_draft 中提取 animation_intents 并转为 Pydantic 模型。"""
        raw = brief.get("animation_intents", [])
        if not isinstance(raw, list):
            return []
        intents: list[AnimationIntent] = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    intents.append(AnimationIntent(**item))
                except Exception:
                    pass
        return intents

    async def translate_scenes(
        self, scenes: list[dict], brief: dict[str, Any] | None = None,
        pipeline_id: str = "", web_context: str = "",
    ) -> dict[str, Any]:
        """将 StructureAgent 的场景列表翻译为用户友好的规划书。

        R8：场景上的 `material_refs`（真实镜头引用）**原样透传**到 `raw_scenes`——
        规划书是用户确认的对象，确认时看到的必须就是下游执行时用的那份引用。
        """
        import json

        scenes_json = json.dumps(scenes, ensure_ascii=False, indent=2)
        brief_json = json.dumps(brief, ensure_ascii=False) if brief else "{}"

        try:
            from clipwright.plugins.prompt_overrides import safe_builder
            system_prompt = safe_builder("build_plan_translate_system")(brief_json)
            from clipwright.plugins.prompt_registry import PluginPromptRegistry
            plugin_prompts = PluginPromptRegistry.get_for_agent("requirements")
            if plugin_prompts:
                system_prompt += "\n\n## 插件能力扩展\n" + "\n\n".join(plugin_prompts)
            if web_context:
                system_prompt += f"\n\n## 联网搜索参考\n{web_context}"

            result = await self.llm_or_fallback(lambda: self._llm.structured_output(system_prompt=system_prompt, user_prompt=f"结构 Agent 输出:\n{scenes_json}", pipeline_id=pipeline_id), fallback={}, retries=2)
            if isinstance(result, dict):
                if not result.get("markdown_content"):
                    result["markdown_content"] = self._default_markdown(scenes)
                if not result.get("total_duration_sec"):
                    result["total_duration_sec"] = sum(s.get("duration_sec", 0) for s in scenes)
                if not result.get("scene_count"):
                    result["scene_count"] = len(scenes)
                result["raw_scenes"] = scenes
                # R8：接地情况对用户可见（哪些场景用了真实镜头、哪些没料）
                grounded = self._material_summary(scenes)
                if grounded:
                    result["material_summary"] = grounded
                return result
        except Exception as e:
            logger.warning("规划书翻译失败: %s，使用降级", e)

        return {
            "summary": "基础规划书（自动生成）",
            "markdown_content": self._default_markdown(scenes),
            "total_duration_sec": sum(s.get("duration_sec", 0) for s in scenes),
            "scene_count": len(scenes),
            "raw_scenes": scenes,
            "material_summary": self._material_summary(scenes),
        }

    @staticmethod
    def _material_summary(scenes: list[dict]) -> str:
        """R8：规划书接地摘要（无索引/未接地时返回 ""，不产生空段落）。"""
        try:
            from clipwright.services.scene_grounding import summary_text

            return summary_text(scenes)
        except Exception:
            return ""

    @staticmethod
    def _default_markdown(scenes: list[dict]) -> str:
        lines = ["# 视频成片规划书\n"]
        lines.append("## 场景列表\n")
        lines.append("| # | 标题 | 时长 | 关键词 |")
        lines.append("|---|------|------|--------|")
        for i, s in enumerate(scenes, 1):
            lines.append(f"| {i} | {s.get('title', '')} | {s.get('duration_sec', 0)}s | {', '.join(s.get('keywords', [])[:3])} |")
        total = sum(s.get("duration_sec", 0) for s in scenes)
        lines.append(f"\n**总时长**: {total:.0f}s ({total/60:.1f}分钟)\n---\n")
        for i, s in enumerate(scenes, 1):
            lines.append(f"### 场景 {i}: {s.get('title', '')}")
            lines.append(f"- **时长**: {s.get('duration_sec', 0)}秒")
            lines.append(f"- **描述**: {s.get('description', '')}")
            # R8：把"这一场用哪些真实镜头"写进规划书——用户确认时就能看到引用，
            # 而不是等剪完了才发现用的不是他以为的那段素材。
            refs = s.get("material_refs") or []
            if refs:
                lines.append(f"- **素材引用**: {', '.join(str(r) for r in refs)}")
            elif s.get("material_gap"):
                lines.append(f"- **素材缺口**: {s['material_gap']}")
            lines.append("")
        return "\n".join(lines)

