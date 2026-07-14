"""渲染 API — 提交、查询渲染任务。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.services.render import RenderService
from fastapi.responses import FileResponse
from pydantic import BaseModel
import asyncio

router = APIRouter(prefix="/api/render", tags=["render"])
_render_service = RenderService()

# 渲染队列
_render_queue: dict[str, dict] = {}
_render_queue_counter = 0


@router.post("/queue")
async def queue_render(body: RenderRequest) -> dict:
    """将渲染任务加入队列，立即返回任务 ID，后台异步执行。"""
    global _render_queue_counter
    _render_queue_counter += 1
    task_id = f"render_{_render_queue_counter}_{int(__import__('time').time())}"
    _render_queue[task_id] = {"status": "queued", "progress": 0, "result": None}

    params = _resolve_settings(body.settings)
    tl = body.timeline
    out = f"renders/{task_id}.mp4"

    async def _run():
        _render_queue[task_id]["status"] = "rendering"
        _render_queue[task_id]["progress"] = 0
        _render_queue[task_id]["clip_count"] = len(tl.tracks or [])
        _render_queue[task_id]["current_clip"] = 0
        try:
            # 分阶段进度更新回调
            async def on_progress(phase: str, pct: float, detail: str = ""):
                _render_queue[task_id]["progress"] = min(int(pct), 99)
                _render_queue[task_id]["phase"] = phase
                _render_queue[task_id]["detail"] = detail

            result = await _render_service.render(
                tl, out,
                width=params["width"], height=params["height"],
                fps=params["fps"], bitrate=params["bitrate"],
                audio_bitrate=params["audio_bitrate"],
                audio_file_path=body.audio_file_path or "",
                bgm_file_path=body.bgm_file_path or "",
                progress_callback=on_progress,
            )
            _render_queue[task_id]["result"] = result.to_dict()
            _render_queue[task_id]["status"] = "completed" if result.success else "failed"
            _render_queue[task_id]["progress"] = 100
            _render_queue[task_id]["output_path"] = str(out)
        except Exception as e:
            _render_queue[task_id]["status"] = "failed"
            _render_queue[task_id]["result"] = {"error": str(e)}
        finally:
            # 60s 后清理
            async def _cleanup():
                await asyncio.sleep(60)
                _render_queue.pop(task_id, None)
            asyncio.create_task(_cleanup())

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "queued", "output": out}


@router.get("/queue/{task_id}")
async def get_queue_status(task_id: str) -> dict:
    """查询渲染队列任务状态。"""
    task = _render_queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"task_id": task_id, **task}


@router.get("/queue/stream/{task_id}")
async def stream_render_progress(task_id: str):
    """SSE 流：实时推送渲染进度。"""
    from fastapi.responses import StreamingResponse
    import asyncio

    async def event_stream():
        last_status = ""
        for _ in range(600):  # 最多等 5 分钟
            task = _render_queue.get(task_id)
            if task is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Task not found'})}\n\n"
                return
            status = task["status"]
            if status != last_status:
                last_status = status
            yield f"data: {json.dumps({
                'type': 'progress',
                'task_id': task_id,
                'status': status,
                'progress': task.get('progress', 0),
                'phase': task.get('phase', ''),
                'detail': task.get('detail', ''),
                'clip_count': task.get('clip_count', 0),
                'current_clip': task.get('current_clip', 0),
            })}\n\n"
            if status in ("completed", "failed"):
                yield f"data: {json.dumps({'type': status, 'task_id': task_id, 'result': task.get('result')})}\n\n"
                return
            await asyncio.sleep(0.5)
        yield f"data: {json.dumps({'type': 'timeout'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


@router.get("/queue")
async def list_queue() -> dict:
    """列出所有队列任务。"""
    tasks = []
    for tid, info in _render_queue.items():
        tasks.append({"task_id": tid, "status": info["status"], "progress": info.get("progress", 0)})
    tasks.sort(key=lambda t: t["task_id"], reverse=True)
    return {"tasks": tasks}


@router.get("/video")
async def serve_video(path: str):
    """代理视频文件供前端预览（浏览器不能直接加载 file:// 路径）。"""
    src = Path(path)
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    return FileResponse(str(src), media_type="video/mp4",
                        headers={"Accept-Ranges": "bytes"})


@router.get("/download/{filename}")
async def download_render(filename: str):
    """下载渲染输出的 MP4 文件。"""
    from pathlib import Path
    file_path = Path("renders") / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
    return FileResponse(str(file_path), media_type="video/mp4",
                        filename=filename,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})



_EXPORT_PRESETS = {
    "bilibili": {"width": 1920, "height": 1080, "fps": 30, "bitrate": "6M", "audio_bitrate": "320k", "note": "B站推荐 1080p 高码率"},
    "youtube": {"width": 1920, "height": 1080, "fps": 30, "bitrate": "8M", "audio_bitrate": "192k", "note": "YouTube 推荐"},
    "tiktok": {"width": 1080, "height": 1920, "fps": 30, "bitrate": "4M", "audio_bitrate": "192k", "note": "抖音/快手 竖屏"},
    "weibo": {"width": 720, "height": 1280, "fps": 24, "bitrate": "3M", "audio_bitrate": "192k", "note": "微博 竖屏"},
    "1080p": {"width": 1920, "height": 1080, "fps": 30, "bitrate": "5M", "audio_bitrate": "192k", "note": "标准 1080p"},
    "720p": {"width": 1280, "height": 720, "fps": 30, "bitrate": "3M", "audio_bitrate": "192k", "note": "标准 720p"},
    "480p": {"width": 854, "height": 480, "fps": 24, "bitrate": "1.5M", "audio_bitrate": "128k", "note": "标准 480p"},
}


class RenderSettings(BaseModel):
    """渲染参数。"""
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    bitrate: str = "5M"
    audio_bitrate: str = "192k"
    preset: str = ""  # 预设名称，如 "bilibili"、"tiktok"，会覆盖其他参数


class RenderRequest(BaseModel):
    """渲染请求体。"""
    timeline: Timeline
    output_path: Optional[str] = None
    settings: Optional[RenderSettings] = None
    audio_file_path: Optional[str] = None  # 用户上传的配音文件路径
    bgm_file_path: Optional[str] = None  # 背景音乐文件路径


@router.get("/presets")
async def list_presets() -> dict:
    """列出所有导出预设。"""
    return {"presets": _EXPORT_PRESETS}


def _resolve_settings(s: RenderSettings | None) -> dict:
    """根据设置和预设解析最终渲染参数。"""
    base = s.model_dump() if s else {}
    preset_name = base.pop("preset", "") or ""
    if preset_name and preset_name in _EXPORT_PRESETS:
        preset = _EXPORT_PRESETS[preset_name].copy()
        preset.pop("note", None)
        # 预设为基底，单个设置项可覆盖
        preset.update({k: v for k, v in base.items() if v is not None and k != "preset"})
        return preset
    return {
        "width": base.get("width", 1920),
        "height": base.get("height", 1080),
        "fps": base.get("fps", 30.0),
        "bitrate": base.get("bitrate", "5M"),
        "audio_bitrate": base.get("audio_bitrate", "192k"),
    }


@router.post("/start")
async def start_render(
    body: RenderRequest,
) -> dict:
    """提交渲染任务：将 Timeline JSON 渲染为 MP4 视频。"""
    tl = body.timeline
    out = body.output_path or "renders/output.mp4"
    s = body.settings
    params = _resolve_settings(s)
    logger.info("渲染请求: tracks=%d, output=%s, params=%s",
                len(tl.tracks or []), out, params)
    try:
        result = await _render_service.render(
            tl, out,
            width=params["width"],
            height=params["height"],
            fps=params["fps"],
            bitrate=params["bitrate"],
            audio_bitrate=params["audio_bitrate"],
            audio_file_path=body.audio_file_path or "",
            bgm_file_path=body.bgm_file_path or "",
        )
    except Exception as e:
        logger.exception("start_render failed: %s", e)
        raise
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return result.to_dict()


@router.get("/status/{render_id}")
async def get_render_status(render_id: str) -> dict:
    """查询渲染进度。优先查询队列，若无则返回 404。"""
    # 先检查队列
    queue_task = _render_queue.get(render_id)
    if queue_task is not None:
        return {
            "render_id": render_id,
            "status": queue_task["status"],
            "progress": queue_task.get("progress", 0),
            "phase": queue_task.get("phase", ""),
            "detail": queue_task.get("detail", ""),
            "output_path": queue_task.get("output_path", ""),
            "result": queue_task.get("result"),
        }
    # 回退：检查 renders/ 目录中是否有同名文件
    from pathlib import Path
    file_path = Path("renders") / f"{render_id}.mp4"
    if file_path.exists():
        return {
            "render_id": render_id,
            "status": "completed",
            "progress": 100,
            "output_path": str(file_path),
        }
    raise HTTPException(status_code=404, detail=f"渲染任务不存在: {render_id}")


@router.get("/thumbnail")
async def get_video_thumbnail(path: str, time_sec: float = 0.5):
    """从视频文件提取一帧作为缩略图。"""
    src = Path(path)
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

    ext = src.suffix.lower()
    if ext not in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wav", ".mp3", ".m4a"):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    # 对纯音频文件，返回默认缩略图
    if ext in (".wav", ".mp3", ".m4a"):
        raise HTTPException(status_code=400, detail="音频文件无缩略图")

    import subprocess
    thumb = Path(tempfile.mktemp(suffix=".jpg"))
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ss", str(time_sec),
             "-vframes", "1", "-vf", "scale=160:-1", "-q:v", "5",
             str(thumb)],
            capture_output=True, text=False, timeout=15,
        )
        if result.returncode != 0 or not thumb.exists() or thumb.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="缩略图生成失败")
        return FileResponse(str(thumb), media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=3600"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error("缩略图异常: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
