"""资产管理 — 上传、格式检测、缩略图生成。支持按项目隔离和软连接。"""

from __future__ import annotations

import json
import os
import shutil
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
    """素材导入与管理。

    当 project_id 指定时，素材存储在 projects/{project_id}/assets/ 下：
        assets/
        ├── files/          # 软连接到原始文件
        ├── thumbnails/     # 缩略图
        └── index.json      # 素材索引

    上传的素材通过软连接（Windows 回退到复制）引用原始文件，
    删除素材仅删除软连接和 JSON 元数据，不删除原始文件。
    """

    def __init__(self, project_id: str | None = None) -> None:
        self._project_id = project_id
        if project_id:
            base = settings.project_dir / project_id / "assets"
        else:
            base = settings.library_dir
        self._library_dir = base
        self._library_dir.mkdir(parents=True, exist_ok=True)
        self._files_dir = self._library_dir / "files"
        self._files_dir.mkdir(parents=True, exist_ok=True)
        self._thumb_dir = self._library_dir / "thumbnails"
        self._thumb_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._library_dir / "index.json"
        self._assets: dict[str, AssetInfo] = {}
        self._load_index()

    # ── 导入 ──

    async def import_file(self, file_path: str | Path) -> AssetInfo:
        """导入一个媒体文件，检测格式并生成缩略图。

        P0-1 安全策略：
        - 源文件位于白名单目录内 → 软连接引用（不复制）；
        - 源文件位于白名单外 → 安全复制进素材库（保证对外服务的文件始终在白名单内）；
        - 删除时仅移除连接/副本，不影响源文件。
        """
        from clipwright.security import allowed_media_roots, is_within

        src = Path(file_path).resolve()
        if not src.exists():
            return AssetInfo(asset_id="", filename="", file_path="", media_type="", error=f"文件不存在: {file_path}")

        asset_id = f"asset_{uuid.uuid4().hex[:12]}"
        ext = src.suffix.lower()
        dest = self._files_dir / f"{asset_id}{ext}"

        _inside_whitelist = any(is_within(root, src) for root in allowed_media_roots())

        # 软连接引用白名单内文件；白名单外回退复制（Windows 非管理员也回退复制）
        try:
            dest.unlink(missing_ok=True)
            if _inside_whitelist:
                os.symlink(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))
        except OSError:
            logger.debug("symlink failed for %s, falling back to copy", src.name)
            shutil.copy2(str(src), str(dest))

        # 格式检测
        media_type = self._detect_type(ext)
        info = await self._probe(dest)

        # 缩略图
        thumb_path = ""
        if media_type in ("video", "image"):
            thumb_file = self._thumb_dir / f"{asset_id}_thumb.jpg"
            self._generate_thumbnail(str(dest), str(thumb_file), media_type)
            if thumb_file.exists():
                thumb_path = str(thumb_file.resolve())

        asset = AssetInfo(
            asset_id=asset_id,
            filename=src.name,
            file_path=str(dest.resolve()),
            media_type=media_type,
            duration_sec=info.get("duration", 0),
            width=info.get("width", 0),
            height=info.get("height", 0),
            file_size=dest.stat().st_size,
            thumbnail_path=thumb_path,
        )
        self._assets[asset_id] = asset
        self._save_index()
        return asset

    async def list_assets(self) -> list[AssetInfo]:
        """列出当前项目的所有素材。"""
        return list(self._assets.values())

    def get(self, asset_id: str) -> Optional[AssetInfo]:
        """获取指定素材信息（返回前校验文件存在性）。"""
        asset = self._assets.get(asset_id)
        if asset and asset.file_path:
            fp = Path(asset.file_path)
            if not fp.exists():
                asset.error = "素材文件不存在（原始文件可能已移动或删除）"
        return asset

    def delete_asset(self, asset_id: str) -> bool:
        """删除素材：移除软连接/文件和缩略图，但保留原始文件。"""
        asset = self._assets.pop(asset_id, None)
        if not asset:
            return False
        try:
            # 移除软连接/素材文件
            fp = Path(asset.file_path) if asset.file_path else None
            if fp and fp.exists():
                fp.unlink()
            # 移除缩略图
            if asset.thumbnail_path:
                tp = Path(asset.thumbnail_path)
                if tp.exists():
                    tp.unlink()
        except OSError as e:
            logger.warning("清理素材文件失败 %s: %s", asset_id, e)
        self._save_index()
        return True

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
        from clipwright.tool.video import resolve_ffprobe
        info: dict[str, Any] = {"duration": 0, "width": 0, "height": 0}
        try:
            result = subprocess.run(
                [resolve_ffprobe(), "-v", "error", "-print_format", "json",
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
        from clipwright.tool.video import resolve_ffmpeg
        ffmpeg = resolve_ffmpeg()
        try:
            if media_type == "video":
                subprocess.run(
                    [ffmpeg, "-y", "-i", src, "-ss", "0.5", "-vframes", "1",
                     "-vf", "scale=320:-1", thumb_path],
                    capture_output=True, text=True, timeout=30,
                )
            elif media_type == "image":
                subprocess.run(
                    [ffmpeg, "-y", "-i", src, "-vf", "scale=320:-1", thumb_path],
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
