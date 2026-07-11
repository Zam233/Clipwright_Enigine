"""资产管理 — 上传、格式检测、缩略图生成。"""

from __future__ import annotations

import json
import mimetypes
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings


class AssetInfo:
    """已导入的素材信息。"""
    def __init__(
        self,
        asset_id: str,
        filename: str,
        file_path: str,
        media_type: str,
        duration_sec: float = 0,
        width: int = 0,
        height: int = 0,
        file_size: int = 0,
        thumbnail_path: str = "",
        error: str = "",
    ):
        self.asset_id = asset_id
        self.filename = filename
        self.file_path = file_path
        self.media_type = media_type  # video / audio / image
        self.duration_sec = duration_sec
        self.width = width
        self.height = height
        self.file_size = file_size
        self.thumbnail_path = thumbnail_path
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "filename": self.filename,
            "file_path": self.file_path,
            "media_type": self.media_type,
            "duration_sec": self.duration_sec,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
            "thumbnail_path": self.thumbnail_path,
            "error": self.error,
        }


class AssetManager:
    """素材导入与管理。"""

    def __init__(self) -> None:
        self._library_dir = settings.library_dir
        self._library_dir.mkdir(parents=True, exist_ok=True)
        self._thumb_dir = self._library_dir / "thumbnails"
        self._thumb_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._library_dir / "index.json"
        self._assets: dict[str, AssetInfo] = {}
        self._load_index()

    # ── 导入 ──

    async def import_file(self, file_path: str | Path) -> AssetInfo:
        """导入一个媒体文件，检测格式并生成缩略图。"""
        src = Path(file_path)
        if not src.exists():
            return AssetInfo(asset_id="", filename="", file_path="", media_type="", error=f"文件不存在: {file_path}")

        asset_id = f"asset_{uuid.uuid4().hex[:12]}"
        ext = src.suffix.lower()
        dest = self._library_dir / f"{asset_id}{ext}"
        import shutil
        shutil.copy2(str(src), str(dest))

        # 格式检测
        media_type = self._detect_type(ext)
        info = await self._probe(dest)

        # 缩略图
        thumb_path = ""
        if media_type in ("video", "image"):
            thumb_path = str(self._thumb_dir / f"{asset_id}_thumb.jpg")
            self._generate_thumbnail(str(dest), thumb_path, media_type)

        asset = AssetInfo(
            asset_id=asset_id,
            filename=src.name,
            file_path=str(dest.resolve()),
            media_type=media_type,
            duration_sec=info.get("duration", 0),
            width=info.get("width", 0),
            height=info.get("height", 0),
            file_size=dest.stat().st_size,
            thumbnail_path=thumb_path if Path(thumb_path).exists() else "",
        )
        self._assets[asset_id] = asset
        self._save_index()
        return asset

    async def list_assets(self) -> list[AssetInfo]:
        return list(self._assets.values())

    def get(self, asset_id: str) -> Optional[AssetInfo]:
        return self._assets.get(asset_id)

    # ── 内部 ──

    @staticmethod
    def _detect_type(ext: str) -> str:
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
        audio_exts = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        if ext in video_exts:
            return "video"
        if ext in audio_exts:
            return "audio"
        if ext in image_exts:
            return "image"
        return "unknown"

    @staticmethod
    async def _probe(path: Path) -> dict[str, Any]:
        """用 ffprobe 检测媒体信息。"""
        info: dict[str, Any] = {"duration": 0, "width": 0, "height": 0}
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            info["duration"] = float(fmt.get("duration", 0))
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    info["width"] = int(stream.get("width", 0))
                    info["height"] = int(stream.get("height", 0))
                    break
        except Exception as e:
            logger.debug("ffprobe 检测失败: %s", e)
        return info

    @staticmethod
    def _generate_thumbnail(src: str, thumb_path: str, media_type: str) -> None:
        """生成缩略图。"""
        try:
            if media_type == "video":
                subprocess.run(
                    ["ffmpeg", "-y", "-i", src, "-ss", "0.5", "-vframes", "1",
                     "-vf", "scale=320:-1", thumb_path],
                    capture_output=True, text=True, timeout=30,
                )
            elif media_type == "image":
                subprocess.run(
                    ["ffmpeg", "-y", "-i", src, "-vf", "scale=320:-1", thumb_path],
                    capture_output=True, text=True, timeout=30,
                )
        except Exception as e:
            logger.debug("缩略图生成失败: %s", e)

    def _load_index(self) -> None:
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                for item in data:
                    a = AssetInfo(**item)
                    self._assets[a.asset_id] = a
            except Exception as e:
                logger.debug("素材索引加载失败: %s", e)

    def _save_index(self) -> None:
        try:
            data = [a.to_dict() for a in self._assets.values()]
            self._index_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("素材索引保存失败: %s", e)
