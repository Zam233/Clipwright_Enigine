"""素材预处理管线 — 上传后自动转码/场景检测/音频提取/内容分析。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger


class PreprocessingTask:
    """单个素材的预处理任务。"""

    def __init__(self, asset_id: str, file_path: str):
        self.asset_id = asset_id
        self.file_path = file_path
        self.status: str = "pending"  # pending / running / completed / failed
        self.results: dict[str, Any] = {}
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "file_path": self.file_path,
            "status": self.status,
            "results": self.results,
            "errors": self.errors,
        }


class MaterialPreprocessor:
    """素材预处理器 — 异步执行转码/场景检测/音频提取/内容分析。"""

    _tasks: dict[str, PreprocessingTask] = {}

    @classmethod
    def get_task(cls, asset_id: str) -> Optional[PreprocessingTask]:
        return cls._tasks.get(asset_id)

    @classmethod
    def list_tasks(cls) -> list[PreprocessingTask]:
        return list(cls._tasks.values())

    @classmethod
    async def process(cls, asset_id: str, file_path: str) -> PreprocessingTask:
        """对单个素材执行全部预处理步骤。"""
        task = PreprocessingTask(asset_id, file_path)
        cls._tasks[asset_id] = task
        task.status = "running"

        steps = [
            ("proxy", cls._generate_proxy(file_path)),
            ("scene_detect", cls._detect_scenes(file_path)),
            ("audio_extract", cls._extract_audio(file_path)),
            ("analyze", cls._analyze_content(file_path)),
        ]

        for name, coro in steps:
            try:
                result = await coro
                task.results[name] = result
                logger.info("预处理 [%s] %s 完成", asset_id[:8], name)
            except Exception as e:
                task.errors.append(f"{name}: {e}")
                logger.warning("预处理 [%s] %s 失败: %s", asset_id[:8], name, e)

        task.status = "completed" if not task.errors else "failed"
        return task

    @staticmethod
    async def _generate_proxy(file_path: str) -> dict:
        """生成 720p 代理文件。"""
        src = Path(file_path)
        ext = src.suffix.lower()
        proxy_dir = src.parent / ".proxy"
        proxy_dir.mkdir(parents=True, exist_ok=True)
        proxy_path = str(proxy_dir / f"{src.stem}_proxy{ext}")

        if Path(proxy_path).exists():
            return {"proxy_path": proxy_path, "cached": True}

        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        if ext not in video_exts:
            return {"proxy_path": "", "note": "not a video file"}

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", file_path,
            "-vf", "scale=-2:720",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            proxy_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not Path(proxy_path).exists():
            raise RuntimeError(f"proxy generation failed: {result.stderr[:200]}")
        size = Path(proxy_path).stat().st_size
        return {"proxy_path": proxy_path, "size_bytes": size, "cached": False}

    @staticmethod
    async def _detect_scenes(file_path: str) -> dict:
        """检测视频场景切换点。"""
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", file_path,
            "-vf", "select='gt(scene,0.3)',showinfo",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        import re
        timestamps = re.findall(r"pts_time:([\d.]+)", result.stderr or "")
        scene_changes = [round(float(t), 1) for t in timestamps if t]
        return {"scene_count": len(scene_changes), "scene_changes": scene_changes[:50]}

    @staticmethod
    async def _extract_audio(file_path: str) -> dict:
        """提取音频流供后续分析。"""
        src = Path(file_path)
        ext = src.suffix.lower()
        audio_exts = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}
        if ext in audio_exts:
            return {"audio_path": file_path, "note": "already audio"}

        audio_path = Path(tempfile.mktemp(suffix=".wav")).name
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", file_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not Path(audio_path).exists():
            raise RuntimeError(f"audio extraction failed: {result.stderr[:200]}")
        return {"audio_path": audio_path}

    @staticmethod
    async def _analyze_content(file_path: str) -> dict:
        """分析内容：分辨率、码率、编码等元数据。"""
        cmd = [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        fmt = data.get("format", {})

        info = {
            "duration": float(fmt.get("duration", 0)),
            "size": int(fmt.get("size", 0)),
            "bitrate": fmt.get("bit_rate", ""),
            "streams": [],
        }
        for s in data.get("streams", []):
            sinfo = {"type": s.get("codec_type"), "codec": s.get("codec_name")}
            if s.get("codec_type") == "video":
                sinfo.update({
                    "width": s.get("width"), "height": s.get("height"),
                    "fps": s.get("r_frame_rate", ""),
                })
            elif s.get("codec_type") == "audio":
                sinfo.update({
                    "sample_rate": s.get("sample_rate"),
                    "channels": s.get("channels"),
                })
            info["streams"].append(sinfo)
        return info


# 全局预处理队列
_preprocessing_queue: asyncio.Queue = asyncio.Queue()
_preprocessing_results: dict[str, PreprocessingTask] = {}


async def preprocess_worker():
    """后台预处理工作线程。"""
    while True:
        try:
            task_data = await _preprocessing_queue.get()
            asset_id, file_path = task_data["asset_id"], task_data["file_path"]
            logger.info("预处理队列开始: %s", asset_id[:8])
            task = await MaterialPreprocessor.process(asset_id, file_path)
            _preprocessing_results[asset_id] = task
            _preprocessing_queue.task_done()
        except Exception as e:
            logger.error("预处理队列异常: %s", e)


async def enqueue_preprocessing(asset_id: str, file_path: str) -> dict:
    """将素材加入预处理队列。"""
    task = PreprocessingTask(asset_id, file_path)
    _preprocessing_results[asset_id] = task
    await _preprocessing_queue.put({"asset_id": asset_id, "file_path": file_path})
    return {"asset_id": asset_id, "status": "queued"}


def get_preprocessing_status(asset_id: str) -> Optional[dict]:
    """查询预处理状态。"""
    task = _preprocessing_results.get(asset_id)
    return task.to_dict() if task else None
