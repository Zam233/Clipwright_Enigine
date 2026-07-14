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
from clipwright.schema.agent import AgentContext, AgentDecision, RequirementsInput, RequirementsOutput
from clipwright.services.llm import LLMService
from clipwright.config import logger


CREATIVE_BRIEF_SYSTEM = """你是一位专业的视频创作顾问。你的任务是与用户对话，收集创作需求，逐步完善创作方案。

## 对话策略
- 每次回复都要推进对话：回复用户 + 追问关键缺失信息
- 当收集到足够信息时，设置 is_ready=true
- 信息不足的标准：至少需要了解 主题/目标受众/风格方向

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
    "key_elements": ["\u5143\u7d201"],
    "special_requirements": []
  },
  "is_ready": false,
  "missing_info": ["\u8fd8\u672a\u4e86\u89e3\u7684\u4fe1\u606f"]
}

\u5f53 is_ready=true \u65f6\uff0cbrief_draft \u5fc5\u987b\u5b8c\u6574\u586b\u5199\u3002
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
            return RequirementsOutput(
                decision=AgentDecision.PASS,
                creative_brief=brief,
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
