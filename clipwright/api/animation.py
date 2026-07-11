"""动画 API — 查询动画定义和编排序列。"""

from __future__ import annotations

from fastapi import APIRouter

from clipwright.animation import AnimationRegistry
from clipwright.schema.animation import AnimationDef, AnimationType

router = APIRouter(prefix="/api/animation", tags=["animation"])


@router.get("/list", response_model=list[AnimationDef])
async def list_animations(anim_type: str = "") -> list[AnimationDef]:
    """列出所有或指定类型的动画定义。"""
    if anim_type:
        try:
            t = AnimationType(anim_type)
            return AnimationRegistry.list(t)
        except ValueError:
            pass
    return AnimationRegistry.list()


@router.get("/onscreen", response_model=list[AnimationDef])
async def list_onscreen() -> list[AnimationDef]:
    """列出所有屏幕动画。"""
    return AnimationRegistry.list(AnimationType.ONSCREEN)


@router.get("/transitions", response_model=list[AnimationDef])
async def list_transitions() -> list[AnimationDef]:
    """列出所有转场动画。"""
    return AnimationRegistry.list(AnimationType.TRANSITION)


@router.get("/get/{animation_id}", response_model=AnimationDef)
async def get_animation(animation_id: str) -> AnimationDef:
    """获取单个动画定义的详细信息。"""
    defn = AnimationRegistry.get(animation_id)
    if defn is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Animation '{animation_id}' not found")
    return defn
