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
    def register(cls, skill: BaseSkill, **kwargs) -> None:
        if not skill.name:
            raise ValueError(f"Skill must have a non-empty name: {type(skill).__name__}")
        cls._skills[skill.name] = skill

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
    def _get_skill_info(cls, skill: BaseSkill) -> SkillInfo:
        return SkillInfo(
            name=skill.name,
            description=skill.description,
            required_tools=skill.required_tools,
            available=skill.is_available(),
        )
