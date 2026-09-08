"""SkillRegistry — 全局技能注册表。

与 ToolRegistry 采用相同模式，统一管理所有技能的注册和分发。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.schema.skill import SkillExecResult, SkillInfo, SkillStatus
from clipwright.skill.base import BaseSkill


class SkillRegistry:
    """全局技能注册表。"""

    _instance: SkillRegistry | None = None
    _skills: dict[str, BaseSkill] = {}

    def __new__(cls) -> SkillRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, skill: BaseSkill, plugin_id: str = "", **kwargs) -> None:
        if not skill.name:
            raise ValueError(f"Skill must have a non-empty name: {type(skill).__name__}")
        cls._skills[skill.name] = skill
        # 轮 87：归属记录在 skill 对象上（与 loader 的 _plugin_id 补丁同模式，
        # 旧实现把 plugin_id 参数直接丢弃）
        setattr(skill, "_plugin_id", str(plugin_id or ""))

    @classmethod
    def unregister(cls, name: str) -> bool:
        """注销技能（插件禁用/卸载时调用）；返回是否实际移除。"""
        removed = cls._skills.pop(name, None) is not None
        return removed

    @classmethod
    def list_by_plugin(cls, plugin_id: str) -> list[SkillInfo]:
        """按插件归属列出 Skill（内置 Skill 归属为空串，不在此列）。"""
        out: list[SkillInfo] = []
        for skill in cls._skills.values():
            if str(getattr(skill, "_plugin_id", "")) == plugin_id:
                out.append(cls._get_skill_info(skill))
        return out

    @classmethod
    def owner(cls, name: str) -> str:
        """返回技能归属的 plugin_id（内置为空串）。"""
        skill = cls._skills.get(name)
        return str(getattr(skill, "_plugin_id", "")) if skill is not None else ""

    @classmethod
    def get(cls, name: str) -> Optional[BaseSkill]:
        return cls._skills.get(name)

    @classmethod
    def list(cls) -> list[SkillInfo]:
        return [cls._get_skill_info(s) for s in cls._skills.values()]

    @classmethod
    def list_available_names(cls) -> list[str]:
        return [s.name for s in cls._skills.values() if s.is_available()]

    @classmethod
    async def execute(cls, name: str, **kwargs: Any) -> SkillExecResult:
        skill = cls._skills.get(name)
        if skill is None:
            return SkillExecResult(
                status=SkillStatus.NOT_FOUND,
                skill_name=name,
                error=f"Skill '{name}' not registered",
            )
        try:
            raw = await skill.execute(**kwargs)
            if isinstance(raw, SkillExecResult):
                return raw
            return SkillExecResult(
                status=SkillStatus.SUCCESS,
                skill_name=name,
                output=raw if isinstance(raw, dict) else {"result": raw},
            )
        except Exception as e:
            return SkillExecResult(
                status=SkillStatus.ERROR,
                skill_name=name,
                error=str(e),
            )

    @classmethod
    def clear(cls) -> None:
        cls._skills.clear()

    @classmethod
    async def run_skill(cls, name: str, skill_input: dict[str, Any]) -> SkillExecResult:
        """轮 90：以 dict 形参执行技能（供 Agent 工具回调按名字分派）。"""
        skill = cls.get(name)
        if skill is None:
            return SkillExecResult(
                status=SkillStatus.NOT_FOUND,
                skill_name=name,
                error=f"Skill {name!r} is not registered",
            )
        runner = skill.execute
        return await runner(**skill_input)

    @classmethod
    def _get_skill_info(cls, skill: BaseSkill) -> SkillInfo:
        # 轮 87：填充 parameters（旧实现恒为空 → /api/skill/list 无法告知参数结构）
        params: dict = {}
        try:
            tool_schema = skill.to_llm_tool() or {}
            schema_params = tool_schema.get("parameters") or {}
            if isinstance(schema_params, dict):
                params = schema_params
        except Exception:
            params = {}
        return SkillInfo(
            name=skill.name,
            description=skill.description,
            required_tools=skill.required_tools,
            available=skill.is_available(),
            parameters=params,
            plugin_id=str(getattr(skill, "_plugin_id", "")),
        )
