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

## 动画标记规则
每个场景 description 必须包含一个动画标记。优先使用 mg_dynamic（动态 LLM 生成），其次选用预置动画。
在生成场景前**必须先调用 describe_llm_mg 工具**了解动态 MG 能力。

标记选择规则：
1. 数据/数字/统计/百分比、对比/A vs B、流程/步骤、逻辑关系/因果 等场景 → 使用 mg_dynamic
2. 只有纯文字强调/关键句/标语 → 使用 [文字动画]
3. 长文本口播内容 → 走字幕，不做动画标记
4. 每部视频鼓励 1-2 个 mg_dynamic 场景，不要把每个场景都做成 MG

mg_dynamic payload 结构（JSON）：
  {{"description": "动画描述", "text": "A|B|C", "style": "tech_dark",
   "data": [{{"label": "A", "value": 100}}]}}
  - description: 必填，动画内容描述
  - text: 必填，用 | 分隔的数据/文字
  - style: 可选，视觉风格（如 tech_dark）
  - data: 可选，图表数据数组

示例：
  [逻辑动画]mg_dynamic:{{"description": "近三年营收增长柱状图",
  "text": "2023|2024|2025", "style": "tech_dark"}}

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
6. **每个场景 description 中必须包含动画标记，优先使用 mg_dynamic**
"""


TOOL_PROMPT = """
## 可用工具
你**必须**在生成场景脚本前调用工具获取能力信息（工具列表见下方 function schemas）：
1. **describe_llm_mg**：了解内置 LLM 动效生成引擎 (llm_mg) 的动态 MG 动画能力
2. **list_animations**：获取所有预置动画类型（文字/逻辑/过渡）
3. 其他插件工具：根据需要查询（如 AI 生成图片/视频/音乐等）

调用顺序：先 describe_llm_mg，再 list_animations，最后根据返回的能力信息为每个场景选择合适的动画标记。
"""


# mg_dynamic 标记引导 — 供 _build_anim_guide 与 _enrich_scene_animations 共用，
# 保证两处引导一致：payload 结构化（description/text/style/data）+ 选择规则。
MG_DYNAMIC_GUIDE = """### LLM 动态 MG 动画（mg_dynamic）— 先调用 describe_llm_mg 工具
对于数据图表、对比、流程、逻辑关系等需要动态图形的场景，优先使用 mg_dynamic 标记。
**必须**先调用 describe_llm_mg 工具获取最新的标记格式、可用模板和生成能力，不要凭记忆使用。

标记 payload 结构（JSON，紧跟 mg_dynamic: 之后）：
  {"description": "动画描述", "text": "A|B|C", "style": "tech_dark",
   "data": [{"label": "A", "value": 100}]}
  - description: 必填，动画内容自然语言描述（如"三阶段增长柱状图"）
  - text: 必填，用 | 分隔的数据或文字（柱状图条目、对比项、流程步骤、金句等）
  - style: 可选，视觉风格（tech_dark / gradient / clean 等，默认 tech_dark）
  - data: 可选，图表数据数组（图表类用），每项含 label/value 等字段

标记示例：
  [逻辑动画]mg_dynamic:{"description":"近三年营收增长柱状图",
  "text":"2023|2024|2025","style":"tech_dark",
  "data":[{"label":"2023","value":100},{"label":"2024","value":180},{"label":"2025","value":300}]}

选择规则：
1. 场景涉及数据/数字/统计/百分比 → mg_dynamic（数据可视化）
2. 场景涉及对比/A vs B/优劣分析 → mg_dynamic（对比图）
3. 场景涉及流程/步骤/进度 → mg_dynamic（进度条或流程图）
4. 场景涉及逻辑关系/因果/分类/层级 → mg_dynamic（关系图）
5. 场景涉及标题揭示/重要声明（非开场）→ 可使用 mg_dynamic；**开场/引言场景（第一个场景）禁止使用任何逻辑动画标记**——知识讲解类视频开场是口播引入，动画用于中段论证
6. 只有纯文字强调/关键句/标语 → 才使用 [文字动画]
7. 长文本口播内容 → 走字幕，不要用动画标记承载大段文字
8. 同一场景至多一个 mg_dynamic 标记；每部视频鼓励 1-2 个 MG 场景

全局约束：第一个场景（开场）不得使用 [逻辑动画] 或 [文字动画] 标记；动画只用于内容展开后的论证/数据/对比场景。"""


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

            # 复用人在回路已确认的场景结构（production_plan.raw_scenes），
            # 避免管线重新生成导致与用户确认的规划书发生漂移（绕过审阅）。
            confirmed_scenes = []
            if isinstance(input_data.production_plan, dict):
                confirmed_scenes = input_data.production_plan.get("raw_scenes") or []
            if confirmed_scenes and isinstance(confirmed_scenes, list):
                reused, reuse_warnings = _validate_scenes(confirmed_scenes)
                if reused:
                    logger.info("StructureAgent: 复用已确认规划书的 %d 个场景（跳过重新生成）", len(reused))
                    # 为缺少动画标记的场景调用 LLM 补充动画标记（不硬编码），
                    # 使 AnimationAgent 能创建动画（含 LLM 动态 MG 动画）。
                    reused = await self._enrich_scene_animations(reused, context)
                    # 源头剥离开场动画标记：开场（第一个场景）不生成动画，
                    # 知识讲解类视频开场是口播引入，动画用于中段论证/数据/对比。
                    reused = self._strip_opening_animation_markers(reused)
                    output = StructureOutput(
                        decision=AgentDecision.PASS,
                        script_skeleton={
                            "topic": context.topic,
                            "tone": tone,
                            "scene_count": len(reused),
                            "_warnings": reuse_warnings + ["复用已确认规划书场景"],
                        },
                        scenes=reused,
                    )
                    output._llm_usage = None
                    return output

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
            if input_data.vision_prompt:
                system_prompt += f"\n\n## Vision Prompt\n{input_data.vision_prompt}\n"
            if input_data.rag_context:
                system_prompt += f"\n\n## 参考知识\n{input_data.rag_context}\n"

            from clipwright.plugins.prompt_registry import PluginPromptRegistry
            plugin_prompts = PluginPromptRegistry.get_for_agent("structure")
            if plugin_prompts:
                system_prompt += "\n\n## 插件能力\n" + "\n\n".join(plugin_prompts)

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
                    # 动态收集所有声明 agent_callable 且可用的工具
                    # （内置 list_animations/describe_llm_mg + 插件注册工具），
                    # 不再硬编码工具列表，插件工具可被 LLM 主动调用。
                    available_tools: dict[str, Any] = {}
                    for tool in ToolRegistry.list_agent_callable():
                        available_tools[tool.name] = tool
                    if "list_animations" not in available_tools:
                        list_tool = ToolRegistry.get("list_animations")
                        if list_tool and list_tool.is_available():
                            available_tools["list_animations"] = list_tool
                    if "describe_llm_mg" not in available_tools:
                        mg_tool = ToolRegistry.get("describe_llm_mg")
                        if mg_tool and mg_tool.is_available():
                            available_tools["describe_llm_mg"] = mg_tool
                    tool_schemas = [
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or f"Execute {tool.name}",
                                "parameters": tool.to_llm_tool("openai")["function"]["parameters"],
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

            # 为缺少动画标记的场景调用 LLM 补充动画标记（不硬编码），供 AnimationAgent 创建动画
            scenes = await self._enrich_scene_animations(scenes, context)
            # 源头剥离开场动画标记：开场（第一个场景）不生成动画，
            # 知识讲解类视频开场是口播引入，动画用于中段论证/数据/对比。
            scenes = self._strip_opening_animation_markers(scenes)

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

        # 动态 MG 动画 — **必须**先调用 describe_llm_mg 工具了解能力再使用
        parts.append(MG_DYNAMIC_GUIDE)
        parts.append("")

        total = len(text_anims) + len(logic_anims) + len(trans_anims)
        shown = min(TOP_N, len(text_anims)) + min(TOP_N, len(logic_anims)) + min(TOP_N, len(trans_anims))
        parts.append(
            f"（当前展示 {shown}/{total} 种动画，"
            "调用 list_animations 工具可获取完整列表。"
            "动画 id 或 name 均可用于标记。）"
        )

        return "\n".join(parts)

    async def _enrich_scene_animations(self, scenes: list[dict], context: AgentContext) -> list[dict]:
        """为缺少动画标记的场景调用 LLM 补充动画标记（文字动画/逻辑动画/mg_dynamic）。

        不硬编码动画：由 LLM 根据场景内容与可用动画列表（AnimationCatalog 动态提供）
        和 llm_mg 插件能力（通过 describe_llm_mg 工具动态获取）选择合适的动画标记。
        """
        import re
        marker_re = re.compile(r"\[(?:文字动画|逻辑动画|过渡动画|动画)\]")
        scenes_needing = [s for s in scenes if not marker_re.search(s.get("description", "") or "")]
        if not scenes_needing:
            return scenes

        logger.info("StructureAgent: %d/%d 场景缺少动画标记，调用 LLM 补充", len(scenes_needing), len(scenes))
        anim_guide = self._build_anim_guide()

        mg_info = ""
        try:
            from clipwright.animation.mg import list_templates
            templates = list_templates()
            mg_info = (
                "\n## llm_mg 动态 MG 动画（内置，始终可用）\n"
                "llm_mg 是内置的 LLM 驱动动态 MG 动画生成引擎。"
                "可从自然语言描述动态生成完整的 HTML/CSS 动画，"
                "适用于数据图表、对比图、进度条等自定义动效。\n\n"
                "标记格式：[逻辑动画]mg_dynamic:{\"description\":\"动画描述\",\"text\":\"A|B|结果\",\"style\":\"tech_dark\"}\n"
                "在场景 description 末尾添加此标记，AnimationAgent 会在渲染时自动生成 MG 动画。"
            )
            if templates:
                names = "、".join(t["name"] for t in templates[:8])
                mg_info += f"\n可用模板（参考）：{names}"
        except Exception as e:
            logger.debug("StructureAgent: 获取 llm_mg 模板失败: %s", e)

        scenes_text = "\n".join(
            f"场景{i}: 标题={s.get('title', '')} | 描述={s.get('description', '')}"
            for i, s in enumerate(scenes)
        )
        system_prompt = (
            "你是视频动画标记专家。为每个分镜场景的 description 补充一个合适的动画标记，"
            "供后续 AnimationAgent 创建动画。请根据场景内容从可用动画中选择最合适的，不要硬编码。\n\n"
            "## 动画选择规则\n"
            + MG_DYNAMIC_GUIDE
            + "\n\n"
            + anim_guide
            + mg_info
        )
        user_prompt = (
            f"以下是 {len(scenes)} 个分镜场景。请为缺少动画标记的场景补充合适的动画标记。\n\n"
            "分析每个场景的内容（标题、描述），判断属于上述哪种类型：\n"
            "- 若有数据/对比/流程/逻辑关系 → 用 mg_dynamic\n"
            "- 若是纯文字强调 → 用 [文字动画]\n\n"
            "返回 JSON：{\"markers\": [{\"index\": 场景序号(从0开始), \"animation_marker\": \"动画标记字符串\"}]}。\n\n"
            f"{scenes_text}"
        )
        try:
            result = await self._llm.structured_output(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema={
                    "type": "object",
                    "properties": {
                        "markers": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "index": {"type": "integer"},
                                    "animation_marker": {"type": "string"},
                                },
                                "required": ["index", "animation_marker"],
                            },
                        },
                    },
                    "required": ["markers"],
                },
                pipeline_id=context.pipeline_id,
            )
            markers = result.get("markers", []) if isinstance(result, dict) else []
            enriched = 0
            for m in markers:
                idx = m.get("index")
                marker = (m.get("animation_marker") or "").strip()
                if isinstance(idx, int) and 0 <= idx < len(scenes) and marker:
                    desc = scenes[idx].get("description", "") or ""
                    if not marker_re.search(desc):
                        scenes[idx]["description"] = f"{desc} {marker}".strip()
                        enriched += 1
            logger.info("StructureAgent: LLM 补充了 %d 个动画标记", enriched)
        except Exception as e:
            logger.warning("StructureAgent: LLM 补充动画标记失败: %s", e)
        return scenes

    @staticmethod
    def _strip_opening_animation_markers(scenes: list[dict]) -> list[dict]:
        """源头剥离开场场景（第一个场景）的动画标记。

        用户要求：知识讲解类视频开场是口播引入，开头特定时间内不应生成动画标记——
        实现应放在结构 Agent（生成源头），而非动画 Agent（消费端事后跳过）。
        复用规划书场景与 LLM 新生成场景两条路径都在返回前调用本方法，硬保证
        第一个场景的 description 不携带任何 [逻辑动画]/[文字动画]/[动画] 标记。
        """
        import re
        if not scenes:
            return scenes
        opening = scenes[0]
        desc = opening.get("description", "") or ""
        marker_re = re.compile(r"\[(?:文字动画|逻辑动画|过渡动画|动画)\][^\n]*")
        stripped = marker_re.sub("", desc).strip()
        if stripped != desc:
            opening["description"] = stripped
            logger.info(
                "StructureAgent: 开场场景已剥离动画标记（scene[0] title=%s）",
                str(opening.get("title", ""))[:30],
            )
        return scenes

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
        return "".join(parts) + "\n\n请确保生成的分镜场景与上述已确认的简报、规划书在核心信息、风格方向、结构上保持一致，同时忠实反映用户提供的原始文稿内容。"

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
