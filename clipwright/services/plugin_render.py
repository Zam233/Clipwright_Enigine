"""插件 ClipKind 的渲染时回调（M5）。

契约（`media_overlay.v1`，MVP）：
- engine 把 `ClipRenderRequest` 交给插件声明的 `renderer_tool`；
- 插件在 worker 内执行自己的运行时，返回**一个带 alpha 的透明视频** artifact；
- engine 校验媒体契约（尺寸/帧率/时长/alpha）后，把 clip 改写为普通视频 overlay，
  交给既有 ffmpeg 合成链——engine 不执行插件代码、不解析插件工程文件。

本模块只做契约与改写，不直接调用 ffmpeg；实际渲染仍由 `services/render.py` 完成。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from clipwright.config import logger
from clipwright.services.clip_kind_registry import ClipKindDefinition, ClipKindRegistry
from clipwright.schema.timeline import Timeline

CONTRACT = "media_overlay.v1"
#: 时长容差（秒）：1 帧 @30fps
DURATION_TOLERANCE_SEC = 1 / 30.0
FPS_TOLERANCE = 0.01


class PluginRenderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RenderScope:
    plugin_id: str
    project_id: str
    task_id: str = ""


def build_request(
    clip: Any,
    *,
    definition: ClipKindDefinition,
    scope: RenderScope,
    width: int,
    height: int,
    fps: float,
) -> dict[str, Any]:
    """构造通用 ClipRenderRequest（字段全部是通用媒体信息）。"""
    return {
        "contract": CONTRACT,
        "plugin_id": scope.plugin_id,
        "clip_kind": definition.kind_id,
        "clip_id": str(getattr(clip, "id", "")),
        "task_id": scope.task_id,
        "project_id": scope.project_id,
        "start_sec": float(getattr(clip, "start_sec", 0.0)),
        "duration_sec": float(getattr(clip, "duration_sec", 0.0)),
        "fps": float(fps),
        "width": int(width),
        "height": int(height),
        "metadata": dict(getattr(clip, "metadata", {}) or {}),
        "input_artifacts": [],
        "render_settings": {},
    }


async def render_clip(host: Any, request: dict[str, Any],
                      *, renderer_tool: str, timeout: float = 600.0) -> dict[str, Any]:
    """调用插件 renderer tool；失败统一成 PluginRenderError。"""
    if not renderer_tool:
        raise PluginRenderError("NO_RENDERER", "clip_kind 未声明 renderer_tool")
    scope = {
        "plugin_id": request.get("plugin_id", ""),
        "project_id": request.get("project_id", ""),
        "task_id": request.get("task_id", ""),
    }
    result = await host.invoke(
        "tool.call",
        {"id": renderer_tool, "input": {"request": request}, "scope": scope},
        timeout=timeout,
        scope=scope,
    )
    if not isinstance(result, dict) or not result.get("success"):
        error = (result or {}).get("error") if isinstance(result, dict) else result
        raise PluginRenderError("PLUGIN_RENDER_FAILED", f"插件渲染失败: {error}")
    payload = result.get("output")
    if not isinstance(payload, dict):
        raise PluginRenderError("BAD_RESULT", "插件渲染结果必须是对象")
    return payload


def probe_media(path: str | Path) -> dict[str, Any]:
    """用 ffprobe 读媒体事实（宽/高/帧率/时长/像素格式）。"""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,pix_fmt,duration",
        "-show_entries", "format=duration", "-of", "json", str(path),
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        data = json.loads(raw.stdout or "{}")
    except Exception as e:  # noqa: BLE001
        raise PluginRenderError("PROBE_FAILED", f"媒体探测失败: {e}") from e
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    def _fps(value: str) -> float:
        try:
            num, _, den = str(value or "0/1").partition("/")
            return float(num) / float(den or 1)
        except Exception:  # noqa: BLE001
            return 0.0

    duration = stream.get("duration") or fmt.get("duration") or 0
    try:
        duration = float(duration)
    except Exception:  # noqa: BLE001
        duration = 0.0
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": _fps(stream.get("r_frame_rate")),
        "duration": duration,
        "pix_fmt": str(stream.get("pix_fmt") or ""),
    }


def validate_result(
    payload: dict[str, Any],
    request: dict[str, Any],
    *,
    probe: Callable[[str | Path], dict[str, Any]] = probe_media,
) -> dict[str, Any]:
    """按 MVP 容差表校验，返回 overlay 描述（供改写时间线）。"""
    path = str(payload.get("path") or "").strip()
    if not path or not Path(path).is_file():
        raise PluginRenderError("ARTIFACT_MISSING", f"插件返回的媒体不存在: {path!r}")
    mime = str(payload.get("mime") or "")
    if not mime.startswith("video/"):
        raise PluginRenderError("BAD_MIME", f"media_overlay 需要视频: {mime!r}")
    info = probe(path)
    if "a" not in info.get("pix_fmt", "") and "4444" not in info.get("pix_fmt", ""):
        raise PluginRenderError(
            "NO_ALPHA", f"透明视频缺少 alpha 通道: {info.get('pix_fmt')!r}",
        )
    if int(payload.get("width") or 0) != int(request["width"]) or \
            int(payload.get("height") or 0) != int(request["height"]):
        # 允许 contain：插件声明 fit=contain 时按请求尺寸输出
        raise PluginRenderError(
            "SIZE_MISMATCH",
            f"尺寸不一致: {payload.get('width')}x{payload.get('height')} "
            f"!= {request['width']}x{request['height']}",
        )
    fps = float(payload.get("fps") or 0)
    if abs(fps - float(request["fps"])) > FPS_TOLERANCE:
        raise PluginRenderError("FPS_MISMATCH", f"帧率不一致: {fps} != {request['fps']}")
    duration = float(payload.get("duration") or 0)
    if abs(duration - float(request["duration_sec"])) > DURATION_TOLERANCE_SEC:
        raise PluginRenderError(
            "DURATION_MISMATCH",
            f"时长差超过 1 帧: {duration} vs {request['duration_sec']}",
        )
    return {
        "source_path": path,
        "artifact_id": str(payload.get("artifact_id") or ""),
        "start_sec": float(request["start_sec"]),
        "duration_sec": float(request["duration_sec"]),
        "alpha": True,
        "pix_fmt": info.get("pix_fmt", ""),
    }


async def materialize_plugin_clips(
    timeline: Timeline,
    *,
    host: Any,
    scope: RenderScope,
    width: int,
    height: int,
    fps: float,
    probe: Callable[[str | Path], dict[str, Any]] = probe_media,
) -> Timeline:
    """把时间线里的插件 clip 改写为普通视频 overlay（原地副本，不改入参）。

    engine 在这里只调用插件 renderer + 校验媒体；插件工程文件不参与解析。
    """
    result = timeline.model_copy(deep=True)
    for track in result.tracks:
        definition = ClipKindRegistry.get(str(track.kind))
        is_plugin_track = bool(definition and not definition.builtin)
        for clip in track.clips or []:
            definition = ClipKindRegistry.get(str(clip.kind))
            if definition is None or definition.builtin:
                continue
            request = build_request(
                clip, definition=definition, scope=scope, width=width, height=height, fps=fps,
            )
            payload = await render_clip(
                host, request, renderer_tool=definition.renderer_tool,
            )
            overlay = validate_result(payload, request, probe=probe)
            clip.kind = "video"
            clip.asset_id = overlay["source_path"]
            clip.metadata = {
                **dict(clip.metadata or {}),
                "alpha": True,
                "plugin_kind": definition.kind_id,
                "plugin_artifact_id": overlay["artifact_id"],
            }
            logger.info(
                "插件片段已具象化: clip=%s kind=%s artifact=%s",
                clip.id, definition.kind_id, overlay["artifact_id"],
            )
        if is_plugin_track:
            # 插件轨道按视频轨道参与 overlay 合成（index>0 时走 overlay_segments）
            track.kind = "video"
    return result
