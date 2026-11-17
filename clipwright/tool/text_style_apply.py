"""apply_text_style —— 把完整文字样式/动效写入任务时间线的文字 clip。

这是 PRD §14.5 要求的“样式工具”：LLM 不再只能调用 25 个独立 mp4 工具，
而是能声明 style + motion，直接改文字层（clip.metadata.style / motion），
由 ASS/预览渲染器消费。

限制：
- 只改已存在的 text clip，不凭空新增（新增仍走 edit/insert 工具）；
- 时间线 owner 必须是 animation 或更上游；audio 定稿后拒绝覆盖；
- 文本内容不在这里改，避免误改字幕。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.timeline import Timeline
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.services import task_blackboard as bb
from clipwright.tool.base import BaseTool


def _hex(value: Any, fallback: str = "#ffffff") -> str:
    s = str(value or "").strip()
    if s.startswith("#") and len(s) in (4, 7, 9):
        if len(s) == 4:
            return "#" + "".join(ch * 2 for ch in s[1:]) + "ff"
        return s if len(s) == 9 else s + "ff"
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([0-9.]+))?\s*\)", s)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return f"#{r:02x}{g:02x}{b:02x}{int(max(0, min(1, a)) * 255):02x}"
    return fallback


def _load_timeline(task_id: str) -> tuple[dict[str, Any], Timeline]:
    board = bb.get(task_id)
    for key in ("timeline", "final_timeline"):
        raw = board.get(key)
        if not raw:
            continue
        if isinstance(raw, dict) and str(raw.get("path") or ""):
            data = json.loads(Path(str(raw["path"])).read_text(encoding="utf-8"))
        elif isinstance(raw, dict) and raw.get("tracks"):
            data = raw
        else:
            continue
        return board, Timeline(**data)
    raise ValueError(f"任务 {task_id} 黑板里没有 timeline（先跑 edit）")


class ApplyTextStyleTool(BaseTool):
    name = "apply_text_style"
    capability = "text_style"
    permission_tier = "write_local"
    agent_callable = True
    description = (
        "给任务时间线中已存在的文字 clip 应用完整样式/动效："
        "fill/box/box_radius/box_padding/stroke/shadow/glow/text_align/offset/"
        "motion(typewriter/slide_in/scale_in/highlight 等)/duration_reason。"
        "写入 clip.metadata.style + motion，由 ASS/预览消费；不是生成 mp4。"
    )

    async def execute(
        self,
        task_id: str,
        clip_id: str,
        *,
        style: Optional[dict[str, Any]] = None,
        motion: Optional[dict[str, Any]] = None,
        duration_sec: Optional[float] = None,
        duration_reason: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        from clipwright.animation.text_style import normalize_motion, normalize_style

        try:
            board, timeline = _load_timeline(str(task_id))
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))

        target = None
        for track in timeline.tracks:
            for clip in track.clips:
                if str(clip.id) == str(clip_id):
                    target = clip
                    break
            if target is not None:
                break
        if target is None:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"clip 不存在: {clip_id}")
        if str(target.kind) not in ("text", "caption"):
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"apply_text_style 只作用于 text/caption，当前 {target.kind}")

        raw_style = dict(style or (target.metadata or {}).get("style") or {})
        st = normalize_style(raw_style)
        text = str(target.text or "")
        mo = normalize_motion(motion or (target.metadata or {}).get("motion") or {}, text=text)
        if not duration_reason:
            duration_reason = str((target.metadata or {}).get("duration_reason") or "")

        target.font_size = float(st["font_size"])
        _fill = st.get("fill")
        if isinstance(_fill, dict):
            _fill = ((_fill.get("colors") or ["#ffffff"])[0])
        target.font_color = _hex(_fill)
        target.font_weight = str(st["font_weight"])
        target.font_italic = bool(st["font_italic"])
        target.letter_spacing = float(st["letter_spacing"])
        target.text_align = str(st["text_align"])
        target.stroke_width = float(st["stroke_width"])
        target.stroke_color = _hex(st["stroke_color"])
        target.shadow_x = float(st["shadow_x"])
        target.shadow_y = float(st["shadow_y"])
        target.shadow_blur = float(st["shadow_blur"])
        target.shadow_color = _hex(st["shadow_color"])
        if st.get("glow_color"):
            target.glow_color = _hex(st["glow_color"])
            target.glow_width = float(st["glow_width"])
        if duration_sec:
            target.duration_sec = max(0.2, float(duration_sec))

        meta = dict(target.metadata or {})
        meta["style"] = st
        meta["motion"] = mo
        meta["text_animation"] = {
            "style": st, "motion": mo, "display_sec": float(target.duration_sec),
            "duration_reason": duration_reason,
        }
        meta["anim_type"] = str(mo["type"])
        meta["duration_reason"] = duration_reason
        # 所有文字动画统一走 engine + motion provider；工具不再直接构建 MG。
        try:
            from clipwright.animation.text_engine import get_engine
            target = get_engine().apply_style(
                target, style=st, motion=mo,
                width=int(timeline.width or 1920),
                height=int(timeline.height or 1080),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"重建 MG HTML 失败: {exc}")


        try:
            bb.put(str(task_id), "timeline", timeline.model_dump(), by="animation")
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"写回黑板失败: {exc}")

        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"task_id": task_id, "clip_id": clip_id, "style": st, "motion": mo,
                    "duration_sec": target.duration_sec},
        )
