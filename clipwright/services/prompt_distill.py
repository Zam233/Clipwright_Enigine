"""MV 提示词对话（PRD §II.12 / F-05 的后端部分）。

现有 ChatForge 的 ``/commit`` 产出的是 **PersonaManifest**（磨人设）。MV 创作页需要的是
另一个方向：**把一段对话蒸馏成"视频提示词"**——`prompt`（要什么）+ `avoid`（不要什么）。

本模块只做"对话 → 提示词"的确定性收口：

- 有 LLM 时：让模型输出 `{prompt, avoid, rationale}`（结构化），并用规则兜底清洗；
- 无 LLM / 失败时：**从用户消息里抽取**（去重、截断、去掉寒暄），仍返回可用结果并标
  ``degraded=True``——不允许悄悄返回空提示词。

入参是消息列表（``role/content``），不依赖会话存储，便于单测与前端直连。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

#: 单条提示词上限（与 D3 的 4000 字符一致）。
MAX_PROMPT_CHARS = 4000
#: avoid 条数上限（防刷屏）。
MAX_AVOID_ITEMS = 6

_GREETING_RE = re.compile(r"^(你好|您好|hi|hello|在吗|谢谢|thanks)[!！。.,，\s]*$", re.IGNORECASE)
_NEGATION_HINTS = ("不要", "别", "禁止", "避免", "不许", "不要用", "don't", "avoid", "no ")


class PromptDraft(BaseModel):
    """蒸馏结果。"""

    prompt: str = ""
    avoid: list[str] = Field(default_factory=list)
    rationale: str = ""
    degraded: bool = False
    source_messages: int = 0


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _split_avoid(line: str) -> Optional[str]:
    for hint in _NEGATION_HINTS:
        index = line.lower().find(hint)
        if index >= 0:
            candidate = _clean(line[index + len(hint):]).lstrip("：:，,、 ")
            if candidate:
                return candidate
    return None


def distill_from_messages(messages: Iterable[dict[str, Any]]) -> PromptDraft:
    """规则蒸馏：用户消息去重拼接为 prompt；含否定表达的句子进 avoid。"""
    seen: set[str] = set()
    parts: list[str] = []
    avoid: list[str] = []
    count = 0

    for message in messages or []:
        if str(message.get("role") or "user") != "user":
            continue
        text = _clean(str(message.get("content") or ""))
        if not text or _GREETING_RE.match(text):
            continue
        count += 1
        for sentence in re.split(r"[。；;！!\n]", text):
            sentence = _clean(sentence)
            if not sentence:
                continue
            negation = _split_avoid(sentence)
            if negation:
                if negation not in avoid and len(avoid) < MAX_AVOID_ITEMS:
                    avoid.append(negation)
                continue
            if sentence not in seen:
                seen.add(sentence)
                parts.append(sentence)

    prompt = "；".join(parts)[:MAX_PROMPT_CHARS]
    return PromptDraft(
        prompt=prompt,
        avoid=avoid,
        rationale="规则蒸馏（未调用 LLM）",
        degraded=True,
        source_messages=count,
    )


def normalize_draft(payload: dict[str, Any], *, source_messages: int = 0) -> PromptDraft:
    """把 LLM 的结构化输出收口成 ``PromptDraft``（字段缺失时降级）。"""
    prompt = _clean(str(payload.get("prompt") or ""))[:MAX_PROMPT_CHARS]
    raw_avoid = payload.get("avoid") or []
    if isinstance(raw_avoid, str):
        raw_avoid = [raw_avoid]
    avoid = []
    for item in raw_avoid:
        text = _clean(str(item))
        if text and text not in avoid and len(avoid) < MAX_AVOID_ITEMS:
            avoid.append(text)
    return PromptDraft(
        prompt=prompt,
        avoid=avoid,
        rationale=_clean(str(payload.get("rationale") or "")),
        degraded=not bool(prompt),
        source_messages=source_messages,
    )


DISTILL_SYSTEM = """你是视频创作提示词助手。把用户与你的对话蒸馏成一段**视频提示词**。

要求：
1. `prompt` 只写"要什么"：风格、节奏、画面类型、叙事结构、必须出现/不能出现的元素；
   不要写寒暄、不要复述对话、不要写"用户说"。
2. `avoid` 写"不要什么"（红线与易犯错误），最多 6 条，每条一句短句。
3. `rationale` 用一句话说明你的取舍依据。
4. 全部输出中文，`prompt` 不超过 2000 字。

输出纯 JSON：{"prompt": "...", "avoid": ["..."], "rationale": "..."}
"""


async def distill_with_llm(
    messages: Iterable[dict[str, Any]],
    *,
    llm: Any = None,
    pipeline_id: str = "chat_forge_prompt",
) -> PromptDraft:
    """优先用 LLM 蒸馏；失败/未配置时退回规则蒸馏（``degraded=True``）。"""
    messages = list(messages or [])
    fallback = distill_from_messages(messages)
    if llm is None:
        return fallback
    transcript = "\n".join(
        f"{message.get('role', 'user')}: {_clean(str(message.get('content') or ''))}"
        for message in messages
        if _clean(str(message.get("content") or ""))
    )
    if not transcript:
        return fallback
    try:
        payload = await llm.structured_output(
            system_prompt=DISTILL_SYSTEM,
            user_prompt=transcript[:6000],
            pipeline_id=pipeline_id,
        )
    except Exception:  # noqa: BLE001 - 蒸馏失败不得阻断创作流程
        return fallback
    if not isinstance(payload, dict):
        return fallback
    draft = normalize_draft(payload, source_messages=fallback.source_messages)
    return draft if draft.prompt else fallback


__all__ = [
    "DISTILL_SYSTEM",
    "MAX_AVOID_ITEMS",
    "MAX_PROMPT_CHARS",
    "PromptDraft",
    "distill_from_messages",
    "distill_with_llm",
    "normalize_draft",
]
