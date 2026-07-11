"""Skill 系统 — 可组合的高级能力。

Skill 是比 Tool 更高层级的可组合能力：
- Tool: 原子操作（如 scene_detect、audio_extract），调用外部命令
- Skill: 编排多个 Tool + 自有逻辑，完成一个业务目标
- Plugin: 可同时注册 Tool 和 Skill 的分发单元

使用方法：
    from clipwright.skill import SkillRegistry, BaseSkill
    SkillRegistry.register(MySkill())
    result = await SkillRegistry.execute("my_skill", **params)
"""

from clipwright.skill.base import BaseSkill
from clipwright.skill.builtin import register_builtin_skills
from clipwright.skill.registry import SkillRegistry

__all__ = [
    "BaseSkill",
    "SkillRegistry",
    "register_builtin_skills",
]
