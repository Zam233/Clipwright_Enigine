"""类型规范查询工具（PRD Part I F10 / A-11，只读）。

主 agent 在路线 C 下需要知道"这个类型的规范是什么"，才能判断：
- 剂量级冲突能不能走豁免；
- 同一性级冲突是不是只能换类型。

工具只读，不修改任何东西；类型不存在时返回明确的 ``found=False``（不抛异常），
因为"查不到"本身是主 agent 需要看到的信息。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class GetTypeSpecTool(BaseTool):
    """返回一个视频类型的完整规范（提示词/规则/指标/声明）。"""

    name = "get_type_spec"
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "查询视频类型规范：10 个提示词槽位（genre 等）、param_rules、signature_traits、"
        "declares（含 plan_schema）。只读；类型不存在时 found=false。"
    )

    async def execute(self, category_id: str = "", stage: str = "") -> ToolExecResult:
        """``stage`` 可选：给出该阶段应注入的文本（身份层 + 阶段层）。"""
        from clipwright.services.type_spec_runtime import load_spec

        cid = str(category_id or "").strip()
        if not cid:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                error="category_id 为空",
                output={"found": False},
            )

        spec = load_spec(cid)
        bundle = spec.prompts.normalized()
        if not bundle.slots and not spec.rules and not spec.traits:
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                output={"found": False, "category_id": cid, "reason": "类型未声明规范"},
            )

        payload: dict[str, Any] = {
            "found": True,
            "category_id": cid,
            "prompts": {key: slot.text for key, slot in bundle.slots.items()},
            "identity_text": bundle.identity_text(),
            "param_rules": [rule.model_dump() for rule in spec.rules],
            "signature_traits": [trait.model_dump() for trait in spec.traits],
            "declares": spec.declares.model_dump(),
            "strict_rules": [r.key for r in spec.rules if r.enforce == "strict"],
            "strict_traits": [t.name for t in spec.traits if t.enforce == "strict"],
        }
        if stage:
            payload["stage"] = stage
            payload["stage_text"] = spec.authority(stage=stage).stage_text()
        return ToolExecResult(status=ToolStatus.SUCCESS, output=payload)


__all__ = ["GetTypeSpecTool"]
