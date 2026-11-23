"""R10 只读复盘访谈：在 review 快照之上按 Agent 角色提问，不执行任务。"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Iterable

from clipwright.config import logger

REVIEW_ROLES = ("main", "structure", "material", "edit", "animation", "audio", "quality", "research")

REVIEW_SYSTEM = (
    "你在参加一次工具复盘访谈，不是继续创作任务。"
    "不得调用工具、不得修改任务/黑板/工作区/时间线，也不要判断任务能否继续。"
    "只根据给定的 runs、工具统计与黑板键，评价工具是否趁手、缺少什么。"
    "用中文 Markdown，分「用得不趁手」「缺少的工具」两段，总长不超过 500 字。"
)


def _role_context(review: dict[str, Any], role: str) -> str:
    runs = [
        r for r in (review.get("runs") or [])
        if (role == "main" or str(r.get("stage") or "") == role
            or str(r.get("role") or "") == role)
    ]
    return (
        f"任务 {review.get('task_id')} 当前状态：{review.get('task', {}).get('status')}\n"
        f"角色：{role}\n"
        f"相关 runs：{runs[:20]}\n"
        f"工具统计：{review.get('tools') or {}}\n"
        f"黑板键：{review.get('blackboard_keys') or []}\n"
        f"报告：{review.get('reports') or []}\n"
        "请只回答：哪些工具不趁手、缺少哪些工具。"
    )


async def interview(
    review: dict[str, Any],
    *,
    roles: Iterable[str] | None = None,
    ask: Callable[[str, str], Awaitable[str]] | None = None,
) -> dict[str, Any]:
    """按角色生成只读复盘；`ask(system, user)` 可注入测试桩。"""
    selected = [str(r) for r in (roles or REVIEW_ROLES) if str(r).strip()]
    if not selected:
        return {"read_only": True, "answers": {}}
    if ask is None:
        from clipwright.services.llm import LLMService

        svc = LLMService()

        async def _ask(system: str, user: str) -> str:
            return await svc.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                use_flash=True, max_tokens=700,
            )

        ask = _ask

    async def _one(role: str) -> tuple[str, str]:
        try:
            text = await ask(REVIEW_SYSTEM, _role_context(review, role))
            return role, str(text or "")[:4000]
        except Exception as exc:  # noqa: BLE001 — 单角色失败不影响其它
            logger.warning("复盘访谈 %s 失败: %s", role, exc)
            return role, f"（访谈失败：{type(exc).__name__}: {exc}）"

    pairs = await asyncio.gather(*[_one(r) for r in selected])
    return {
        "read_only": True,
        "task_id": str(review.get("task_id") or ""),
        "answers": dict(pairs),
        "notes": [
            "访谈只读：没有调用工具、没有派发阶段、没有写任务/黑板/工作区",
        ],
    }
