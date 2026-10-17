"""切点复核工具（V7）——把 `services/timeline_view.py` 的图表能力暴露成原子工具。

为什么做成 Tool
--------------
复核图的真正价值是"能被别的 Agent 调用"：质检要它出证据、自愈循环要它判断
"这次重做有没有变好"、编辑器要一个"切点复核"按钮。此前它只挂在
`RenderService._post_render_review` 里（渲染完顺手做一次），别人拿不到。

本工具提供两个动作：
- `cuts`：给定切点时间 → 每个切点抽前/中/后三帧拼条（查跳变/爆闪/字幕遮挡）；
- `overview`：给定总时长 → 均匀抽帧拼一张全片联络表（查节奏单调/大段黑屏/字幕整段缺失）。

两者都是 best-effort：ffmpeg 抽帧失败只是少几张图，不抛异常（复核不该成为新的失败点）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class TimelineViewTool(BaseTool):
    """生成切点复核图 / 全片总览图（供质检、自愈循环与编辑器复用）。"""

    name = "timeline_view"
    description = (
        "为成片生成复核图：cuts=每个切点前后三帧对照，overview=全片均匀抽帧联络表。"
        "返回图片路径列表，供人或视觉模型判断跳变/爆闪/字幕遮挡/节奏问题"
    )
    dependencies = []

    async def execute(
        self,
        video_path: str,
        *,
        mode: str = "cuts",
        cut_times: Optional[list[float]] = None,
        duration_sec: float = 0.0,
        out_dir: str = "",
        max_cuts: int = 12,
        window: float = 0.35,
        cols: int = 4,
        frames: int = 12,
        fps: float = 0.0,
        **kwargs: Any,
    ) -> ToolExecResult:
        path = Path(video_path or "")
        if not video_path or not path.exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"视频文件不存在: {video_path}")

        try:
            from clipwright.services.timeline_view import (
                CUT_WINDOW_SEC, DEFAULT_MAX_CUTS, build_overview_sheet, build_review_sheets,
                overview_times,
            )
        except Exception as exc:  # 依赖缺失（Pillow 等）时明确报错
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"复核能力不可用: {exc}")

        base = Path(out_dir) if out_dir else path.parent / "timeline_view"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"输出目录不可用: {exc}")

        try:
            if mode == "overview":
                times = overview_times(duration_sec or 0.0, count=max(1, int(frames)))
                if not times:
                    return ToolExecResult(
                        status=ToolStatus.ERROR, tool_name=self.name,
                        error="overview 模式需要 duration_sec > 0（或用 cuts 模式按切点出图）")
                out_png = base / "overview.png"
                result = build_overview_sheet(str(path), times, out_png, cols=max(1, int(cols)))
                if not result:
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                          error="抽帧失败（ffmpeg 不可用或视频为空）")
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
                    "mode": "overview",
                    "sheets": [result],
                    "frames": len(times),
                    "count": 1,
                })

            times = [float(t) for t in (cut_times or []) if float(t) > 0]
            if mode == "cuts" and not times:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error="cuts 模式需要 cut_times（非空、>0）")
            sheets = build_review_sheets(
                str(path), times, base,
                max_cuts=int(max_cuts or DEFAULT_MAX_CUTS),
                window=float(window or CUT_WINDOW_SEC),
            )
            if not sheets:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error="抽帧失败（ffmpeg 不可用或视频为空）")
            output: dict[str, Any] = {
                "mode": "cuts",
                "sheets": sheets,
                "cut_times": times[: len(sheets)],
                "count": len(sheets),
                "dir": str(base),
            }
            if fps:
                # 提示调用方：切点可以按帧率对齐（O1 的 tick 换算），此处只透传，不做静默改动
                output["fps"] = float(fps)
            logger.info("timeline_view 工具: %d 张复核图 → %s", len(sheets), base)
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output=output)
        except Exception as exc:
            logger.warning("timeline_view 工具失败: %s", exc)
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(exc))
