"""类型提示词注入（PRD F1/F4，验收 V1/V15）。

类型的 10 个槽位（3 身份 + 7 阶段）要在**对应阶段真的进到提示词里**，而不是只存在于
配置与工具查询里。本模块只做三件事：

1. 把身份层渲染成一段固定序（``genre`` → ``anti_patterns`` → ``lexicon``）的文本；
2. 把某一阶段的槽位渲染成文本；
3. 以**固定顺序**（原文 → 身份 → 阶段）拼成最终 system prompt。

固定顺序是 V15 的要求：同一类型、同一阶段，注入位置必须可预期，否则模型行为
"看起来一样、实际不同"就没法归因。没有槽位时原样返回，零行为变化。
"""

from __future__ import annotations

from typing import Any, Iterable

from clipwright.services.type_spec_runtime import load_spec

#: 身份层标题（含"优先于参数"的硬约束，避免模型把类型当成装饰词）。
IDENTITY_HEADER = "## 视频类型身份（决定这是什么片子，优先于任何数字参数）"
STAGE_HEADER_TPL = "## 本阶段类型规范（{stage}）"


def identity_block(category_id: str) -> str:
    """身份层文本；类型不存在或未声明时返回空串。"""
    if not category_id:
        return ""
    spec = load_spec(category_id)
    text = spec.prompts.identity_text().strip()
    if not text:
        return ""
    return f"{IDENTITY_HEADER}\n{text}"


def stage_block(category_id: str, stage: str) -> str:
    """单阶段槽位文本；未声明时返回空串。"""
    if not category_id or not stage:
        return ""
    spec = load_spec(category_id)
    text = spec.prompts.stage_text(stage).strip()
    if not text:
        return ""
    return f"{STAGE_HEADER_TPL.format(stage=stage)}\n{text}"


def stage_block_many(category_id: str, stages: Iterable[str]) -> str:
    """多阶段槽位（如音频阶段同时管字幕）；按传入顺序拼接，跳过空段。"""
    blocks = [stage_block(category_id, stage) for stage in stages]
    return "\n\n".join(block for block in blocks if block)


def compose(base: str, category_id: str, stage: str = "") -> str:
    """固定顺序拼接：``base`` → 身份层 → 本阶段规范。

    三块都为空时返回原 ``base``（保持"未声明类型的插件零行为变化"）。
    """
    parts: list[str] = []
    if (base or "").strip():
        parts.append(base.strip())
    identity = identity_block(category_id)
    if identity:
        parts.append(identity)
    stage_text_block = stage_block(category_id, stage)
    if stage_text_block:
        parts.append(stage_text_block)
    return "\n\n".join(parts) + ("\n" if parts else "")


def compose_many(base: str, category_id: str, stages: Iterable[str]) -> str:
    """同 :func:`compose`，但可注入多个阶段槽位（音频 + 字幕）。"""
    parts: list[str] = []
    if (base or "").strip():
        parts.append(base.strip())
    identity = identity_block(category_id)
    if identity:
        parts.append(identity)
    many = stage_block_many(category_id, stages)
    if many:
        parts.append(many)
    return "\n\n".join(parts) + ("\n" if parts else "")


def first_line(category_id: str, stage: str) -> str:
    """取某阶段槽位的第一句（用于把自然语言规范塞进检索式这类短文本场景）。"""
    block = stage_block(category_id, stage)
    if not block:
        return ""
    body = block.split("\n", 1)[1] if "\n" in block else ""
    for line in body.splitlines():
        line = line.strip().lstrip("-").strip()
        if line:
            return line
    return ""


def stage_summary(category_id: str) -> dict[str, Any]:
    """某个类型"哪些阶段有槽位"的概览（诊断用，不参与注入）。"""
    spec = load_spec(category_id or "")
    slots = spec.prompts.normalized().slots
    return {
        "category_id": category_id,
        "identity": [key for key in ("genre", "anti_patterns", "lexicon") if key in slots],
        "stages": [
            key
            for key in ("structure", "material", "edit", "animation", "audio", "subtitle", "quality")
            if key in slots
        ],
        "total_chars": sum(len(slot.effective_text()) for slot in slots.values()),
    }


__all__ = [
    "IDENTITY_HEADER",
    "STAGE_HEADER_TPL",
    "compose",
    "compose_many",
    "first_line",
    "identity_block",
    "stage_block",
    "stage_block_many",
    "stage_summary",
]
