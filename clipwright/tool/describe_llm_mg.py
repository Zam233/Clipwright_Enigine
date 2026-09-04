"""描述 LLM MG 内置能力 — 供 Agent 按需了解动态 MG 生成。

不硬编码动画类型：由内置 llm_mg 模块动态提供可用的模板和参数信息。
Agent 在需要创建自定义 MG 动画时调用此工具，获取标记格式和可用模板。

注意：llm_mg 已内置到 clipwright.animation.mg，不再依赖插件加载器。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.config import logger
# 注意：不可在顶层 `from clipwright.animation.mg import list_templates` ——
# mg/__init__ 初始化时会经 generator 回导 clipwright.tool，形成循环导入。
# 改为函数内延迟导入（list_templates 在 _get_llm_mg_description 内使用）。


def _get_llm_mg_description() -> dict[str, Any]:
    """动态获取 llm_mg 能力描述（内置，始终可用，不通过插件加载器）。"""
    from clipwright.animation.mg import list_templates

    templates: list[dict] = []
    plugin_available = True  # 内置，始终可用

    try:
        templates = list_templates()
    except Exception as e:
        logger.debug("describe_llm_mg: 获取模板列表失败: %s", e)

    template_list: list[dict] = []
    for t in templates:
        template_list.append({
            "id": t.get("animation_id", ""),
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "duration_sec": t.get("duration_sec", 3.0),
        })

    return {
        "plugin_available": True,  # 内置，始终可用
        "plugin_name": "LLM Motion Graphics Generator (内置)",
        "description": (
            "llm_mg 是一个内置的 LLM 驱动动态 MG 动画生成引擎。"
            "可以从自然语言描述动态生成完整的 HTML/CSS 动画，"
            "适用于数据图表、对比图、进度条、标题特效等自定义动效。"
            "已内置到系统核心，无需插件加载。"
        ),
        "marker_format": (
            "[逻辑动画]mg_dynamic:{\"description\":\"自然语言动画描述\","
            "\"text\":\"A|B|结果\",\"style\":\"tech_dark\"}"
        ),
        "marker_usage": (
            "在场景 description 末尾添加上述标记，"
            "AnimationAgent 会在渲染时调用内置 llm_mg 引擎自动生成动画。"
        ),
        "available_templates": template_list,
        "text_animation_support": (
            "llm_mg 也支持生成文字动画效果（标题揭示、打字机、"
            "计数器等），标记格式同上。"
        ),
    }


class DescribeLLMMGTool(BaseTool):
    """描述内置 llm_mg 引擎的动态 MG 动画生成能力。"""

    name = "describe_llm_mg"
    agent_callable = True
    description = (
        "了解 LLM 动效生成 (llm_mg) 引擎的能力，包括动态 MG 动画生成、"
        "可用模板列表、标记格式。当需要为场景添加自定义动态图形动画时调用此工具。"
        "llm_mg 已内置，始终可用。"
    )

    async def execute(
        self,
        **kwargs: Any,
    ) -> ToolExecResult:
        """返回 llm_mg 引擎的能力描述和可用模板。

        Returns:
            ToolExecResult with engine capabilities and template list.
        """
        info = _get_llm_mg_description()
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output=info,
        )
