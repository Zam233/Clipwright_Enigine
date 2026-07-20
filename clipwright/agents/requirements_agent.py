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
from clipwright.services.llm import LLMService
from clipwright.config import logger


CREATIVE_BRIEF_SYSTEM = """你是一位专业的视频创作顾问。你的任务是与用户对话，收集创作需求，逐步完善创作方案。

## 对话策略
- 每次回复都要推进对话：回复用户 + 追问关键缺失信息
- 当收集到足够信息时，设置 is_ready=true
- 信息不足的标准：至少需要了解 主题/目标受众/风格方向

## 动画需求识别
如果用户的创作需求中提到了视觉效果、数据展示、对比、流程、图表等信息呈现需求，
在 brief_draft 中设置 animation_intents 数组，每个元素描述一个场景的动画需求。

animation_intents 格式:
[
  {
    "scene_index": null,
    "type": "mg",
    "description": "自然语言描述该动画应呈现的效果",
    "text_content": "动画中要显示的文字内容，多段内容用 | 分隔",
    "style_hint": "样式提示: tech_dark / minimal_clean / bold_vibrant / retro",
    "suggested_template": "最接近的现有模板 ID，不确定则留空"
  }
]

类型说明:
- type="mg": 动态图形动画（数据图表、标题揭示、进度条、对比图等）
- type="text": 文字入场动画（打字机、淡入、弹跳等）
- type="logic": 逻辑关系图解（箭头、流程图、因果关系等）

只在用户明确需要视觉信息呈现（图表/对比/数据可视化/标题动画）时填写 animation_intents。

## 输出格式（纯 JSON）
{
  "reply": "对用户的自然语言回复",
  "brief_draft": {
    "title": "视频标题",
    "overview": "概述",
    "target_audience": "目标受众",
    "core_message": "核心信息",
    "style_direction": "风格方向",
    "structure_suggestion": "结构建议",
    "duration_estimate": "预估时长",
    "key_elements": ["元素1"],
    "special_requirements": [],
    "animation_intents": []
  },
  "is_ready": false,
  "missing_info": ["还未了解的信息"]
}

当 is_ready=true 时，brief_draft 必须完整填写。
"""


PLAN_TRANSLATE_SYSTEM = """你是一位专业的视频创作顾问。请将结构 Agent 生成的场景规划翻译为用户友好的 Markdown 规划书。

## 输出格式（纯 JSON）
{
  "summary": "规划书总体摘要",
  "sections": [
    {"title": "段落标题", "description": "段落描述", "scenes": [1, 2, 3]}
  ],
  "markdown_content": "完整的 Markdown 格式规划书\n- 包含场景表格\n- 每个场景的详细描述\n- 总时长统计",
  "total_duration_sec": 300,
  "scene_count": 5
}
"""


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
        if script:
            user_context += f"文稿预览: {script[:500]}\n"
        if references:
            user_context += f"参考素材: {len(references)} 个文件\n"

        result = await self._llm.structured_output(
            system_prompt=CREATIVE_BRIEF_SYSTEM,
            user_prompt=user_context,
            pipeline_id=context.pipeline_id,
        )
        if isinstance(result, dict) and "brief_draft" in result:
            return result["brief_draft"]
        return {}

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
        pipeline_id: str = "",
    ) -> dict[str, Any]:
        """将 StructureAgent 的场景列表翻译为用户友好的规划书。"""
        import json

        scenes_json = json.dumps(scenes, ensure_ascii=False, indent=2)
        brief_json = json.dumps(brief, ensure_ascii=False) if brief else "{}"

        try:
            result = await self._llm.structured_output(
                system_prompt=f"{PLAN_TRANSLATE_SYSTEM}\n\n参考方案:\n{brief_json}",
                user_prompt=f"结构 Agent 输出:\n{scenes_json}",
                pipeline_id=pipeline_id,
            )
            if isinstance(result, dict):
                if not result.get("markdown_content"):
                    result["markdown_content"] = self._default_markdown(scenes)
                if not result.get("total_duration_sec"):
                    result["total_duration_sec"] = sum(s.get("duration_sec", 0) for s in scenes)
                if not result.get("scene_count"):
                    result["scene_count"] = len(scenes)
                result["raw_scenes"] = scenes
                return result
        except Exception as e:
            logger.warning("规划书翻译失败: %s，使用降级", e)

        return {
            "summary": "基础规划书（自动生成）",
            "markdown_content": self._default_markdown(scenes),
            "total_duration_sec": sum(s.get("duration_sec", 0) for s in scenes),
            "scene_count": len(scenes),
            "raw_scenes": scenes,
        }

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
            lines.append(f"- **描述**: {s.get('description', '')}\n")
        return "\n".join(lines)
