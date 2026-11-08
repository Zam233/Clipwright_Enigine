"""用户创作提示词（PRD §II.12 R2 / F-04 落点）。

创作页把用户写的提示词与"不要什么"随任务下发（``extra_params.creative_prompt`` /
``creative_avoid``）。本模块把它渲染成可注入的文本块，并明确**优先级高于类型默认风格**
（PRD §I.3.2 的 L1 > L2）。

只做纯函数，不调 LLM、不碰管线状态。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

#: 提示词长度上限（与 D3 / 前端 textarea 一致）。
MAX_PROMPT_CHARS = 4000
#: avoid 条数上限（与后端蒸馏一致）。
MAX_AVOID_ITEMS = 6


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def resolve_creative_prompt(
    extra_params: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """取 ``(prompt, avoid)``；缺失返回 ``("", [])``（零行为变化）。"""
    extra = extra_params or {}
    prompt = _clean(extra.get("creative_prompt"))[:MAX_PROMPT_CHARS]
    raw_avoid = extra.get("creative_avoid") or []
    if isinstance(raw_avoid, str):
        raw_avoid = [raw_avoid]
    avoid: list[str] = []
    for item in raw_avoid:
        text = _clean(item)
        if text and text not in avoid and len(avoid) < MAX_AVOID_ITEMS:
            avoid.append(text)
    return prompt, avoid


def has_creative_prompt(extra_params: dict[str, Any] | None) -> bool:
    return bool(resolve_creative_prompt(extra_params)[0])


def creative_prompt_block(extra_params: dict[str, Any] | None) -> str:
    """渲染注入块；没有提示词时返回空串（不留空段）。"""
    prompt, avoid = resolve_creative_prompt(extra_params)
    if not prompt and not avoid:
        return ""
    lines = ["## 用户创作提示词（优先级高于类型默认风格）", ""]
    if prompt:
        lines.append(prompt)
        lines.append("")
    if avoid:
        lines.append("用户明确不要的（必须遵守，不得出现）：")
        lines.extend(f"- {item}" for item in avoid)
        lines.append("")
    lines.append(
        "冲突处理：与类型规范重叠处按本段执行；但若与平台硬底线冲突，以硬底线为准。"
    )
    return "\n".join(lines)


def merge_into_prompts(base_text: str, extra_params: dict[str, Any] | None) -> str:
    """把创作提示词拼到既有文本之后（无提示词时原样返回）。"""
    block = creative_prompt_block(extra_params)
    if not block:
        return base_text
    if not base_text.strip():
        return block
    return f"{base_text.rstrip()}\n\n{block}"


__all__ = [
    "MAX_AVOID_ITEMS",
    "MAX_PROMPT_CHARS",
    "creative_prompt_block",
    "has_creative_prompt",
    "merge_into_prompts",
    "resolve_creative_prompt",
]
