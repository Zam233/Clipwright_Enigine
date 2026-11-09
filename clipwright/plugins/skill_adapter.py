"""subprocess 插件 Skill 的宿主适配（M4）。

Skill 的元数据（name/description/required_tools/LLM schema）由插件声明，
执行经 `skill.call` RPC 转发到 worker；engine 不执行插件代码。
"""
from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.skill.base import BaseSkill


class RemoteSkill(BaseSkill):
    """把 subprocess 插件声明的 Skill 呈现为宿主技能。"""

    def __init__(self, host: Any, plugin_id: str, skill_id: str,
                 meta: dict[str, Any]) -> None:
        self._host = host
        self._plugin_id = str(plugin_id)
        self._skill_id = str(skill_id)
        self.name = str(meta.get("name") or skill_id)
        self.description = str(meta.get("description") or "")
        self.required_tools = list(meta.get("required_tools") or [])
        self._llm_schema = dict(meta.get("llm_schema") or {})

    async def execute(self, **kwargs: Any) -> SkillExecResult:
        if self._host is None or not getattr(self._host, "alive", False):
            return SkillExecResult(
                status=SkillStatus.ERROR, skill_name=self.name,
                output={"error": "插件未运行"},
            )
        try:
            raw = await self._host.invoke(
                "skill.call", {"id": self._skill_id, "input": kwargs},
            )
        except Exception as e:  # noqa: BLE001 — 失败必须回成结果而不是抛穿
            logger.warning("远程 Skill 调用失败 %s（%s）: %s",
                           self.name, self._plugin_id, e)
            return SkillExecResult(
                status=SkillStatus.ERROR, skill_name=self.name,
                output={"error": str(e)},
            )
        if isinstance(raw, dict) and raw.get("success") is False:
            return SkillExecResult(
                status=SkillStatus.ERROR, skill_name=self.name,
                output={"error": raw.get("error") or "技能执行失败"},
            )
        output = raw.get("output") if isinstance(raw, dict) else raw
        return SkillExecResult(
            status=SkillStatus.SUCCESS, skill_name=self.name,
            output=output if isinstance(output, dict) else {"result": output},
        )

    def is_available(self) -> bool:
        return self._host is not None and bool(getattr(self._host, "alive", False))

    def to_llm_tool(self, fmt: str = "anthropic") -> dict[str, Any]:
        if self._llm_schema:
            name = str(self._llm_schema.get("name") or self.name)
            return {**self._llm_schema, "name": name}
        return super().to_llm_tool(fmt)


__all__ = ["RemoteSkill"]
