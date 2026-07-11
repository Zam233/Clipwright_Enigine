"""Skill API — 查询、执行技能。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from clipwright.schema.skill import SkillExecResult, SkillInfo
from clipwright.skill import SkillRegistry

router = APIRouter(prefix="/api/skill", tags=["skill"])


@router.get("/list", response_model=list[SkillInfo])
async def list_skills() -> list[SkillInfo]:
    """列出所有已注册的技能及其可用状态。"""
    return SkillRegistry.list()


@router.post("/execute", response_model=SkillExecResult)
async def execute_skill(name: str, params: dict[str, Any] = {}) -> SkillExecResult:
    """按名称执行技能。"""
    result = await SkillRegistry.execute(name, **params)
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail=result.error)
    return result
