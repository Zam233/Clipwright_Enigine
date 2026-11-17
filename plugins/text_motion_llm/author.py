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
                data = json.loads(content[content.find("{"):content.rfind("}") + 1])
                return MotionSpec.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"[:800]
        raise ValueError(f"MOTION_SPEC_INVALID: {last}")
