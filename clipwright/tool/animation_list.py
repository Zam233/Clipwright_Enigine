"""动画目录查询工具 — 供 Agent 按需获取可用动画类型列表。

替代 prompt 注入方案：Agent 在需要选择动画时调用此工具，
避免在 system prompt 中列出所有动画类型（防止随插件增多而 prompt 膨胀）。
"""

from __future__ import annotations

from typing import Any

from clipwright.animation.catalog import AnimationCatalog
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class ListAnimationsTool(BaseTool):
    """列出当前可用的动画类型（文字动画 / 逻辑动画 / 过渡动画 / MG 动画）。"""

    name = "list_animations"
    agent_callable = True
    description = (
        "列出所有可用的动画类型和标记格式。"
        "用于在场景 description 中标注 [文字动画]xxx / [逻辑动画]xxx / [过渡动画]xxx 标记时，"
        "查询当前有哪些动画可供选择。"
        "返回每个动画的 id、名称、简要描述和标记用法。"
    )

    async def execute(
        self,
        category: str = "all",
        top_k: int = 10,
        **kwargs: Any,
    ) -> ToolExecResult:
        """返回可用动画列表。

        Args:
            category: 动画类别 — "text" / "logic" / "transition" / "mg" / "all"
            top_k: 每个类别最多返回几个（0 = 全部）
        """
        results: dict[str, list[dict]] = {}

        if category in ("all", "text"):
            anims = AnimationCatalog.get_text_animations()
            results["text_animations"] = [
                {"id": a["id"], "name": a.get("name", ""), "desc": a.get("desc", ""),
                 "usage": f"[文字动画]{a.get('name', '')}：要显示的文字"}
                for a in (anims[:top_k] if top_k > 0 else anims)
            ]

        if category in ("all", "logic"):
            anims = AnimationCatalog.get_logic_animations()
            results["logic_animations"] = [
                {"id": a["id"], "name": a.get("name", ""), "desc": a.get("desc", ""),
                 "usage": f"[逻辑动画]{a.get('name', '')}：要展示的概念"}
                for a in (anims[:top_k] if top_k > 0 else anims)
            ]

        if category in ("all", "transition"):
            anims = AnimationCatalog.get_transition_animations()
            results["transition_animations"] = [
                {"id": a["id"], "name": a.get("name", ""), "desc": a.get("desc", ""),
                 "usage": f"[过渡动画]{a.get('name', '')}"}
                for a in (anims[:top_k] if top_k > 0 else anims)
            ]

        total = sum(len(v) for v in results.values())
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={
                "categories": results,
                "total_available": total,
                "hint": "在场景 description 中用 [类型]名称：文本 格式标记动画",
            },
        )
