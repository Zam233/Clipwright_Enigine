"""附加：音频落点与混音报告（只读）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clipwright.schema.timeline import Timeline
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.services import task_blackboard as bb
from clipwright.tool.base import BaseTool


def _load(task_id: str, timeline: dict[str, Any] | None) -> Timeline:
    if isinstance(timeline, dict):
        return Timeline(**timeline)
    board = bb.get(task_id) or {}
    for key in ("final_timeline", "timeline"):
        raw = board.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict) and raw.get("path"):
            raw = json.loads(Path(str(raw["path"])).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return Timeline(**raw)
    raise ValueError(f"任务 {task_id} 没有 timeline")


class AudioTimelineProbeTool(BaseTool):
    name = "audio_timeline_probe"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "查看旁白/字幕/BGM 的实际落点与总时长，返回音频/文字轨片段。"
    parameters_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"},
                       "timeline": {"type": "object"}},
    }

    async def execute(self, *, task_id: str = "", timeline: dict[str, Any] | None = None,
                      **kwargs: Any) -> ToolExecResult:
        try:
            tl = _load(task_id, timeline)
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(exc))
        out: dict[str, list[dict[str, Any]]] = {"audio": [], "text": []}
        for track in tl.tracks:
            kind = str(track.kind)
            if kind not in out:
                continue
            for clip in track.clips or []:
                out[kind].append({
                    "clip_id": str(clip.id), "asset_id": str(clip.asset_id),
                    "start_sec": float(clip.start_sec),
                    "duration_sec": float(clip.duration_sec),
                    "text": str(clip.text or "")[:120],
                })
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"duration_sec": float(tl.duration_sec or 0), **out})


class MixReportTool(BaseTool):
    name = "mix_report"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "一次性报告音频轨片段数、音量异常、重叠/间隙、字幕数量与总时长。"
    parameters_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"},
                       "timeline": {"type": "object"}},
    }

    async def execute(self, *, task_id: str = "", timeline: dict[str, Any] | None = None,
                      **kwargs: Any) -> ToolExecResult:
        try:
            tl = _load(task_id, timeline)
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(exc))
        audio: list[Any] = []
        text: list[Any] = []
        for track in tl.tracks:
            if str(track.kind) == "audio":
                audio.extend(track.clips or [])
            elif str(track.kind) == "text":
                text.extend(track.clips or [])
        loud = [str(c.id) for c in audio if float(getattr(c, "volume", 1.0) or 1.0) > 1.0]
        spans = sorted(((float(c.start_sec), float(c.start_sec) + float(c.duration_sec))
                        for c in audio), key=lambda x: x[0])
        overlaps = sum(1 for a, b in zip(spans, spans[1:]) if b[0] < a[1] - 1e-6)
        gaps = sum(1 for a, b in zip(spans, spans[1:]) if b[0] > a[1] + 1e-6)
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
            "duration_sec": float(tl.duration_sec or 0),
            "audio_clips": len(audio), "text_clips": len(text),
            "volume_over_1": loud, "overlaps": overlaps, "gaps": gaps,
            "issues": ([f"{len(loud)} 个音频片段音量 > 1.0"] if loud else [])
                      + ([f"{overlaps} 处音频重叠"] if overlaps else []),
        })
