"""独立风险判定（PRD F10 审核门 / F11 命令风险，开发计划 B8/B9）。

**独立性是可检验的**，不是形容词：

1. 输入隔离：请求体只含被判定对象（WorkerSpec 或命令），**不含**主代理的对话、
   理由与账本上下文（PRD A23/A24）；
2. 独立模型：默认走 `model_router.risk_judge_route()`（与主代理解耦，共用
   provider 白名单）；
3. 失败 fail-safe：超时 / 非法 JSON / 字段缺失一律按 `high` 处理（PRD A26）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from clipwright.config import logger, settings

RISK_LEVELS: tuple[str, ...] = ("low", "med", "high", "critical")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "risk": {"type": "string", "enum": list(RISK_LEVELS)},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "suggested_action": {"type": "string"},
    },
    "required": ["risk", "reasons"],
}

_SYSTEM = (
    "你是安全审查器。只根据给定对象本身的字段评估风险，"
    "不参考任何调用方的解释或辩解。输出 risk(低/中/高/极高)、reasons（简短理由）、"
    "suggested_action。若信息不足以判断，按 high 处理。"
)


def _fail_safe(reason: str) -> dict[str, Any]:
    return {
        "risk": "high",
        "reasons": [reason],
        "suggested_action": "人工确认",
        "source": "fallback",
    }


#: 判定输入白名单——只有这些键允许进入请求体（A23/A24 的可检验形式）
_INPUT_KEYS: frozenset[str] = frozenset({
    "role", "system_prompt", "tools", "timeout_sec", "max_turns",
    "command", "cwd", "workspace",
})


async def assess(spec_blob: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    """对任意规格对象做独立风险判定（B8 用于 worker，B9 用于命令）。

    **输入隔离**：只取白名单内的字段，调用方塞进来的解释/对话一律丢弃。
    """
    if not spec_blob:
        return _fail_safe("判定输入为空")

    payload = {k: spec_blob[k] for k in sorted(spec_blob) if k in _INPUT_KEYS}
    if not payload:
        return _fail_safe("判定输入不含可判定字段")
    user = "## 待判定对象\n" + "\n".join(
        f"- {k}: {str(v)[:800]}" for k, v in payload.items()
    )

    try:
        from clipwright.services.llm import LLMService
        from clipwright.services.model_router import risk_judge_route, use_route

        route = risk_judge_route()
        svc = LLMService()
        with use_route(route):
            data = await asyncio.wait_for(
                svc.structured_output(
                    system_prompt=_SYSTEM,
                    user_prompt=user,
                    output_schema=_SCHEMA,
                    use_flash=route is None,
                    pipeline_id="",
                ),
                timeout=timeout or float(
                    getattr(settings, "supervisor_attribution_timeout_sec", 15.0)
                ),
            )
        risk = str((data or {}).get("risk", "")).lower()
        if risk not in RISK_LEVELS:
            return _fail_safe("判定输出不合 schema")
        reasons = [str(r)[:200] for r in ((data or {}).get("reasons") or []) if str(r).strip()]
        if not reasons:
            return _fail_safe("判定未给出理由")
        return {
            "risk": risk,
            "reasons": reasons[:5],
            "suggested_action": str((data or {}).get("suggested_action", ""))[:200],
            "source": "llm",
            "route": route.key if route else "",
            "input_keys": sorted(payload),
        }
    except Exception as e:  # noqa: BLE001 — 判定失败一律升 high（fail-safe）
        logger.warning("独立风险判定失败（按 high 处理）: %s", e)
        return _fail_safe(f"判定失败: {type(e).__name__}")


def requires_confirm(assessment: dict[str, Any]) -> bool:
    """risk=high/critical 时需要二次确认（PRD A22）。"""
    return str(assessment.get("risk", "")).lower() in ("high", "critical")
