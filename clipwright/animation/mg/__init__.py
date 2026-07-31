"""内置 LLM Motion Graphics Generator — 动态 MG 动画生成（系统核心能力）。

llm_mg 是 ClipWright 内置的 LLM 驱动 MG 动画引擎，不是第三方插件。
提供：
- MGGenerator: LLM 驱动的 MG 动画生成器
- FallbackEngine: 语义模板匹配降级
- validate_mg_json / repair_mg_json: JSON 校验和修复
- list_templates: 列出可用 MG 模板
- register_agent_prompts: 向 Agent 注册工具引导（应用启动时调用）

设计原则：
- 引擎不写死动画风格；风格由 Persona 视觉配置与视频类型（category）
  的实际数据驱动，在生成时动态注入，由 LLM 自行决定动画设计。
- Agent 提示词只引导工具调用，不写死标记规则/使用场景/示例。
"""

from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.mg.fallback import FallbackEngine
from clipwright.animation.mg.validator import validate_mg_json, repair_mg_json
from clipwright.animation.mg.storage import MGStorage

__all__ = [
    "MGGenerator",
    "FallbackEngine",
    "validate_mg_json",
    "repair_mg_json",
    "MGStorage",
    "list_templates",
    "register_agent_prompts",
]


def list_templates() -> list[dict]:
    """列出所有可用 MG 模板（内置，不依赖插件加载器）。"""
    from pathlib import Path
    import json

    templates_dir = Path(__file__).resolve().parent / "templates"
    if not templates_dir.exists():
        return []

    templates: list[dict] = []
    for f in sorted(templates_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            templates.append({
                "animation_id": data.get("animation_id", ""),
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "duration_sec": data.get("duration_sec", 3.0),
                "params": list(data.get("params", {}).keys()),
            })
        except Exception:
            pass
    return templates


def register_agent_prompts() -> None:
    """注册 llm_mg 引擎的 Agent 提示词（StructureAgent / AnimationAgent）。

    仅提供工具调用引导，不写死动画规则或示例。
    动画风格决策由引擎根据 Persona + 视频类型数据在生成时动态完成。
    """
    from clipwright.plugins.prompt_registry import PluginPromptRegistry

    # ── StructureAgent：引导调用工具了解能力（不写死规则） ──
    PluginPromptRegistry.register(
        "llm_mg", "structure",
        "## LLM 动态 MG 动画引擎（mg_dynamic）\n"
        "系统内置 LLM 动效引擎，可根据场景内容动态生成复杂 MG 动画"
        "（数据可视化、动态图形、标题特效等），风格自动适配当前 Persona 与视频类型。\n"
        "需要为场景添加动态图形动画时，先调用 describe_llm_mg 工具了解"
        "引擎能力与标记格式，再决定是否使用及如何标记。",
        priority=10,
        description="llm_mg 引擎工具引导",
    )

    # ── AnimationAgent：引导使用内置引擎（不写死生成规则） ──
    PluginPromptRegistry.register(
        "llm_mg", "animation",
        "## LLM MG 动画引擎（mg_dynamic）\n"
        "处理 mg_dynamic 标记时使用内置 MGGenerator（clipwright.animation.mg）。\n"
        "引擎会接收 Persona 视觉风格与视频类型特征数据，自行决定动画设计；"
        "生成失败时自动降级到模板或文字显示。",
        priority=5,
        description="AnimationAgent 使用 llm_mg 引擎的引导",
    )
