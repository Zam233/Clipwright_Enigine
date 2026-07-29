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
    "duration_sec": 30,
    "voiceover_script": "该场景的口播旁白文案",
    "visual_description": {{
        "material_library": "素材来源/库名",
        "material_content": "画面具体内容描述",
        "material_preference": "素材偏好(色调/构图等)",
        "animation_desc": "动画描述(如有)"
    }}
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
- list_animations: List available MG/text/logic animations. Use when scene needs animation.
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
        # Keywords is optional - auto-generate from title/description if missing
        if not isinstance(s.get("keywords"), list) or len(s.get("keywords", [])) == 0:
            # Auto-generate keywords from title and description
            title = s.get("title", "")
            desc = s.get("description", "")
            auto_keywords = []
            if title:
                auto_keywords.extend(title.split()[:3])
            if desc:
                auto_keywords.extend(desc.split()[:2])
            s["keywords"] = auto_keywords[:5] if auto_keywords else ["场景"]
            warnings.append(f"场景[{i}] keywords 已自动补全")
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
            system_prompt += TOOL_PROMPT

            if input_data.persona_prompt:
                system_prompt += f"\n\n## Persona Prompt\n{input_data.persona_prompt}\n"
            if input_data.rag_context:
                system_prompt += f"\n\n## 参考知识\n{input_data.rag_context}\n"

            video_mode = context.extra_params.get("video_mode", "voiceover")
            script_text = context.extra_params.get("script_text", "")
            audio_duration = context.extra_params.get("audio_duration_sec", 0)

            user_prompt = f"选题：{context.topic}\n请生成脚本骨架。"

            # 注入用户审阅确认的简报/规划书（人在回路审阅结果，须严格遵循）
            brief_context = self._build_brief_context(
                input_data.creative_brief, input_data.production_plan
            )
            if brief_context:
                user_prompt += brief_context

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

            # 注入配音时间轴（来自 DubView，用于场景时间对齐）
            dub_segments = context.extra_params.get("dub_segments", [])
            if dub_segments and isinstance(dub_segments, list):
                dub_lines = ["\n## 配音时间轴（场景时间须与之对齐）"]
                for i, seg in enumerate(dub_segments):
                    if isinstance(seg, dict):
                        start = seg.get("start", 0)
                        end = seg.get("end", 0)
                        text = seg.get("text", "")
                        dub_lines.append(f"- 片段{i+1}: {start:.1f}s - {end:.1f}s | {text[:50]}")
                user_prompt += "\n".join(dub_lines)
                logger.info("StructureAgent: 注入 %d 个配音片段到 prompt", len(dub_segments))

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
                    tool_names = ["list_animations"]
                    available_tools = {}
                    for name in tool_names:
                        tool = ToolRegistry.get(name)
                        if tool and tool.is_available():
                            available_tools[name] = tool
                        else:
                            logger.warning("StructureAgent: 工具 %s 不可用或不存在", name)
                    tool_schemas = [
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or f"Execute {tool.name}",
                                "parameters": getattr(tool, "parameters_schema", {}),
                            },
                        }
                        for tool in available_tools.values()
                    ]
                    logger.info("StructureAgent: 可用工具=%s, tool_schemas数量=%d", list(available_tools.keys()), len(tool_schemas))
                    result = await asyncio.wait_for(
                        self._llm.with_tools(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            tool_executor=self._tool_executor_for_llm,
                            tools=tool_schemas,
                            pipeline_id=context.pipeline_id,
                        ),
                        timeout=self.timeout_sec,
                    )
                    logger.info("StructureAgent: LLM 响应长度=%d, 内容预览=%.200s", len(result.content or ""), result.content or "")
                    raw_scenes = self._parse_scenes(result.content)
                    logger.info("StructureAgent: 解析出 %d 个原始场景", len(raw_scenes))
                    scenes, warnings = _validate_scenes(raw_scenes)
                    logger.info("StructureAgent: 验证后 %d 个有效场景, 警告=%s", len(scenes), warnings)
                except asyncio.TimeoutError:
                    logger.warning("StructureAgent: LLM 超时（>%ss），使用 fallback", self.timeout_sec)
                    scenes = self._fallback_scenes(context.topic, tone, script_text)
                    warnings.append(f"LLM 超时（>{self.timeout_sec}s）")

            if not scenes:
                logger.info("StructureAgent: 无有效场景，使用 fallback")
                scenes = self._fallback_scenes(context.topic, tone, script_text)

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
            truncated = script[:8000] + ("..." if len(script) > 8000 else "")
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
            truncated = script[:8000] + ("..." if len(script) > 8000 else "")
            return base + (
                f"\n\n## 完整文稿\n{truncated}\n\n## 要求\n"
                f"1. 总时长约 {audio_dur:.0f}s\n"
                f"2. 每个场景: title, description, duration_sec, keywords\n"
                f"3. 分析每个场景的逻辑关系，选择合适的动画类型标记\n"
                f"4. 场景总时长接近 {audio_dur:.0f}s\n"
                f"5. 文稿长则多场景，短则少场景\n\n{anim_guide}"
            )
        return base

    @staticmethod
    def _build_brief_context(brief: dict | None, plan: dict | None) -> str:
        """将用户审阅确认的简报/规划书序列化为 prompt 上下文（人在回路）。"""
        parts: list[str] = []
        if isinstance(brief, dict) and brief:
            lines = ["\n\n## 用户已确认的创作简报（必须严格遵循）"]
            simple_fields = [
                ("title", "标题"), ("overview", "概述"), ("target_audience", "目标受众"),
                ("core_message", "核心信息"), ("style_direction", "风格方向"),
                ("structure_suggestion", "结构建议"), ("duration_estimate", "预估时长"),
                ("production_plan", "制作方案"), ("reference_style", "参考风格"),
                ("bgm_requirement", "BGM需求"), ("era_background", "年代背景"),
            ]
            for key, label in simple_fields:
                val = brief.get(key)
                if val:
                    lines.append(f"- {label}: {val}")
            for key, label in (("key_elements", "关键元素"), ("special_requirements", "特殊要求")):
                val = brief.get(key)
                if isinstance(val, list) and val:
                    lines.append(f"- {label}: {'、'.join(str(v) for v in val)}")
            mat = brief.get("material_requirements")
            if isinstance(mat, dict):
                mat_parts = [f"{k}: {v}" for k, v in mat.items() if v and not isinstance(v, dict)]
                if mat_parts:
                    lines.append(f"- 素材需求: {'；'.join(mat_parts)}")
            anim = brief.get("animation_style")
            if isinstance(anim, dict):
                anim_parts = [f"{k}: {v}" for k, v in anim.items() if v and not isinstance(v, dict)]
                if anim_parts:
                    lines.append(f"- 动画风格: {'；'.join(anim_parts)}")
            ratio = brief.get("asset_ratio")
            if isinstance(ratio, dict) and (ratio.get("footage") or ratio.get("mg")):
                lines.append(f"- 素材/动画占比: 实拍 {ratio.get('footage', '')} · MG {ratio.get('mg', '')}")
            if len(lines) > 1:
                parts.append("\n".join(lines))
        if isinstance(plan, dict) and plan:
            markdown = plan.get("markdown_content") or plan.get("markdown") or ""
            if markdown:
                parts.append(f"\n\n## 用户已确认的制作规划书（分镜须与之保持一致）\n{str(markdown)[:3000]}")
        if not parts:
            return ""
        return "".join(parts) + "\n\n请确保生成的分镜场景与上述已确认的简报、规划书在核心信息、风格方向、结构上保持一致。"

    async def _tool_executor(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        logger.debug("StructureAgent 工具回调: %s → %s", tool_name, json.dumps(tool_input, ensure_ascii=False)[:300])
        result: ToolExecResult = await ToolRegistry.execute(tool_name, **tool_input)
        return result.model_dump(mode="json")

    async def _tool_executor_for_llm(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._tool_executor(tool_name, tool_input)

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
        # Find JSON array with balanced bracket matching
        start = content.find('[')
        if start != -1:
            depth = 0
            for i in range(start, len(content)):
                if content[i] == '[':
                    depth += 1
                elif content[i] == ']':
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(content[start:i+1])
                            if isinstance(parsed, list):
                                return parsed
                        except json.JSONDecodeError:
                            pass
                        break
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
    def _fallback_scenes(topic: str, tone: str, script_text: str = "") -> list[dict]:
        """无 LLM 或无工具时的占位场景列表。基于文稿内容生成场景。"""
        # If script_text is provided, try to split it into scenes
        if script_text and len(script_text) > 50:
            # Split by paragraphs or sentences
            paragraphs = [p.strip() for p in script_text.split('\n\n') if p.strip()]
            if len(paragraphs) >= 2:
                scenes = []
                total_duration = 300  # 5 minutes default
                duration_per_scene = total_duration // len(paragraphs)
                for i, para in enumerate(paragraphs[:6]):  # Max 6 scenes
                    # Extract first sentence as title
                    sentences = para.split('。')
                    title = sentences[0][:20] if sentences else f"场景{i+1}"
                    scenes.append({
                        "title": title,
                        "description": para[:100],
                        "keywords": [topic, f"场景{i+1}"],
                        "duration_sec": duration_per_scene,
                        "voiceover_script": para,
                        "visual_description": {}
                    })
                return scenes
        
        # Fallback to default scenes
        return [
            {"title": "破题", "description": f"以{tone}风格切入话题：{topic}", "keywords": [topic, "引入"], "duration_sec": 30, "voiceover_script": "", "visual_description": {}},
            {"title": "展开", "description": "核心论点展开", "keywords": ["分析", "论证"], "duration_sec": 120, "voiceover_script": "", "visual_description": {}},
            {"title": "深化", "description": "多角度深入分析", "keywords": ["深度", "视角"], "duration_sec": 90, "voiceover_script": "", "visual_description": {}},
            {"title": "收束", "description": "总结与观点输出", "keywords": ["总结", "观点"], "duration_sec": 60, "voiceover_script": "", "visual_description": {}},
        ]
