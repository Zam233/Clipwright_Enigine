"""结构 Agent（StructureAgent）— 脚本骨架生成 + 场景验证 + 超时保护。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    StructureInput,
    StructureOutput,
)
from clipwright.schema.tool import ToolExecResult
from clipwright.schema.skill import SkillExecResult
from clipwright.services.llm import LLMService
from clipwright.services.degradation import note_degradation
from clipwright.tool.base import AgentToolkit
from clipwright.tool.registry import ToolRegistry
from clipwright.config import logger


async def write_back_plan_drift_if_session(
    context: Any,
    drifts: list[dict[str, Any]],
) -> int:
    """V58 写回：有 ``session_id`` 就把偏离并入会话里的规划书；没有则跳过。

    **必须 offload 到线程**：会话模型的 Mongo 访问在事件循环线程里会返回协程
    （仓库既有的 `_io()` 陷阱，`_persist` 也是这么绕开的）；直接在协程里同步调用
    会静默失败——本轮真机就踩到过"返回 0、写入没发生"。
    """
    session_id = str((getattr(context, "extra_params", None) or {}).get("session_id") or "")
    if not session_id or not drifts:
        return 0
    try:
        from clipwright.services.requirements_service import write_back_plan_drift

        return await asyncio.to_thread(write_back_plan_drift, session_id, drifts)
    except Exception as exc:  # noqa: BLE001 - 写回失败不得影响出片
        logger.warning("规划偏离写回失败（已跳过）: %s", exc)
        return 0


def compose_structure_prompt(base: str, category_id: str) -> str:
    """结构阶段的类型注入（身份 + structure 槽位，固定序）。

    抽成模块级函数是为了能单测：结构阶段的 ``execute()`` 牵动 LLM + 工具链，
    在单测里捕获 system prompt 成本太高，容易变成"测试跑不动、接线悄悄丢掉"。
    """
    from clipwright.services.type_prompts import compose

    return compose(base, category_id, "structure")


SYSTEM_PROMPT_TPL = """你是一个{tone}风格的视频脚本创作者。

## Persona 特征
- 语调: {tone}
- 学术密度: {academic_density}
- 最长句长: {max_sentence_len} 字
- 剪辑节奏: {cut_profile}
{persona_identity}

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

## AI 生成策略（何时调用生成工具）
注意：下方 function schemas 只包含**当前已配置可用**的工具——没有出现的生成工具即未配置，不要尝试调用。

当某个场景的画面素材库大概率无法命中时（具象角色/特定品牌/罕见场景/概念视觉，
普通图库关键词难以描述），可用 AI 生成工具定向补料：
- **ai_image_generate**：为场景生成关键帧图片/概念图（成本较低，可多用）
- **ai_video_generate**：为关键场景生成短视频镜头（成本高、耗时数分钟，整条管线**最多调用 2 次**，只给最重要的画面）
- 生成时 prompt 用具体、可视化的中文描述（主体+环境+风格+光线）

生成产物会自动登记进素材库（按 prompt 语义可检索），后续素材阶段将自动按场景
关键词检索并编入时间线——因此生成后请在场景描述中使用与生成 prompt 一致的
关键词，便于素材阶段命中。
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
# 开场允许保留动画标记的快节奏视频类型（插件 id 集合）。
# 快节奏类型（如鬼畜快剪 kichiku_fastcut）开场即为高能引入，应允许开场动画标记；
# 其余类型（knowledge_longform 知识长片等）维持开场禁动画 —— 开场口播引入，
# 动画用于中段论证/数据/对比。TypeMaker 新增的快节奏类型在此追加 id 即可生效。
_OPENING_ANIMATION_ALLOWED_PLUGIN_IDS: frozenset[str] = frozenset(
    {"kichiku_fastcut"}
)


def _pick_better_scenes(
    a: tuple[list[dict], list[str]], b: tuple[list[dict], list[str]]
) -> tuple[list[dict], list[str]]:
    """B7: 双稿择优启发式 — 有效场景数 3-20 区间内越多越优，越界惩罚；平局取 a。"""
    def _score(pair: tuple[list[dict], list[str]]) -> float:
        n = len(pair[0])
        if n == 0:
            return -1.0
        if 3 <= n <= 20:
            return float(n)
        return -abs(n - 8)

    return b if _score(b) > _score(a) else a


def _align_scene_durations(
    scenes: list[dict],
    audio_duration_sec: float = 0.0,
    dub_segments: list | None = None,
) -> tuple[list[dict], str]:
    """把场景时长对齐到配音时间轴（等比缩放），返回 (scenes, warning)。

    E2E 2026-10-28 实测缺口：**只有"复用已确认规划书"分支**做过这件事，
    全新生成的分支完全不管 `audio_duration_sec`——LLM 常把 6 个场景各写 6s，
    面对 702s 的配音，时间线只覆盖 36s（5%），画面还没进入论证就结束了。
    缩放规则与复用分支一致；dub_segments 有真实分段时以它的末段为准。
    """
    warning = ""
    if not scenes:
        return scenes, warning
    target = 0.0
    if isinstance(dub_segments, list) and dub_segments:
        try:
            target = max(
                float(s.get("end_sec", 0) or 0)
                for s in dub_segments if isinstance(s, dict)
            )
        except Exception:  # noqa: BLE001 — 分段脏数据不阻断
            target = 0.0
    if target <= 0:
        target = float(audio_duration_sec or 0)
    total = sum(float(s.get("duration_sec", 0) or 0) for s in scenes)
    if target <= 0 or total <= 0:
        return scenes, warning
    scale = target / total
    if abs(scale - 1.0) <= 0.02:
        return scenes, warning
    # 权重优先用口播字数：同一段配音里，长段落的镜头就该更长。
    # 口播全空时退回原 duration_sec 比例（纯等比缩放）。
    weights = [len(str(s.get("voiceover_script") or "").strip()) for s in scenes]
    if sum(weights) <= 0:
        weights = [float(s.get("duration_sec", 0) or 0) for s in scenes]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return scenes, warning
    for s, w in zip(scenes, weights):
        s["duration_sec"] = round(target * w / weight_sum, 3)
    logger.info(
        "StructureAgent: 场景时长按配音时间轴等比缩放 ×%.2f（%.1fs → %.1fs，%d 个场景）",
        scale, total, target, len(scenes),
    )
    return scenes, f"场景时长已按配音时间轴缩放 ×{scale:.2f}"


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

    @staticmethod
    def _build_system_prompt(
        identity: dict, language: dict, rhythm: dict, constraints: dict,
    ) -> str:
        """构建结构生成 system prompt。

        轮71：identity.positioning / class_perspective 此前从未进入提示词
        （Persona 治理面板可编辑，但结构 Agent 永远读不到）。
        """
        tone = identity.get("tone") or "neutral"
        extra_lines = []
        if identity.get("positioning"):
            extra_lines.append(f"- 账号定位: {identity['positioning']}")
        if identity.get("class_perspective"):
            extra_lines.append(f"- 阶层视角: {identity['class_perspective']}")
        return SYSTEM_PROMPT_TPL.format(
            tone=tone,
            academic_density=language.get("academic_density", 0.1),
            max_sentence_len=language.get("max_sentence_len", 30),
            cut_profile=rhythm.get("cut_profile", "even_flow"),
            max_duration=constraints.get("max_duration_sec", 900),
            persona_identity="\n".join(extra_lines),
        )

    async def execute(
        self, input_data: StructureInput, context: AgentContext
    ) -> StructureOutput:
        try:
            persona_config = input_data.persona_config
            identity = persona_config.get("identity", {})
            language = persona_config.get("language", {})
            rhythm = persona_config.get("rhythm", {})
            constraints = persona_config.get("constraints", {})

            tone = identity.get("tone") or "neutral"

            # 复用人在回路已确认的场景结构（production_plan.raw_scenes），
            # 避免管线重新生成导致与用户确认的规划书发生漂移（绕过审阅）。
            confirmed_scenes = []
            if isinstance(input_data.production_plan, dict):
                confirmed_scenes = input_data.production_plan.get("raw_scenes") or []
            if confirmed_scenes and isinstance(confirmed_scenes, list):
                reused, reuse_warnings = _validate_scenes(confirmed_scenes)
                if reused:
                    logger.info("StructureAgent: 复用已确认规划书的 %d 个场景（跳过重新生成）", len(reused))
                    # 确认简报摘要随 result_data（script_skeleton）流向下游
                    # 素材/剪辑/音频——修复「复用路径下确认字段全部死亡」的契约断裂
                    brief = input_data.creative_brief if isinstance(input_data.creative_brief, dict) else {}
                    brief_summary = {
                        k: brief[k] for k in (
                            "title", "core_message", "key_elements", "era_background",
                            "reference_style", "target_audience", "style_direction",
                            "special_requirements",
                        ) if brief.get(k)
                    }
                    # 配音时间轴对齐——dub_segments 有真实分段时以分段末段为准，
                    # 否则退回上传配音的总时长（旧实现只认 dub_segments，
                    # 直接上传整段配音的会话在这里完全不对齐）。
                    reused, align_warning = _align_scene_durations(
                        reused,
                        float(context.extra_params.get("audio_duration_sec", 0) or 0),
                        context.extra_params.get("dub_segments") or [],
                    )
                    if align_warning:
                        reuse_warnings.append(align_warning)
                    # 为缺少动画标记的场景调用 LLM 补充动画标记（不硬编码），
                    # 使 AnimationAgent 能创建动画（含 LLM 动态 MG 动画）。
                    reused = await self._enrich_scene_animations(reused, context)
                    # 源头剥离开场动画标记（按视频类型分支）：知识类开场是口播引入，
                    # 动画用于中段论证/数据/对比；快节奏类型（kichiku_fastcut 等）开场允许动画。
                    reused = self._strip_opening_animation_markers(
                        reused, context.category_plugin_id
                    )
                    output = StructureOutput(
                        decision=AgentDecision.PASS,
                        script_skeleton={
                            "topic": context.topic,
                            "tone": tone,
                            "scene_count": len(reused),
                            "brief": brief_summary,
                            "_warnings": reuse_warnings + ["复用已确认规划书场景"],
                        },
                        scenes=reused,
                    )
                    output._llm_usage = None
                    # R8：复用路径同样接地。**已带上引用的场景原样保留**
                    # （`keep_existing=True`），所以人工指定的引用不会被检索结果覆盖；
                    # 只补那些"用户确认时还没有引用"的场景。
                    grounded, gnotes = self._ground_scenes(
                        reused, context, list(output.script_skeleton.get("_warnings") or []))
                    if grounded:
                        output.scenes = grounded
                        output.script_skeleton["_warnings"] = gnotes
                    return output

            system_prompt = self._build_system_prompt(identity, language, rhythm, constraints)
            # V1/V15：类型身份 + 结构阶段规范固定序注入（无槽位时零变化）
            system_prompt = compose_structure_prompt(
                system_prompt, context.category_plugin_id or ""
            )
            system_prompt += TOOL_PROMPT

            # A-16（plan-first）：先取规划；开了 plan_first 却没有规划 → 明确失败，
            # 不许悄悄退回"自由生成"（验收 V55）。
            from clipwright.services.plan_first import (
                PlanMissing,
                plan_prompt_block,
                require_plan,
            )

            try:
                plan = require_plan(context.extra_params)
            except PlanMissing as exc:
                logger.error("StructureAgent: plan-first 缺少规划，明确失败: %s", exc)
                return StructureOutput(
                    decision=AgentDecision.FAIL,
                    script_skeleton={
                        "topic": context.topic,
                        "_warnings": [str(exc)],
                        "failure_reason": "plan_missing",
                    },
                    scenes=[],
                )
            if plan is not None:
                block = plan_prompt_block(plan)
                if block:
                    system_prompt += "\n\n" + block

            # F-04 落点：用户创作提示词（L1，优先于类型默认风格）
            from clipwright.services.creative_prompt import creative_prompt_block

            user_prompt_block = creative_prompt_block(context.extra_params)
            if user_prompt_block:
                system_prompt += "\n\n" + user_prompt_block

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

            # 轮 84（数码评测专项）：品类结构模板——类型插件可声明"这类视频
            # 该按什么骨架展开"（如评测 = 开箱→参数→实测→总结），此前系统
            # prompt 品类无关，结构全靠 LLM 自由发挥；外部插件的
            # get_structure_template 能力也在此一并接线（原为死能力）。
            category_tpl = self._get_category_structure_template(
                context.category_plugin_id
            )
            if category_tpl:
                system_prompt += (
                    f"\n\n## 视频类型结构模板（{context.category_plugin_id}）\n"
                    "场景划分应严格遵循以下段落骨架（段落顺序与占比可按选题微调，"
                    "但不可缺失核心段落）：\n" + category_tpl
                )

            # 自愈重做时注入上一轮质检问题（原 _quality_issues 注入无 Agent 消费）
            system_prompt += self.quality_issues_hint(context)

            # R8：把"素材库里实际有什么"注入结构 prompt——**先看料再排场景**，
            # 而不是先排出场景再去找料（找不到就编）。没有索引时返回 ""，零变化。
            index_context = self._index_context(context)
            if index_context:
                system_prompt += (
                    "\n\n## 素材库里已建索引的内容（排场景前请先看料）\n"
                    "下面是你手上**真实可用**的画面。场景描述里的画面要求应尽量落在这些内容上；"
                    "库里确实没有的，就在 description 里说明「需补充素材」，"
                    "不要假设有拍不到的画面。\n" + index_context
                )

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
                # 动态收集所有声明 agent_callable 且可用的工具
                # （内置 list_animations/describe_llm_mg + 插件注册工具），
                # 不再硬编码工具列表，插件工具可被 LLM 主动调用。
                available_tools: dict[str, Any] = {}
                for tool in ToolRegistry.list_agent_callable():
                    available_tools[tool.name] = tool
                # 两个"必备"能力的兜底：同样走能力解析（插件可接管，缺了也不硬塞）
                for _cap in ("list_animations", "describe_mg"):
                    _tool = self.resolve_tool(_cap)
                    if _tool is not None:
                        available_tools.setdefault(_tool.name, _tool)
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
                # 轮 90（批次 8 收尾）：Skill schema 消费——可用技能以 LLM 工具
                # 形式暴露（旧实现 to_llm_tool 全仓零消费，CLAUDE.md 宣称的
                # "自动生成 LLM tool schema" 名存实亡）。执行经 _tool_executor
                # 分派到 SkillRegistry。
                from clipwright.skill.registry import SkillRegistry as _SkillRegistry
                for _info in _SkillRegistry.list():
                    if not _info.available or _info.name in available_tools:
                        continue
                    _skill = _SkillRegistry.get(_info.name)
                    if _skill is None:
                        continue
                    tool_schemas.append(_skill.to_llm_tool("openai"))
                logger.info("StructureAgent: 可用工具=%s, tool_schemas数量=%d", list(available_tools.keys()), len(tool_schemas))

                async def _generate_scenes(variant_hint: str = "") -> tuple[list[dict], list[str]]:
                    """单次结构生成（统一收口：超时+重试；失败回退 fallback）。"""
                    try:
                        up = user_prompt + variant_hint
                        result = await self.llm_call_with_retry(
                            lambda: self._llm.with_tools(
                                system_prompt=system_prompt,
                                user_prompt=up,
                                tool_executor=self._tool_executor_for_llm,
                                tools=tool_schemas,
                                pipeline_id=context.pipeline_id,
                            ),
                            retries=1, timeout=self.timeout_sec,
                        )
                        logger.info("StructureAgent: LLM 响应长度=%d, 内容预览=%.200s", len(result.content or ""), result.content or "")
                        raw_scenes = self._parse_scenes(result.content)
                        logger.info("StructureAgent: 解析出 %d 个原始场景", len(raw_scenes))
                        sc, wa = _validate_scenes(raw_scenes)
                        logger.info("StructureAgent: 验证后 %d 个有效场景, 警告=%s", len(sc), wa)
                        return sc, wa
                    except asyncio.TimeoutError:
                        logger.warning("StructureAgent: LLM 超时（>%ss），使用 fallback", self.timeout_sec)
                        # F9：超时回退到模板化分镜属于「完成了但打了折扣」，必须留痕
                        note_degradation(
                            "llm_timeout_fallback",
                            detail=f"StructureAgent: 分镜生成 LLM 超时（>{self.timeout_sec}s）",
                            scope="scenes",
                            impact="改用模板化分镜，结构与文案质量下降",
                        )
                        return self._fallback_scenes(context.topic, tone, script_text), [f"LLM 超时（>{self.timeout_sec}s）"]

                scenes, warnings = await _generate_scenes()

                # /A9: 多方案择优 — voiceover 双稿会双倍最大 LLM 开销与耗时
                # （择优启发式只比较场景数）。批3 改为配置开关，默认关闭。
                if (video_mode == "voiceover" and script_text.strip() and scenes
                        and getattr(settings, "structure_double_draft", False)):
                    scenes_b, warnings_b = await _generate_scenes(
                        "\n\n（额外要求：请再输出一版节奏/结构安排与上述不同的替代方案，仍遵循全部约束）"
                    )
                    picked = _pick_better_scenes((scenes, warnings), (scenes_b, warnings_b))
                    scenes, warnings = picked
                    warnings = list(warnings) + ["已生成双稿并择优"]

            if not scenes:
                logger.info("StructureAgent: 无有效场景，使用 fallback")
                # F9：LLM 正常返回但没解析出可用场景 → 同样是降级
                note_degradation(
                    "scenes_fallback",
                    detail="StructureAgent: LLM 未产出可用的有效场景，改用模板化分镜",
                    scope="scenes",
                    impact="改用模板化分镜，结构与文案质量下降",
                )
                scenes = self._fallback_scenes(context.topic, tone, script_text)

            # 为缺少动画标记的场景调用 LLM 补充动画标记（不硬编码），供 AnimationAgent 创建动画
            scenes = await self._enrich_scene_animations(scenes, context)
            # 源头剥离开场动画标记（按视频类型分支）：知识类开场是口播引入，
            # 动画用于中段论证/数据/对比；快节奏类型（kichiku_fastcut 等）开场允许动画。
            scenes = self._strip_opening_animation_markers(
                scenes, context.category_plugin_id
            )
            # 配音时间轴对齐（voiceover 模式）：LLM 给的 duration_sec 常常与真实
            # 配音长度差一个数量级（实测 36s 覆盖 702s 文稿），此处等比拉到配音长度。
            if video_mode == "voiceover":
                scenes, _align_warning = _align_scene_durations(
                    scenes,
                    float(audio_duration or 0),
                    context.extra_params.get("dub_segments") or [],
                )
                if _align_warning:
                    warnings = list(warnings) + [_align_warning]

            # A-17：执行偏离规划 → plan_drift 留痕（JSONL + trace 事件；无 plan 时零变化）
            if plan is not None:
                try:
                    from clipwright.services.plan_first import (
                        append_drift,
                        detect_drift,
                        emit_drift_events,
                    )

                    drifts = detect_drift(plan, scenes)
                    if drifts:
                        drift_path = (context.extra_params or {}).get("plan_drift_path")
                        if drift_path:
                            append_drift(drift_path, drifts)
                        # V58：同一批偏离同时进 trace，前端运行详情可查
                        emit_drift_events(context.pipeline_id, drifts)
                        # V58：会话里的规划书同步写回（有 session_id 才写）
                        await write_back_plan_drift_if_session(context, drifts)
                        warnings = list(warnings) + [
                            f"plan_drift: {item.get('code')}" for item in drifts
                        ]
                except Exception as exc:  # noqa: BLE001 - 留痕失败不得影响出片
                    logger.warning("plan_drift 记录失败（已跳过）: %s", exc)
            # R8：给每个场景钉上真实素材镜头引用（`material_refs`），
            # 让规划书从"一段文字描述"变成"指名道姓用哪些镜头"，下游不再各剪各的。
            scenes, warnings = self._ground_scenes(scenes, context, warnings)

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

    # ── R8：方案接地（场景 → 真实镜头引用）────────────────────

    @staticmethod
    def _index_context(context: AgentContext) -> str:
        """已建索引素材的内容摘要（无索引 → ""，行为与接线前完全一致）。"""
        from clipwright.services.content_query import index_context_text

        project_id = str((context.extra_params or {}).get("project_id") or "") or None
        try:
            return index_context_text(project_id)
        except Exception as exc:  # noqa: BLE001 — 索引读不到不该影响结构生成
            logger.info("结构 Agent 读取内容索引失败，跳过注入：%s", exc)
            return ""

    def _ground_scenes(
        self, scenes: list[dict[str, Any]], context: AgentContext, warnings: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """给场景钉真实镜头引用；**失败一律降级**（不阻断管线）。

        引用由索引检索确定性算出，不由 LLM 编——"模型编素材路径"是幻觉重灾区，
        与 R5 拦截幻觉 op 是同一个道理。
        """
        from clipwright.services.scene_grounding import ground_scenes, load_project_indexes

        project_id = str((context.extra_params or {}).get("project_id") or "") or None
        try:
            indexes = load_project_indexes(project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取内容索引失败，规划书接地跳过：%s", exc)
            return scenes, warnings
        if not indexes:
            return scenes, warnings
        grounded, notes = ground_scenes(scenes, indexes)
        if not grounded:
            return scenes, warnings
        hits = sum(1 for s in grounded if s.get("material_refs"))
        logger.info("StructureAgent: 规划书接地 %d/%d 个场景引用了真实镜头", hits, len(grounded))
        return grounded, list(warnings) + notes

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
            self._ensure_chart_coverage(scenes, context.category_plugin_id)
            self._ensure_outro_animation(scenes, context.category_plugin_id)
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
            result = await self.llm_or_fallback(lambda: self._llm.structured_output(
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
                # 推理型模型的 reasoning 会吃掉预算：不给 max_tokens 时实测
                # HTTP 200 但正文为空（"LLM 补充了 0 个动画标记"），动画全链路
                # 因此少标记。给足预算才拿得到 markers 数组。
                max_tokens=4096,
                pipeline_id=context.pipeline_id), fallback=None, retries=2)
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
        self._ensure_chart_coverage(scenes, context.category_plugin_id)
        self._ensure_outro_animation(scenes, context.category_plugin_id)
        return scenes

    # 轮 84（批次 5 收尾）：知识/评测类的图表硬规则适用类型
    _DATA_CHART_CATEGORIES = {"knowledge_longform", "digital_review"}
    _DATA_CHART_MARKER_RE = re.compile(r"\[逻辑动画\]|mg_[a-z_]+")
    _DATA_CHART_KEYWORDS = (
        "对比", "增长", "提升", "下降", "排名", "占比", "数据", "参数",
        "性能", "跑分", "续航", "价格", "销量", "实测", "评测", "优势",
    )

    @staticmethod
    def _get_category_structure_template(category_plugin_id: str) -> str:
        """轮 84：从类型插件取结构模板（get_structure_template，原死能力接线）。

        轮 87：同时拼接 get_annotation_templates（节奏标注参考）——
        此前该能力零消费。无类型/插件未实现/模板为空 → 返回空串（不注入）。
        """
        if not category_plugin_id:
            return ""
        try:
            from clipwright.category.registry import CategoryRegistry

            plugin = CategoryRegistry.get(category_plugin_id)
        except Exception:
            return ""
        if plugin is None:
            return ""
        parts: list[str] = []
        getter = getattr(plugin, "get_structure_template", None)
        if callable(getter):
            try:
                tpl = str(getter() or "").strip()
            except Exception:
                logger.exception("类型插件结构模板获取失败: %s", category_plugin_id)
                tpl = ""
            if tpl:
                parts.append(tpl)
        ann_getter = getattr(plugin, "get_annotation_templates", None)
        if callable(ann_getter):
            try:
                anns = [str(a) for a in (ann_getter() or []) if str(a).strip()]
            except Exception:
                anns = []
            if anns:
                parts.append("节奏标注参考（可在合适场景的 description 中落位）："
                             + "、".join(anns))
        return "\n".join(parts)


    @classmethod
    def _ensure_chart_coverage(cls, scenes: list[dict], category_plugin_id: str = "") -> int:
        """图表硬规则：知识/评测类时间线至少一个数据类动画标记。

        图表引擎（24 种 diagram preset + MG 模板）齐备但触发靠 LLM 写标记的
        概率事件——而"知识讲解必须有图表"是内容质量硬要求。候选场景（跳过
        开场）中无任何数据类标记时，向数据特征最强的场景注入确定性
        mg_dynamic 标记（MG 引擎自带 LLM→关键词→模板三级兜底，保证成片出图）。

        返回注入的场景序号（-1 = 已有覆盖/不适用，未注入）。
        """
        # A-08：类型可声明 required_animation_markers: [data_chart]；
        # 未声明时回落到旧集合（D4：本期迁出，一周期后删）。
        from clipwright.services.type_spec_runtime import required_markers

        wants_chart = "data_chart" in required_markers(category_plugin_id) or (
            category_plugin_id in cls._DATA_CHART_CATEGORIES
        )
        if not wants_chart or len(scenes) < 2:
            return -1
        if any(
            cls._DATA_CHART_MARKER_RE.search(s.get("description", "") or "")
            for s in scenes
        ):
            return -1
        keywords = cls._DATA_CHART_KEYWORDS
        best_idx, best_score = -1, 0
        for i, s in enumerate(scenes):
            if i == 0:
                continue  # 开场口播不强行配图
            text = f"{s.get('title', '')} {s.get('description', '')}"
            score = sum(text.count(k) for k in keywords)
            if score > best_score:
                best_idx, best_score = i, score
        if best_idx < 0:
            best_idx = 1  # 无数据特征词时取第一个正片场景
        s = scenes[best_idx]
        safe_title = re.sub(r'["\\]', "'", (s.get("title", "") or ""))[:40]
        desc = (s.get("description") or "").strip()
        marker = (
            f'[逻辑动画]mg_dynamic:{{"description":"{safe_title} 核心数据对比图表",'
            f'"text":"","style":"tech_dark"}}'
        )
        s["description"] = f"{desc} {marker}".strip()
        logger.info(
            "StructureAgent: %s 类图表硬规则生效——场景 %d 注入数据图表标记",
            category_plugin_id, best_idx,
        )
        return best_idx

    # 轮 93（批次 6）：片尾文字动画规则适用类型（有结构模板的内容类）
    _OUTRO_ANIMATION_CATEGORIES = {"knowledge_longform", "digital_review", "vlog_daily"}
    _ANY_ANIMATION_MARKER_RE = re.compile(r"\[(?:文字动画|逻辑动画|过渡动画|动画)\]|mg_[a-z_]+")

    @classmethod
    def _ensure_outro_animation(cls, scenes: list[dict], category_plugin_id: str = "") -> int:
        """片尾文字动画规则：结尾场景无任何动画标记时补一个淡入文字动画。

        结尾"总结升华/购买建议/感谢观看"配一个收束文字动画是观感硬要求，
        与开场口播不配动画的约定（_strip_opening_animation_markers）互补。
        仅对 _OUTRO_ANIMATION_CATEGORIES 生效；已有标记则不动。
        返回注入的场景序号（-1 = 未注入）。
        """
        # A-08：declares.outro_animation 优先，未声明回落旧集合。
        from clipwright.services.type_spec_runtime import declare_flag

        if not declare_flag(
            category_plugin_id,
            "outro_animation",
            category_plugin_id in cls._OUTRO_ANIMATION_CATEGORIES,
        ) or not scenes:
            return -1
        last = scenes[-1]
        desc = (last.get("description") or "").strip()
        if cls._ANY_ANIMATION_MARKER_RE.search(desc):
            return -1
        last["description"] = f"{desc} [文字动画]text_fade_in".strip()
        logger.info(
            "StructureAgent: %s 类片尾规则生效——末场景补充收束文字动画",
            category_plugin_id,
        )
        return len(scenes) - 1

    @staticmethod
    def _strip_opening_animation_markers(
        scenes: list[dict], category_plugin_id: str = ""
    ) -> list[dict]:
        """源头剥离开场场景（第一个场景）的动画标记。

        用户要求：知识讲解类视频开场是口播引入，开头特定时间内不应生成动画标记——
        实现应放在结构 Agent（生成源头），而非动画 Agent（消费端事后跳过）。
        复用规划书场景与 LLM 新生成场景两条路径都在返回前调用本方法，硬保证
        第一个场景的 description 不携带任何 [逻辑动画]/[文字动画]/[动画] 标记。

        按视频类型分支（来自 context.category_plugin_id）：
        - 快节奏类型（kichiku_fastcut 等，见 _OPENING_ANIMATION_ALLOWED_PLUGIN_IDS）：
          开场允许动画标记（原样保留），符合其高能开场特性；
        - 知识类及其余类型（knowledge_longform 等，含未指定类型）：维持原有剥离行为。
        """
        # A-08：declares.opening_animation=True 表示"开场允许动画标记"；
        # 未声明时回落旧集合（快速类型允许，其余剥离）。
        from clipwright.services.type_spec_runtime import declare_flag

        if declare_flag(
            category_plugin_id,
            "opening_animation",
            category_plugin_id in _OPENING_ANIMATION_ALLOWED_PLUGIN_IDS,
        ):
            return scenes
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
                "StructureAgent: 开场场景已剥离动画标记（scene[0] title=%s, category=%s）",
                str(opening.get("title", ""))[:30],
                category_plugin_id,
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
            if isinstance(ratio, dict) and (ratio.get("footage") or ratio.get("mg")
                                            or ratio.get("ai_generated")):
                line = f"- 素材/动画占比: 实拍 {ratio.get('footage', '')} · MG {ratio.get('mg', '')}"
                if ratio.get("ai_generated"):
                    line += f" · AI 生成 {ratio.get('ai_generated')}"
                lines.append(line)
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
        # 轮 90：名字命中技能注册表 → 优先走 SkillRegistry（技能与工具同池暴露）
        from clipwright.skill.registry import SkillRegistry as _SkillRegistry
        if _SkillRegistry.get(tool_name) is not None:
            _skill_result = await _SkillRegistry.run_skill(tool_name, tool_input)
            if isinstance(_skill_result, SkillExecResult):
                return _skill_result.model_dump(mode="json")
            return {"status": "success", "skill_name": tool_name,
                    "output": _skill_result if isinstance(_skill_result, dict) else {"result": _skill_result}}
        result: ToolExecResult = await ToolRegistry.call_with_input(tool_name, tool_input)
        return result.model_dump(mode="json")

    async def _tool_executor_for_llm(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._tool_executor(tool_name, tool_input)

    @staticmethod
    def _scenes_from_parsed(parsed: Any) -> list[dict]:
        """从已解析 JSON 中提取场景列表。"""
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("scenes"), list):
            return parsed["scenes"]
        return []

    def _parse_scenes(self, content: str) -> list[dict]:
        content = (content or "").strip().lstrip('\ufeff').lstrip('\u200b')
        # 围栏剥离修复——原实现无条件丢弃首行与末行，当 LLM 输出
        # 「说明文字 + 围栏 JSON」或围栏未闭合时会把 JSON 截坏。
        if content.startswith("```"):
            lines = content.splitlines()
            if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                content = "\n".join(lines[1:-1]).strip()
            else:
                content = "\n".join(lines[1:]).strip()
        if not content:
            return []
        try:
            parsed = json.loads(content)
            return self._scenes_from_parsed(parsed)
        except json.JSONDecodeError:
            pass
        # 放弃逐字符括号深度扫描。提示词强制场景 JSON 的字符串值内含
        # `[逻辑动画]mg_dynamic:{...}` 等标记，字符串内的括号会使深度计数
        # 永不归零/提前归零，解析必然失败并静默降级 fallback_scenes。
        # 改为文本定位 + json.JSONDecoder（JSON 语法自身正确处理字符串内括号）。
        start = content.find('[')
        if start == -1:
            return []
        decoder = json.JSONDecoder()
        try:
            # raw_decode：从首个 '[' 起解析第一个完整 JSON 值，忽略前后杂文本
            parsed, _ = decoder.raw_decode(content[start:])
            result = self._scenes_from_parsed(parsed)
            if result:
                return result
        except json.JSONDecodeError:
            pass
        # 截断/尾部缺损兜底：']' 从尾部逐步回退，交 json.loads 重试（有界 10 次）
        end = content.rfind(']')
        tries = 0
        while end > start and tries < 10:
            try:
                parsed = json.loads(content[start:end + 1])
                result = self._scenes_from_parsed(parsed)
                if result:
                    return result
            except json.JSONDecodeError:
                pass
            end = content.rfind(']', start, end)
            tries += 1
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
