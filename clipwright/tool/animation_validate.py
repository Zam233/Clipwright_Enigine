"""R4：MG 单段前置校验（颜色/关键帧/时长）。"""

from __future__ import annotations

import re
from typing import Any

from clipwright.animation.mg.validator import validate_mg_json
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool

_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)|transparent|none|"
    r"linear-gradient\(.+\)|radial-gradient\(.+\))$"
)


def _bad_color(value: Any) -> bool:
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return True
    text = value.strip()
    if not text:
        return True
    # 允许模板占位符（{color}），渲染前由 params 替换
    if text.startswith("{") and text.endswith("}"):
        return False
    return not bool(_COLOR_RE.match(text))


def color_issues(mg_def: dict[str, Any], timeline_clip: dict[str, Any] | None = None) -> list[str]:
    issues: list[str] = []
    for i, elem in enumerate(mg_def.get("elements") or [] if isinstance(mg_def, dict) else []):
        if not isinstance(elem, dict):
            continue
        for key in ("color", "font_color", "background", "border_color", "glow_color"):
            if key in elem and _bad_color(elem.get(key)):
                issues.append(f"elements[{i}].{key} 颜色为空或格式不合法")
        for j, kf in enumerate(elem.get("keyframes") or []):
            if isinstance(kf, dict) and "color" in kf and _bad_color(kf.get("color")):
                issues.append(f"elements[{i}].keyframes[{j}].color 颜色不合法")
    if isinstance(timeline_clip, dict) and _bad_color(timeline_clip.get("glow_color", "transparent")):
        issues.append("timeline_clip.glow_color 为空或格式不合法")
    return issues


class ValidateAnimationClipTool(BaseTool):
    name = "validate_animation_clip"
    capability = "validate_animation_clip"
    permission_tier = "read"
    agent_callable = True
    dry_run_capable = True
    dependencies: list[str] = []
    description = "校验 MG 动画：颜色字段非空/格式、关键帧、时长、元素 schema；返回可读 issues。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "mg_def": {"type": "object"},
            "duration_sec": {"type": "number"},
            "timeline_clip": {"type": "object"},
        },
        "required": ["mg_def"],
    }

    async def execute(
        self, *, mg_def: dict[str, Any] | None = None,
        duration_sec: float = 0.0, timeline_clip: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        payload = dict(mg_def or {})
        issues: list[str] = []
        if duration_sec and not payload.get("duration_sec"):
            payload["duration_sec"] = float(duration_sec)
        ok, errors = validate_mg_json(payload)
        issues.extend(errors)
        for i, elem in enumerate(payload.get("elements") or []):
            if not isinstance(elem, dict):
                continue
            for key in ("color", "font_color", "background", "border_color", "glow_color"):
                if key in elem and _bad_color(elem.get(key)):
                    issues.append(f"elements[{i}].{key} 颜色为空或格式不合法")
            for j, kf in enumerate(elem.get("keyframes") or []):
                if isinstance(kf, dict) and "color" in kf and _bad_color(kf.get("color")):
                    issues.append(f"elements[{i}].keyframes[{j}].color 颜色不合法")
        if isinstance(timeline_clip, dict) and _bad_color(timeline_clip.get("glow_color", "transparent")):
            issues.append("timeline_clip.glow_color 为空或格式不合法")
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={"ok": ok and not issues, "issues": issues[:30],
                    "duration_sec": payload.get("duration_sec", 0)},
        )
