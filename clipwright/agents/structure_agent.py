"""结构 Agent（StructureAgent）— 脚本骨架生成 + 场景验证 + 超时保护。"""

from __future__ import annotations

import asyncio
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

SYSTEM_PROMPT_TPL = """你是一个{tone}风格的视频脚本创作者。

## Persona 特征
- 语调: {tone}
- 学术密度: {academic_density}
- 最长句长: {max_sentence_len} 字
- 剪辑节奏: {cut_profile}

## 输出格式
返回 JSON 数组，每个元素是一个分镜场景：
{{
    "title": "场景标题",
    "description": "场景描述",
    "keywords": ["关键词1", "关键词2"],
    "duration_sec": 30
}}

要求：
1. 4-6 个场景
2. 每个场景的 duration_sec 加起来不超过 {max_duration}s
3. 遵循 {tone} 的语调风格
4. 禁止使用 forbidden_patterns
5. **每个场景必须有 title(非空)、duration_sec(>0)、description(非空)、keywords(数组)**
"""


TOOL_PROMPT = """
## 可用工具
你可以在生成脚本骨架的过程中调用以下工具来获取参考数据：
- scene_detect: 检测视频中的场景切换点
- bpm_detect: 检测音频文件的 BPM
- audio_extract: 从视频中提取音频
- semantic_match: 用语义搜索匹配文字和视频素材
"""


def _validate_scenes(scenes: list[dict]) -> tuple[list[dict], list[str]]:
    """验证场景列表的完整性，过滤无效场景。

    Returns:
        (valid_scenes, warnings)
    """
    valid = []
    warnings = []
    for i, s in enumerate(scenes):
        issues = []
        if not s.get("title"):
            issues.append("缺少 title")
        if not s.get("description"):
            issues.append("缺少 description")
        dur = s.get("duration_sec", 0)
        if not isinstance(dur, (int, float)) or dur <= 0:
            issues.append(f"duration_sec 无效: {dur}")
        if not isinstance(s.get("keywords"), list) or len(s.get("keywords", [])) == 0:
            issues.append("缺少 keywords")
        if issues:
            warnings.append(f"场景[{i}] {', '.join(issues)}，已过滤")
        else:
            valid.append(s)
    if not valid and scenes:
        warnings.append("所有场景均无效，使用 fallback")
    return valid, warnings


class StructureAgent(BaseAgent[StructureInput, StructureOutput]):
    """结构 Agent：根据选题和 Persona 生成脚本骨架。"""

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

            if input_data.persona_prompt:
                system_prompt += f"\n\n## Persona Prompt\n{input_data.persona_prompt}\n"
            if input_data.rag_context:
                system_prompt += f"\n\n## 参考知识\n{input_data.rag_context}\n"

            video_mode = context.extra_params.get("video_mode", "voiceover")
            script_text = context.extra_params.get("script_text", "")
            audio_duration = context.extra_params.get("audio_duration_sec", 0)

            user_prompt = f"选题：{context.topic}\n请生成脚本骨架。"

            # 注入 animation_intents（来自 RequirementsAgent）
            anim_intents = context.extra_params.get("animation_intents", [])
            if anim_intents:
                intent_lines = ["\n## 动画需求意图（来自需求分析）"]
                for intent in anim_intents:
                    if isinstance(intent, dict):
                        desc = intent.get("description", "")
                        text = intent.get("text_content", "")
                        style = intent.get("style_hint", "")
                        intent_lines.append(f"- [{intent.get('type', 'mg')}] {desc}")
                        if text:
                            intent_lines.append(f"  文字: {text}")
                        if style:
                            intent_lines.append(f"  风格: {style}")
                user_prompt += "\n".join(intent_lines)

            anim_guide = self._build_anim_guide()
            user_prompt = self._build_user_prompt(
                video_mode, script_text, audio_duration, user_prompt, anim_guide,
            )

            scenes: list[dict] = []
            warnings: list[str] = []

            from clipwright.config import settings
            has_api_key = bool(settings.llm_api_key)
            logger.info("StructureAgent: 选题=%s, tone=%s, has_api_key=%s", context.topic, tone, has_api_key)

            if has_api_key:
                try:
                    result = await asyncio.wait_for(
                        self._llm.structured_output(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            pipeline_id=context.pipeline_id,
                        ),
                        timeout=self.timeout_sec,
                    )
                    raw_scenes = self._parse_structured_result(result, context.topic, tone)
                    scenes, warnings = _validate_scenes(raw_scenes)
                except asyncio.TimeoutError:
                    logger.warning("StructureAgent: LLM 超时（>%ss），使用 fallback", self.timeout_sec)
                    scenes = self._fallback_scenes(context.topic, tone)
                    warnings.append(f"LLM 超时（>{self.timeout_sec}s）")

            if not scenes:
                logger.info("StructureAgent: 无有效场景，使用 fallback")
                scenes = self._fallback_scenes(context.topic, tone)

            output = StructureOutput(
                decision=AgentDecision.PASS,
                script_skeleton={
                    "topic": context.topic,
                    "tone": tone,
                    "scene_count": len(scenes),
                    "_warnings": warnings,
                },
                scenes=scenes,
            )
            output._llm_usage = self._llm.last_usage
            return output

        except Exception as e:
            logger.exception("StructureAgent 失败: %s", e)
            return self.build_error_output(str(e), StructureOutput)

    def _build_anim_guide(self) -> str:
        """构建动画引导（精简版 top-N + tool 指引）。

        不再全量列出所有动画名到 prompt 中 —
        只展示各类别最常用的 top-N（N 可配），
        并提示 LLM 调用 list_animations 工具获取更多。
        动画数量从 40+ 增长到任意多也不会膨胀 prompt。
        """
        from clipwright.animation.catalog import AnimationCatalog
        TOP_N = 4  # 每类只展示前 N 个最常用动画

        parts = [
            "## 动画标记",
            "在 scene.description 中用以下格式标记动画：",
            "  [文字动画]动画名：要显示的文字",
            "  [逻辑动画]动画名：要展示的概念 或 关系描述",
            "  [过渡动画]动画名",
            "同一场景 description 最多一个标记。",
            "",
        ]

        # 文字动画 top-N
        text_anims = AnimationCatalog.get_text_animations()
        if text_anims:
            parts.append("### 常用文字动画（更多可调用 list_animations 工具）")
            for a in text_anims[:TOP_N]:
                parts.append(f"  [文字动画]{a['name']} — {a.get('desc', '')}")
            parts.append("")

        # 逻辑动画 top-N（含 MG）
        logic_anims = AnimationCatalog.get_logic_animations()
        if logic_anims:
            parts.append("### 常用逻辑动画（更多可调用 list_animations 工具）")
            for a in logic_anims[:TOP_N]:
                parts.append(f"  [逻辑动画]{a['name']} — {a.get('desc', '')}")
            parts.append("")

        # 过渡动画 top-N
        trans_anims = AnimationCatalog.get_transition_animations()
        if trans_anims:
            parts.append("### 常用过渡动画（更多可调用 list_animations 工具）")
            for a in trans_anims[:TOP_N]:
                parts.append(f"  [过渡动画]{a['name']} — {a.get('desc', '')}")
            parts.append("")

        # MG 动态动画（LLM 自动生成）
        parts.append("### LLM 动态 MG 动画")
        parts.append("  当需要数据图表、对比图、进度条等动态图形时，使用 mg_dynamic：")
        parts.append('  [逻辑动画]mg_dynamic:{"description":"动画描述","text":"A|B|结果","style":"tech_dark"}')
        parts.append("  LLM 将根据 description 自动生成完整的 MG 动画。")
        parts.append("")

        total = len(text_anims) + len(logic_anims) + len(trans_anims)
        shown = min(TOP_N, len(text_anims)) + min(TOP_N, len(logic_anims)) + min(TOP_N, len(trans_anims))
        parts.append(
            f"（当前展示 {shown}/{total} 种动画，"
            "调用 list_animations 工具可获取完整列表。"
            "动画 id 或 name 均可用于标记。）"
        )

        return "\n".join(parts)

    def _build_user_prompt(self, mode: str, script: str, audio_dur: float, base: str, anim_guide: str) -> str:
        if mode == "visual" and script:
            truncated = script[:3000] + ("..." if len(script) > 3000 else "")
            lines = [l.strip() for l in truncated.split('\n') if l.strip()]
            return base + (
                f"\n\n## 场景列表\n{truncated}\n\n## 要求\n"
                f"1. 总时长约 {audio_dur:.0f}s，{len(lines)} 个场景\n"
                f"2. 每个场景需包含: title, description, duration_sec, keywords\n"
                f"3. duration_sec 按重要性分配，总和接近 {audio_dur:.0f}s\n"
                f"4. 场景数量接近 {len(lines)}\n"
                f"5. 在 description 中用 [动画] 和 [转场] 标记\n"
                f"6. keywords 具体可搜索\n\n{anim_guide}"
            )
        elif script:
            truncated = script[:2000] + ("..." if len(script) > 2000 else "")
            return base + (
                f"\n\n## 完整文稿\n{truncated}\n\n## 要求\n"
                f"1. 总时长约 {audio_dur:.0f}s\n"
                f"2. 每个场景: title, description, duration_sec, keywords\n"
                f"3. 分析每个场景的逻辑关系，选择合适的动画类型标记\n"
                f"4. 场景总时长接近 {audio_dur:.0f}s\n"
                f"5. 文稿长则多场景，短则少场景\n\n{anim_guide}"
            )
        return base

    async def _tool_executor(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        logger.debug("StructureAgent 工具回调: %s → %s", tool_name, json.dumps(tool_input, ensure_ascii=False)[:300])
        result: ToolExecResult = await ToolRegistry.execute(tool_name, **tool_input)
        return result.model_dump(mode="json")

    def _parse_scenes(self, content: str) -> list[dict]:
        content = content.strip().lstrip('\ufeff').lstrip('\u200b')
        if content.startswith("```"):
            lines = content.splitlines()
            if len(lines) >= 2:
                content = "\n".join(lines[1:-1]).strip()
        if not content:
            return []
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "scenes" in parsed:
                return parsed["scenes"]
        except json.JSONDecodeError:
            pass
        import re
        arr_match = re.search(r'\[.*?\]', content, re.DOTALL)
        if arr_match:
            try:
                parsed = json.loads(arr_match.group())
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        return []

    def _parse_structured_result(self, result: dict, topic: str, tone: str) -> list[dict]:
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "scenes" in result:
            return result["scenes"]
        if "content" in result:
            return self._parse_scenes(result["content"])
        return []

    @staticmethod
    def _fallback_scenes(topic: str, tone: str) -> list[dict]:
        """无 LLM 或无工具时的占位场景列表。"""
        return [
            {"title": "破题", "description": f"以{tone}风格切入话题：{topic}", "keywords": [topic, "引入"], "duration_sec": 30},
            {"title": "展开", "description": "核心论点展开", "keywords": ["分析", "论证"], "duration_sec": 120},
            {"title": "深化", "description": "多角度深入分析", "keywords": ["深度", "视角"], "duration_sec": 90},
            {"title": "收束", "description": "总结与观点输出", "keywords": ["总结", "观点"], "duration_sec": 60},
        ]
