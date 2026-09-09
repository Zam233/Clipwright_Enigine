"""Skill API — 查询、执行技能。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from fastapi import APIRouter, HTTPException

from clipwright.schema.skill import SkillExecResult, SkillInfo
from clipwright.skill import SkillRegistry

router = APIRouter(prefix="/api/skill", tags=["skill"])


@router.get("/list", response_model=list[SkillInfo])
async def list_skills() -> list[SkillInfo]:
    """列出所有已注册的技能及其可用状态。"""
    return SkillRegistry.list()


class _ExecuteRequest(BaseModel):
    """P1-11: name/params 收进 body——旧签名 name 是查询参数，前端只发 body
    必 422（Field required query name）。"""
    name: str
    params: dict[str, Any] = {}


@router.post("/execute", response_model=SkillExecResult)
async def execute_skill(req: _ExecuteRequest) -> SkillExecResult:
    """按名称执行技能。"""
    name, params = req.name, req.params
    result = await SkillRegistry.execute(name, **params)
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail=result.error)
    return result
