"""MG 动画降级策略 — 当 LLM 生成失败时匹配已有模板。"""

from __future__ import annotations

import copy
from typing import Any


class FallbackEngine:
    """降级引擎：语义描述 → 已有模板匹配 → 参数填充。"""

    KEYWORD_TEMPLATE_MAP: dict[str, str] = {
        "对比|vs|比较|pk|差异": "mg_comparison_split",
        "标题|title|reveal|揭示|开头": "mg_title_reveal",
        "进度|progress|完成|percent|百分比": "mg_progress_bar",
        "数字|count|计数|增长|统计|counter": "mg_counter_up",
        "标签|badge|徽章|标注|callout|提示": "mg_callout_badge",
    }

    @classmethod
    def find_best_template(
        cls, description: str, templates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """根据描述匹配最合适的模板。"""
        if not templates:
            return None

        desc_lower = description.lower()
        scores: dict[str, float] = {}

        for t in templates:
            tid = t.get("animation_id", "")
            score = 0.0

            for pattern, template_id in cls.KEYWORD_TEMPLATE_MAP.items():
                if tid == template_id:
                    for kw in pattern.split("|"):
                        if kw.lower() in desc_lower:
                            score += 2.0
                            break

            scores[tid] = score

        if scores:
            best_id = max(scores, key=scores.get)
            if scores[best_id] > 0:
                for t in templates:
                    if t.get("animation_id") == best_id:
                        return t

        for t in templates:
            if t.get("animation_id") == "mg_comparison_split":
                return t

        return templates[0] if templates else None

    @classmethod
    def extract_keywords(cls, text: str) -> list[str]:
        """从文本中提取 | 分隔的关键信息段。"""
        parts = [p.strip() for p in text.replace("→", "|").split("|") if p.strip()]
        return parts

    @classmethod
    def fill_template_params(
        cls, template: dict[str, Any], text_content: str, persona_style: dict | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """用提取的关键词填充模板参数。"""
        parts = cls.extract_keywords(text_content)
        params: dict[str, str] = {}

        param_defs = template.get("params", {})
        param_keys = list(param_defs.keys())

        for i, key in enumerate(param_keys):
            if i < len(parts):
                params[key] = parts[i]
            else:
                params[key] = param_defs[key].get("default", "") if isinstance(param_defs[key], dict) else ""

        if len(params) == 0 and parts:
            params["text"] = parts[0]

        style = persona_style or {}
        if "primary_color" in style and "accent" in params:
            # Persona 主色覆盖默认 accent
            params["accent"] = style["primary_color"]

        return template, params
