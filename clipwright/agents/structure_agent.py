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
from clipwright.config import logger

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

            # 判断模式
            video_mode = context.extra_params.get("video_mode", "voiceover")
            script_text = context.extra_params.get("script_text", "")
            audio_duration = context.extra_params.get("audio_duration_sec", 0)

            user_prompt = f"选题：{context.topic}\n请生成脚本骨架。"

            anim_guide = (
                "## 文字动画标记（作用于文字轨 clip 的入场/出场）\n"
                "分析场景内容的逻辑关系，选择合适的动画类型标记在 description 中。\n"
                "format: [文字动画]动画名：要显示的文字内容\n"
                "示例: description: \"以中立风格引入话题 [文字动画]淡入：人工智能正在改变世界\"\n\n"
            )

            # 动态获取可用文字动画
            from clipwright.animation.catalog import AnimationCatalog
            text_anims = AnimationCatalog.get_text_animations()
            for a in text_anims:
                anim_guide += f"  [文字动画]{a['name']} — {a.get('desc', '')}\n"

            anim_guide += (
                "\n## 逻辑动画标记（独立创建动画轨，展示逻辑关系）\n"
            )
            logic_anims = AnimationCatalog.get_logic_animations()
            for a in logic_anims:
                anim_guide += f"  [逻辑动画]{a['name']} — {a.get('desc', '')}\n"
            anim_guide += (
                "\n注意：同一场景 description 最多只应包含一个动画标记，避免冲突。\n"
            )

            if video_mode == "visual":
                max_chars = 3000
                truncated = script_text[:max_chars] + ("..." if len(script_text) > max_chars else "")
                lines = [l.strip() for l in truncated.split('\n') if l.strip()]
                scene_count = len(lines)
                user_prompt += (
                    f"\n\n## 场景列表（每行一个场景描述）\n{truncated}\n\n"
                    f"## 要求\n"
                    f"1. 总时长约 {audio_duration:.0f}s，{scene_count} 个场景\n"
                    f"2. 每个场景需包含: title, description, duration_sec, keywords\n"
                    f"3. duration_sec 按重要性分配，总和接近 {audio_duration:.0f}s\n"
                    f"4. 场景数量接近 {scene_count}\n"
                    f"5. 在 description 中用 [动画] 和 [转场] 标记\n"
                    f"6. keywords 具体可搜索\n\n"
                    f"{anim_guide}"
                )
            elif script_text:
                max_chars = 2000
                truncated = script_text[:max_chars] + ("..." if len(script_text) > max_chars else "")
                user_prompt += (
                    f"\n\n## 完整文稿\n{truncated}\n\n"
                    f"## 要求\n"
                    f"1. 总时长约 {audio_duration:.0f}s\n"
                    f"2. 每个场景: title, description, duration_sec, keywords\n"
                    f"3. 分析每个场景的逻辑关系，选择合适的动画类型标记\n"
                    f"4. 场景总时长接近 {audio_duration:.0f}s\n"
                    f"5. 文稿长则多场景，短则少场景\n\n"
                    f"{anim_guide}"
                )

            scenes: list[dict] = []

            from clipwright.config import settings
            has_api_key = bool(settings.llm_api_key)

            logger.info("StructureAgent: 选题=%s, tone=%s, has_api_key=%s",
                        context.topic, tone, has_api_key)

            if has_api_key:
                # ── LLM 模式：使用 structured_output 获取 JSON 结果 ──
                # StructureAgent 不需要工具调用，structured_output 强制 LLM 返回 JSON
                result = await self._llm.structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                scenes = self._parse_structured_result(result, context.topic, tone)
            else:
                # ── 离线模式：回退到占位实现 ──
                logger.info("StructureAgent: 无 API key，使用 fallback 场景")
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
        logger.debug("StructureAgent 工具回调: %s → %s",
                     tool_name, json.dumps(tool_input, ensure_ascii=False)[:300])
        result: ToolExecResult = await ToolRegistry.execute(tool_name, **tool_input)
        logger.debug("StructureAgent 工具回调结果 %s: status=%s, output=%.200s",
                     tool_name, result.status,
                     json.dumps(result.output, ensure_ascii=False)[:200] if result.output else str(result.error))
        return result.model_dump(mode="json")

    def _parse_scenes(self, content: str) -> list[dict]:
        """从 LLM 响应文本中解析场景列表。"""
        # 清理内容：去除 BOM、零宽字符
        content = content.strip().lstrip('\ufeff').lstrip('\u200b')
        # 去除 markdown 代码块标记
        if content.startswith("```"):
            lines = content.splitlines()
            if len(lines) >= 2:
                content = "\n".join(lines[1:-1]).strip()
        content = content.strip()
        if not content:
            logger.warning("StructureAgent: LLM 返回空内容")
            return self._fallback_scenes("", "neutral")
        # 尝试直接解析 JSON
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                logger.info("StructureAgent: 成功解析 %d 个场景", len(parsed))
                return parsed
            if isinstance(parsed, dict) and "scenes" in parsed:
                logger.info("StructureAgent: 从 dict 解析 %d 个场景", len(parsed["scenes"]))
                return parsed["scenes"]
        except json.JSONDecodeError as je:
            logger.warning("StructureAgent: JSON 解析失败: %s", je)
            logger.warning("StructureAgent: raw[%d] first=%.300s", len(content), content[:300])
            logger.warning("StructureAgent: raw[%d] last=%.200s", len(content), content[-200:])
            # 尝试找第一个不合法的字符位置
            for idx, c in enumerate(content[:500]):
                if ord(c) < 32 and c not in '\n\r\t':
                    logger.warning("StructureAgent: 非法字符 pos=%d ord=%d repr=%s", idx, ord(c), repr(c))
                    break
        # 搜索 JSON 数组
        import re
        arr_match = re.search(r'\[\s*\{[^}]*\}\s*\]', content, re.DOTALL)
        if arr_match:
            try:
                parsed = json.loads(arr_match.group())
                if isinstance(parsed, list):
                    logger.info("StructureAgent: 从文本提取 %d 个场景", len(parsed))
                    return parsed
            except json.JSONDecodeError:
                pass
        # 搜索 JSON 对象中的 scenes 数组
        obj_match = re.search(r'"scenes"\s*:\s*\[.*?\]', content, re.DOTALL)
        if obj_match:
            try:
                partial = '{' + obj_match.group() + '}'
                parsed = json.loads(partial)
                if "scenes" in parsed:
                    logger.info("StructureAgent: 从 scenes 字段提取 %d 个场景", len(parsed["scenes"]))
                    return parsed["scenes"]
            except json.JSONDecodeError:
                pass
        logger.warning("StructureAgent: 场景解析完全失败，使用 fallback")
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
