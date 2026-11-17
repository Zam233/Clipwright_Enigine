"""text-motion-llm：在 Animation Agent 阶段把 LLM 输出转成 MotionSpec。"""

from __future__ import annotations

import json
from typing import Any

from clipwright.animation.motion_spec import MotionSpec

_SYSTEM = """你是文字动效编排器。只输出 MotionSpec JSON，不输出 HTML/JS/CSS/解释。
必须满足 motion_spec/1；文字内容不得修改；至少一个主元素有可观察运动。
keyframes 支持 opacity/translate_x/translate_y/translate_z/scale/rotate/rotate_x/rotate_y/rotate_z/blur/
letter_spacing/font_size/clip_inset；easing 支持 linear/named/cubic-bezier/steps/hold/spring。
不要使用未知字段。"""


class TextMotionAuthor:
    id = "llm_motion"
    version = "1.0.0"

    async def author(self, request) -> MotionSpec:
        from clipwright.services.llm import LLMService, MAX_OUTPUT_TOKENS

        llm = LLMService()
        user = json.dumps({
            "text": request.text, "style": request.style, "intent": request.intent,
            "canvas": {"width": request.width, "height": request.height},
            "duration_sec": request.duration_sec,
            "allowed_features": sorted(request.allowed_features),
            "caption_band_px": request.caption_band_px,
            "scene_context": request.scene_context,
        }, ensure_ascii=False)
        last = ""
        for _ in range(2):
            prompt = user if not last else f"{user}\n\n上次输出错误：{last}\n请修正并只输出 JSON。"
            resp = await llm.generate(
                messages=[{"role": "system", "content": _SYSTEM},
                          {"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=min(4096, MAX_OUTPUT_TOKENS), timeout=300,
            )
            content = getattr(resp, "content", "") or ""
            try:
                from clipwright.animation.text_generation import _extract_json
                data = _extract_json(content)
                if data is None:
                    data = json.loads(_close_json(content))
                if data is None:
                    raise ValueError("无可解析 JSON")
                data = _normalize_payload(data, request)
                return MotionSpec.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"[:800]
        raise ValueError(f"MOTION_SPEC_INVALID: {last}")


def _normalize_payload(data: dict, request) -> dict:
    """兼容模型常见别名：elements/text/at/stagger，统一到 MotionSpec。"""
    out = dict(data)
    if "schema" not in out and "version" in out:
        out["schema"] = out.pop("version")
    out.setdefault("schema", "motion_spec/1")
    out.setdefault("duration_sec", float(request.duration_sec))
    layers = out.pop("layers", None) or out.pop("elements", None) or []
    norm = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        item = dict(layer)
        if "content" not in item and isinstance(item.get("text"), str):
            item["content"] = item.pop("text")
        elif "text" in item:
            item.pop("text", None)
        if "layout" not in item and isinstance(item.get("position"), dict):
            item["layout"] = item.pop("position")
        if "stagger" in item and "text_motion" not in item:
            st = item.pop("stagger") or {}
            item["text_motion"] = {
                "unit": st.get("unit", "char"),
                "stagger": float(st.get("delay_sec", st.get("stagger", 0)) or 0),
            }
        kfs = []
        for kf in item.get("keyframes") or []:
            if not isinstance(kf, dict):
                continue
            k = dict(kf)
            if "time" not in k and "at" in k:
                k["time"] = k.pop("at")
            if "at" in k:
                k.pop("at", None)
            kfs.append(k)
        item["keyframes"] = kfs
        norm.append(item)
    out["layers"] = norm
    return out


def _close_json(content: str) -> str:
    """模型偶尔少输出最后几个闭合括号：按未闭合栈补齐。"""
    s = content[content.find("{"):]
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
    if in_str:
        s += '"'
    for ch in reversed(stack):
        s += "]" if ch == "[" else "}"
    return s
