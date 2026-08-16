"""鉴权与数据所有权辅助（P3-3B）。

语义约定：
- 中间件在请求进入路由前把身份写入 request.state（user_id / user_role）；
- off/token 模式下 user_id 恒为 None（None = 管理员/旧行为，不做所有权过滤）；
- jwt 模式下 user_id 为 Server 账号 ID，路由按 owner_id 过滤与校验。
"""

from __future__ import annotations

from fastapi import HTTPException, Request


def current_user_id(request: Request) -> str | None:
    """当前请求的用户 ID；None 表示管理员语义（off/token 模式或运维令牌）。"""
    return getattr(request.state, "user_id", None)


def current_user_role(request: Request) -> str | None:
    return getattr(request.state, "user_role", None)


def is_admin(request: Request) -> bool:
    return current_user_role(request) == "admin"


def enforce_owner(request: Request, owner_id: str | None, kind: str) -> None:
    """jwt 模式下校验资源所有权；off/token 模式（user_id 为 None）一律放行。"""
    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return
    if owner_id and owner_id == uid:
        return
    raise HTTPException(status_code=403, detail=f"无权访问该{kind}（不属于当前账号）")


def filter_by_owner(request: Request, items: list[dict], key: str = "owner_id") -> list[dict]:
    """jwt 模式下仅保留当前账号的数据；off/token 模式（无 user_id）返回全部。

    安全策略：jwt 模式下 owner_id 为空/缺失的遗留数据一律隐藏（避免越权可见），
    由管理员（admin role 或 off/token 模式）负责迁移/接管。
    """
    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return items
    return [it for it in items if it.get(key) == uid]
