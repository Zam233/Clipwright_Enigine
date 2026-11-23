"""R4：MG 单段预览 —— HTML + 浏览器渲染网格（给 Agent 看）。

不是正式 MP4 渲染：用 Playwright seek 抽帧，拼成一张网格 PNG；Agent 可以
直接拿到 `grid_path`，或传 `inspect=true` 让 vision 对网格做质检。
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any

from clipwright.animation.mg_renderer import MGRenderer
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _root(workspace_root: str, project_id: str) -> Path:
    if workspace_root:
        return Path(workspace_root)
    if project_id:
        from clipwright.services.workspace import Workspace

        ws = Workspace(str(project_id))
        if ws.exists:
            return ws.dirs().root
    return Path(tempfile.mkdtemp(prefix="clipwright_preview_"))


def _grid(frames: list[Path], out: Path, *, cols: int = 3,
          thumb_w: int = 640, labels: list[float] | None = None) -> Path:
    from PIL import Image, ImageDraw

    if not frames:
        raise ValueError("没有可拼网格的帧")
    thumbs: list[Image.Image] = []
    for f in frames:
        with Image.open(f) as im:
            im = im.convert("RGB")
            h = max(1, int(im.height * thumb_w / max(1, im.width)))
            thumbs.append(im.resize((thumb_w, h)))
    cols = max(1, min(int(cols or 3), len(thumbs)))
    rows = (len(thumbs) + cols - 1) // cols
    cell_h = max(t.height for t in thumbs)
    label_h = 22
    out_img = Image.new("RGB", (cols * thumb_w, rows * (cell_h + label_h)), "#111111")
    draw = ImageDraw.Draw(out_img)
    for i, im in enumerate(thumbs):
        x = (i % cols) * thumb_w
        y = (i // cols) * (cell_h + label_h)
        out_img.paste(im, (x, y))
        label = f"t={labels[i]:.2f}s" if labels and i < len(labels) else f"#{i + 1}"
        draw.text((x + 8, y + cell_h + 4), label, fill="#ffffff")
    out.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(out)
    return out


def _times(duration: float, frame_count: int, times: list[float] | None) -> list[float]:
    if times:
        return [max(0.0, float(t)) for t in times][:12]
    n = max(2, min(9, int(frame_count or 5)))
    total = max(0.1, float(duration or 1.0))
    return [round(total * i / (n - 1), 3) for i in range(n)]


class PreviewAnimationClipTool(BaseTool):
    name = "preview_animation_clip"
    capability = "preview_animation_clip"
    permission_tier = "write_local"
    agent_callable = True
    dry_run_capable = True
    dependencies = ["ffmpeg"]
    description = (
        "把单段 MG 渲染成 HTML 并在浏览器中抽帧，拼成网格 PNG 给 Agent 查看；"
        "可选 inspect=true 让 vision 对网格做质检。不是正式 MP4。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "mg_def": {"type": "object"},
            "params": {"type": "object"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "fps": {"type": "number", "default": 30},
            "times": {"type": "array", "items": {"type": "number"}},
            "frame_count": {"type": "integer", "default": 5, "minimum": 2, "maximum": 12},
            "cols": {"type": "integer", "default": 3, "minimum": 1, "maximum": 4},
            "grid": {"type": "boolean", "default": True},
            "inspect": {"type": "boolean", "default": False,
                        "description": "对网格做 vision 质检（会产生视觉模型费用）"},
            "brief": {"type": "string"},
            "workspace_root": {"type": "string"},
            "project_id": {"type": "string"},
        },
        "required": ["mg_def"],
    }

    async def execute(
        self, *, mg_def: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        width: int | None = None, height: int | None = None,
        fps: float = 30.0, times: list[float] | None = None,
        frame_count: int = 5, cols: int = 3, grid: bool = True,
        inspect: bool = False, brief: str = "",
        workspace_root: str = "", project_id: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if not isinstance(mg_def, dict) or not mg_def:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="mg_def 不能为空")
        root = _root(workspace_root, project_id)
        out_dir = root / "cache" / "animation_preview" / uuid.uuid4().hex[:10]
        out_dir.mkdir(parents=True, exist_ok=True)
        html = MGRenderer.render(mg_def, params=params or {}, width=width,
                                 height=height, fps=fps)
        html_path = out_dir / "index.html"
        html_path.write_text(html, encoding="utf-8")
        duration = float(mg_def.get("duration_sec") or 1.0)
        ts = _times(duration, frame_count, times)
        frames: list[Path] = []
        grid_path = ""
        grid_error = ""
        if grid:
            try:
                from clipwright.animation.agentic.frames import capture_frames

                frames = await asyncio.to_thread(
                    capture_frames, html_path, ts, out_dir / "frames", "#111111",
                )
                grid_path = str(await asyncio.to_thread(
                    _grid, frames, out_dir / "grid.png", cols=cols, labels=ts,
                ))
            except Exception as exc:  # noqa: BLE001 — 抽帧依赖浏览器，失败仍给 HTML
                grid_error = f"{type(exc).__name__}: {exc}"[:300]
        output: dict[str, Any] = {
            "preview_path": str(html_path),
            "preview_html": html[:20000],
            "format": "html+grid" if grid_path else "html",
            "duration_sec": duration,
            "frames": [str(f) for f in frames],
            "grid_path": grid_path,
            "grid_error": grid_error,
            "times": ts,
            "note": ("HTML + 网格预览；正式 MP4 走 /api/animation/preview"
                     if not grid_error else "HTML 已生成；浏览器抽帧失败，见 grid_error"),
        }
        if inspect and frames:
            try:
                from clipwright.animation.agentic.frames import inspect_frames

                result = await inspect_frames(frames, brief)
                output["findings"] = result.get("findings") or []
                output["inspect_ok"] = not any(
                    str(f.get("severity") or "") == "blocking"
                    for f in output["findings"] if isinstance(f, dict)
                )
            except Exception as exc:  # noqa: BLE001 — 质检失败不丢预览
                output["inspect_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name, output=output,
            output_path=grid_path or str(html_path),
        )
