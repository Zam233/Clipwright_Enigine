"""结构 Agent（StructureAgent）— 脚本骨架生成。

输入：选题/热点 + Persona 配置
输出：脚本骨架（段落结构、关键论点、分镜列表）

工具调用：LLM 可在生成过程中调用已注册的原子能力工具
（如 scene_detect、bpm_detect、semantic_match 等）。
"""

from __future__ import annotations

import json
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    StructureInput,
    StructureOutput,
)
from clipwright.schema.tool import ToolExecResult
from clipwright.services.llm import LLMService
from clipwright.tool.base import AgentToolkit
from clipwright.tool.registry import ToolRegistry

# 脚本骨架生成的系统提示词模板
SYSTEM_PROMPT_TPL = """你是一个{tone}风格的视频脚本创作者。

## Persona 特征
- 语调: {tone}
- 学术密度: {academic_density}
- 最长句长: {max_sentence_len} 字
- 剪辑节奏: {cut_profile}

## 输出格式
返回 JSON 数组，每个元素是一个分镜场景：
{{
    "title": "场景标题（如：破题/展开/深化/收束）",
    "description": "场景描述",
    "keywords": ["关键词1", "关键词2"],
    "duration_sec": 30
}}

要求：
1. 4-6 个场景
2. 每个场景的 duration_sec 加起来不超过 {max_duration}s
3. 遵循 {tone} 的语调风格
4. 禁止使用 forbidden_patterns
"""

# 工具调用引导
TOOL_PROMPT = """

## 可用工具
你可以在生成脚本骨架的过程中调用以下工具来获取参考数据：

- scene_detect: 检测视频中的场景切换点，可用于分析参考视频的剪辑节奏
- bpm_detect: 检测音频文件的 BPM，可用于匹配音乐节奏
- audio_extract: 从视频中提取音频，用于后续分析
- semantic_match: 用语义搜索匹配文字和视频素材

当你需要信息时，主动调用工具。工具调用不会终止你的推理过程，
你会收到工具执行结果并可以继续你的分析。
"""


class StructureAgent(BaseAgent[StructureInput, StructureOutput]):
    """结构 Agent：根据选题和 Persona 生成脚本骨架。

    支持 LLM tool calling:
    - 自动感知 ToolRegistry 中可用的工具
    - 在 LLM 推理过程中按需调用工具获取参考数据
    - 无 API key 时回退到占位实现
    """

    agent_name = "structure_agent"

    def __init__(self) -> None:
        super().__init__()
        self._llm = LLMService()

    async def execute(
        self, input_data: StructureInput, context: AgentContext
    ) -> StructureOutput:
        try:
            persona_config = input_data.persona_config
            identity = persona_config.get("identity", {})
            language = persona_config.get("language", {})
            rhythm = persona_config.get("rhythm", {})
            constraints = persona_config.get("constraints", {})

            tone = identity.get("tone", "neutral")
            academic_density = language.get("academic_density", 0.1)
            max_sentence_len = language.get("max_sentence_len", 30)
            cut_profile = rhythm.get("cut_profile", "even_flow")
            max_duration = constraints.get("max_duration_sec", 900)

            system_prompt = SYSTEM_PROMPT_TPL.format(
                tone=tone,
                academic_density=academic_density,
                max_sentence_len=max_sentence_len,
                cut_profile=cut_profile,
                max_duration=max_duration,
            )

            # 注入 Persona 的 Prompt 指令
            if input_data.persona_prompt:
                system_prompt += (
                    f"\n\n## Persona Prompt\n{input_data.persona_prompt}\n"
                )

            # 注入 RAG 检索上下文
            if input_data.rag_context:
                system_prompt += (
                    f"\n\n## 参考知识\n{input_data.rag_context}\n"
                )

            user_prompt = f"选题：{context.topic}\n请生成脚本骨架。"
            scenes: list[dict] = []

            from clipwright.config import settings
            has_api_key = bool(settings.llm_api_key)

            if has_api_key:
                # ── LLM 模式：支持工具调用 ──
                # 构建 AgentToolkit（只包含当前环境可用的工具）
                toolkit = AgentToolkit(
                    tool_names=ToolRegistry.list_available_names(),
                    fmt="anthropic" if settings.llm_provider == "anthropic" else "openai",
                )

                if toolkit.available:
                    system_prompt += TOOL_PROMPT
                    full_prompt = user_prompt

                    # 执行带工具调用的 LLM 推理
                    resp = await self._llm.with_tools(
                        system_prompt=system_prompt,
                        user_prompt=full_prompt,
                        tool_executor=self._tool_executor,
                        tools=toolkit.llm_tools,
                    )

                    if not resp.success:
                        # LLM 失败，回退
                        scenes = self._fallback_scenes(context.topic, tone)
                    else:
                        scenes = self._parse_scenes(resp.content)
                else:
                    # 无可用工具 → 纯文本模式
                    result = await self._llm.structured_output(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )
                    scenes = self._parse_structured_result(result, context.topic, tone)
            else:
                # ── 离线模式：回退到占位实现 ──
                scenes = self._fallback_scenes(context.topic, tone)

            return StructureOutput(
                decision=AgentDecision.PASS,
                script_skeleton={
                    "topic": context.topic,
                    "tone": tone,
                    "scene_count": len(scenes),
                },
                scenes=scenes,
            )

        except Exception as e:
            return self.build_error_output(str(e), StructureOutput)

    async def _tool_executor(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """执行工具调用（LLM with_tools 的回调）。"""
        result: ToolExecResult = await ToolRegistry.execute(tool_name, **tool_input)
        return result.model_dump(mode="json")

    def _parse_scenes(self, content: str) -> list[dict]:
        """从 LLM 响应文本中解析场景列表。"""
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(line for line in lines if not line.startswith("```"))
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "scenes" in parsed:
                return parsed["scenes"]
        except json.JSONDecodeError:
            pass
        return self._fallback_scenes("", "neutral")

    def _parse_structured_result(
        self, result: dict, topic: str, tone: str
    ) -> list[dict]:
        """从结构化输出中提取场景。"""
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "scenes" in result:
            return result["scenes"]
        if "content" in result:
            return self._parse_scenes(result["content"])
        return self._fallback_scenes(topic, tone)

    @staticmethod
    def _fallback_scenes(topic: str, tone: str) -> list[dict]:
        """无 LLM 或无工具时的占位场景列表。"""
        return [
            {
                "title": "破题",
                "description": f"以{tone}风格切入话题：{topic}",
                "keywords": [topic, "引入"],
                "duration_sec": 30,
            },
            {
                "title": "展开",
                "description": "核心论点展开",
                "keywords": ["分析", "论证"],
                "duration_sec": 120,
            },
            {
                "title": "深化",
                "description": "多角度深入分析",
                "keywords": ["深度", "视角"],
                "duration_sec": 90,
            },
            {
                "title": "收束",
                "description": "总结与观点输出",
                "keywords": ["总结", "观点"],
                "duration_sec": 60,
            },
        ]
