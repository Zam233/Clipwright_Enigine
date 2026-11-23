"""Agent 提示词单一来源（PRD W4，2026-11-02）。

为什么集中：提示词原来散在 7 个 agent、kernel、loop、MG generator 里，同一个规则在多处
出现且措辞不同（例：阶段顺序、降级必须处置、大对象走引用），改一处漏三处。收在一个模块里
才能"一处改、全局生效"，也才能被审阅与比对。

三条纪律：

1. **文案逐字不动**。本次只是搬运，不改一个字；有插值的模板保持原占位符形态
   （`{tone}` / `{{BRIEF_TEMPLATE}}`），由调用方按原方式替换。
2. **只放文本**。不放业务判断、不放配置读取、不做 I/O；需要参数化的用模块底部
   `def build_*()` 函数，参数显式声明。
3. **覆盖顺序**（PRD W4 裁定）：全局 `prompts.py` < Category < Persona < Skill < 插件。
   下游覆盖必须通过既有的 persona/插件机制注入，**不要**在本文件里读配置。

迁移对照（符号 → 原位置）：

| 符号 | 原位置 |
|---|---|
| `CREATIVE_BRIEF_SYSTEM` | `agents/requirements_agent.py` |
| `PLAN_TRANSLATE_SYSTEM` | `agents/requirements_agent.py` |
| `STRUCTURE_SYSTEM_PROMPT_TPL` | `agents/structure_agent.py:SYSTEM_PROMPT_TPL` |
| `STRUCTURE_TOOL_PROMPT` | `agents/structure_agent.py:TOOL_PROMPT` |
| `STRUCTURE_MG_DYNAMIC_GUIDE` | `agents/structure_agent.py:MG_DYNAMIC_GUIDE` |
| `LOOP_PLAN_ONLY_SYSTEM` | `services/agent_loop.py:_PLAN_ONLY_SYSTEM` |
| `LOOP_CLOSEOUT_BLOCK` | `services/agent_loop.py:_CLOSEOUT_BLOCK` |
| `LOOP_NORMAL_SYSTEM` | `services/agent_loop.py:_NORMAL_SYSTEM` |
| `MG_STRICT_JSON_OUTPUT` | `animation/mg/generator.py:_STRICT_JSON_OUTPUT` |
| `MG_NO_BACKGROUND_CONSTRAINT` | `animation/mg/generator.py:_NO_BACKGROUND_CONSTRAINT` |
| `MG_ORCHESTRATION_CONSTRAINTS` | `animation/mg/generator.py:_MG_ORCHESTRATION_CONSTRAINTS` |

搬运动作**不含**函数内的动态拼接模板（material / edit / audio / quality 的局部 prompt），
那些是下一步（参数化）的对象，见 PRD W4.4。
"""

from __future__ import annotations

from .agents import *  # noqa: F401,F403
from .loop import *  # noqa: F401,F403
from .mg import *  # noqa: F401,F403
from .material import *  # noqa: F401,F403
from .chat import CHAT_TITLE_SYSTEM  # noqa: F401
from .type_forge import TYPE_FORGE_SYSTEM, TYPE_TITLE_SYSTEM  # noqa: F401
from .engine import (  # noqa: F401
    KINETIC_TYPOGRAPHY_SYSTEM,
    MG_CRITIQUE_CHECK_ITEMS,
    MG_CRITIQUE_IMAGE_MATCH_CHECK,
    MG_CRITIQUE_OUTPUT,
    MG_CRITIQUE_PREFIX,
    MG_CRITIQUE_REPAIR_IMAGE_GUIDANCE,
    MG_DESIGNER_SYSTEM,
    MG_FALLBACK_SYSTEM,
    MG_VISUAL_RUBRIC,
    PV_ENGINE_ORCHESTRATOR_SYSTEM,
)


# ── requirements / CREATIVE_BRIEF_SYSTEM ── 需求简报对话与 JSON 字段约束（含 {{BRIEF_TEMPLATE}} 占位）

# ── requirements / PLAN_TRANSLATE_SYSTEM ── 结构产物翻译为制作规划书

# ── structure / SYSTEM_PROMPT_TPL ── 分镜/脚本结构生成（.format 模板）

# ── structure / TOOL_PROMPT ── 结构阶段的工具使用附加约束

# ── structure / MG_DYNAMIC_GUIDE ── MG 动态动画标记引导

# ── loop / _PLAN_ONLY_SYSTEM ── 规划书任务：只派 structure 后收尾

# ── loop / _CLOSEOUT_BLOCK ── 软着陆收口模式的权威规则

# ── loop / _NORMAL_SYSTEM ── 主 Agent 创作任务的系统提示

# ── mg / _STRICT_JSON_OUTPUT ── MG 只输出裸 JSON

# ── mg / _NO_BACKGROUND_CONSTRAINT ── MG 背景遮挡约束

# ── mg / _MG_ORCHESTRATION_CONSTRAINTS ── MG 节奏/构图/错峰约束


# ── 参数化提示词（PRD W4.4）────────────────────────────────────────────────
# 为什么单独一组：这些文案带运行时数字（剩余回合/上限/已用），
# 原来以 f-string 内联在函数里，改文案必须改代码。搬到这里之后：
# 文案是常量，数字由调用方传参；`{}` 是**唯一的插值入口**，不会与文案里的
# 花括号混淆（原 f-string 里的花括号在这里保持字面量语义）。

#: 回合预算提示的抬头（`{remaining}` / `{max_turns}` / `{used}`）

#: 剩余充裕：按需推进

#: 剩余中等：提高收尾优先级

#: 剩余告急：必须立即收尾


def _c(name: str) -> str:
    """取常量文本：插件符号覆盖优先，否则本模块默认值（B5 常量覆盖）。

    为什么需要它：19 个「内部组合常量」（`LOOP_BUDGET_HEADER`、`MATERIAL_QUERY_BATCH_HEAD`…）
    是在 builder 体内被拼接的。改成运行时读 `_c(...)`，插件覆盖同名符号才能生效；无覆盖时
    返回的就是本模块默认字符串，输出逐字节不变。

    延迟导入避免 `prompts` ↔ `plugins.prompt_overrides` 循环导入（后者要用
    `getattr(prompts, name)` 取默认值）。
    """
    try:
        from clipwright.plugins.prompt_overrides import resolve_constant

        return resolve_constant(name)
    except Exception:  # noqa: BLE001 — 覆盖层不可用时用默认值，绝不因此报错
        return str(globals().get(name, ""))


def build_budget_note(remaining: int, max_turns: int, used: int) -> str:
    """按剩余回合选档，拼出预算提示（文案见上面的常量）。"""
    head = _c("LOOP_BUDGET_HEADER").format(remaining=remaining, max_turns=max_turns, used=used)
    if remaining > 40:
        return head + _c("LOOP_BUDGET_PLENTY")
    if remaining > 12:
        return head + _c("LOOP_BUDGET_TIGHT")
    return head + _c("LOOP_BUDGET_CRITICAL")


# ── 模块内提示词（PRD W4.3 第二批，2026-11-02）─────────────────────────────
# 这些原本内联在 agent 的函数里（局部变量），搬出来才能被审阅与统一改写。

#: 原位置 clipwright/agents/edit_agent.py:1044

#: 原位置 clipwright/agents/quality_agent.py:613


#: 规划书翻译的衔接句（`{brief_json}` 由调用方传入参考方案 JSON）

#: 插件能力扩展段抬头（无插件提示词时不拼）

#: 联网搜索参考段抬头（无联网上下文时不拼）


def build_plan_translate_system(brief_json: str) -> str:
    """把参考方案 JSON 拼进规划书翻译提示（原 f-string 逐字等价）。"""
    return _c("PLAN_TRANSLATE_SYSTEM") + _c("PLAN_TRANSLATE_HEADER").format(brief_json=brief_json)


# ── 带插值的模块提示词（PRD W4.4 收尾批）─────────────────────────────────


def build_bgm_match_system(slot_keys: list[str]) -> str:
    """BGM 情绪匹配提示（原 `audio_agent._llm_match_bgm` 内的 f-string，逐字等价）。"""
    slots = "、".join(slot_keys)
    return (
        "你是视频 BGM 情绪匹配专家。根据分镜场景的情绪基调，为每个 BGM 槽位"
        "推荐音乐风格，并设计音量包络与停顿。\n\n"
        "## BGM 槽位（按时间进度划分）\n"
        f"可用槽位: {slots}\n"
        "槽位含义参考：intro/opening/hook=开场铺垫；backing/background="
        "平稳推进；climax/build/intensity=高潮；outro/resolution=收尾。\n\n"
        "## 输出要求\n"
        '返回 JSON: {"allocations": [{"slot": "槽位key", '
        '"style": "音乐风格描述(如 warm ambient piano / tense electronic)", '
        '"volume_envelope": [{"t": 0-1(相对时长), "v": 0-1(音量)}], '
        '"pause_design": {"pause_before_sec": 秒, "pause_after_sec": 秒}}]}\n'
        "- slot 必须是给定槽位之一，每个槽位最多一条 allocation；\n"
        "- 包络至少 2 个点，t 由 0 递增到 1，音量 0-1；\n"
        "- 停顿秒数必须 >= 0。"
    )


#: 动画标记补充提示的固定抬头（与 MG_DYNAMIC_GUIDE / 动画目录拼接使用）


def build_anim_marker_system(anim_guide: str, mg_info: str) -> str:
    """补动画标记的 system prompt（原 `structure_agent._enrich_scene_animations` 逐字等价）。

    `MG_DYNAMIC_GUIDE` 固定在 prompts.py 里，`anim_guide` / `mg_info` 由调用方
    按当前动画目录与 MG 能力动态拼好传入。
    """
    return _c("ANIM_MARKER_SYSTEM_HEADER") + _c("STRUCTURE_MG_DYNAMIC_GUIDE") + "\n\n" + anim_guide + mg_info


def build_anim_marker_user(scenes_count: int, scenes_text: str) -> str:
    """补动画标记的 user prompt（逐字等价）。"""
    return (
        f"以下是 {scenes_count} 个分镜场景。请为缺少动画标记的场景补充合适的动画标记。\n\n"
        "分析每个场景的内容（标题、描述），判断属于上述哪种类型：\n"
        "- 若有数据/对比/流程/逻辑关系 → 用 mg_dynamic\n"
        "- 若是纯文字强调 → 用 【文字动画】\n\n"
        '返回 JSON：{"markers": [{"index": 场景序号(从0开始), "animation_marker": "动画标记字符串"}]}。\n\n'
        f"{scenes_text}"
    )


# ── 素材搜索关键词提示词（PRD W4.4 最后一批，material_agent）───────────────
# 这三处原来在 `material_agent` 的三个模块级函数里用 f-string 反复拼接
# （scene_lines / category_hint / style_hint / brief_hint_text 四个片段混在长文案里）。
# 搬出来后：文案是常量，运行时数据走参数；`style_hint` / `brief_hint_text`
# 这两个"有才拼、没有就是空串"的片段也一并搬进来，保证输出逐字不变。

#: 单场景关键词提示（`{scene_title}` / `{keywords}` / `{description}` 之外的固定文案）

#: 批量关键词提示的固定开头

#: 批量关键词提示的固定要求段（插在场景行之后）

#: 批量提示里"英文关键词"那段（图库索引是英文，中文命中率为 0）

#: 英译提示（`{text}` 是拼好的中文关键词串）

#: Persona 风格偏好（无 persona_style 时整段为空串）
def material_style_hint(persona_style: str) -> str:
    return f"\n- 优先匹配风格: {persona_style}" if persona_style else ""


def material_brief_hint(brief_hint: str) -> str:
    """简报素材要求（无 brief_hint 时整段为空串）。"""
    return f"\n- 简报素材要求: {brief_hint}" if brief_hint else ""


def build_material_queries_batch_prompt(
    scene_lines: list[str], *, category_hint: str = "",
    persona_style: str = "", brief_hint: str = "",
) -> str:
    """批量关键词提示（逐字等价于旧 f-string 拼接）。"""
    return (
        _c("MATERIAL_QUERY_BATCH_HEAD")
        + "\n".join(scene_lines)
        + _c("MATERIAL_QUERY_BATCH_TAIL")
        + category_hint
        + _c("MATERIAL_QUERY_BATCH_EN")
        + material_style_hint(persona_style)
        + material_brief_hint(brief_hint)
        + _c("COMMON_TOOL_GUIDE")
    )


def build_material_queries_prompt(
    scene_title: str, keywords: list[str], description: str, *,
    persona_style: str = "", brief_hint: str = "",
) -> str:
    """单场景关键词提示（逐字等价）。"""
    return (
        _c("MATERIAL_QUERY_SINGLE_HEAD").format(
            scene_title=scene_title,
            keywords=", ".join(keywords),
            description=description,
        )
        + material_style_hint(persona_style)
        + material_brief_hint(brief_hint)
        + _c("COMMON_TOOL_GUIDE")
    )


def build_material_english_prompt(text: str) -> str:
    """中文关键词英译提示（逐字等价）。"""
    return _c("MATERIAL_ENGLISH_HEAD") + text
