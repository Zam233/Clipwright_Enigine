"""素材自动索引服务 — 上传后自动提取信息、分类、建立索引。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings
from clipwright.schema.material import MaterialAsset, MaterialType


class MediaIndexer:
    """媒体文件索引器：提取元数据、生成描述、建立向量索引。"""

    @staticmethod
    async def extract_metadata(file_path: str) -> dict[str, Any]:
        """提取媒体文件元数据。"""
        result = {"duration": 0, "width": 0, "height": 0, "codec": "", "fps": 0}
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", file_path],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(r.stdout)
            fmt = data.get("format", {})
            result["duration"] = float(fmt.get("duration", 0))
            result["format"] = fmt.get("format_name", "")
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    result["width"] = s.get("width", 0)
                    result["height"] = s.get("height", 0)
                    result["codec"] = s.get("codec_name", "")
                    fps_str = s.get("r_frame_rate", "0/1")
                    parts = fps_str.split("/")
                    result["fps"] = float(parts[0]) / float(parts[1]) if len(parts) == 2 else 0
                    break
                elif s.get("codec_type") == "audio":
                    result["codec"] = s.get("codec_name", "")
                    result["sample_rate"] = s.get("sample_rate", 0)
                    result["channels"] = s.get("channels", 0)
        except Exception as e:
            logger.warning("MediaIndexer 元数据提取失败: %s", e)
        return result

    @staticmethod
    async def classify(file_path: str, metadata: dict) -> list[str]:
        """根据元数据和文件名自动生成标签。"""
        tags = []
        path = Path(file_path)
        name = path.stem.lower()

        # 从文件名提取可能的标签
        name_parts = name.replace("_", " ").replace("-", " ").replace(".", " ").split()
        tags.extend(name_parts[:5])

        # 从类型推断
        ext = path.suffix.lower()
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
        audio_exts = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        if ext in video_exts:
            tags.append("video")
            w, h = metadata.get("width", 0), metadata.get("height", 0)
            if w > 0 and h > 0:
                tags.append("landscape" if w > h else "portrait")
                if w >= 3840:
                    tags.append("4k")
                elif w >= 1920:
                    tags.append("1080p")
        elif ext in audio_exts:
            tags.append("audio")
        elif ext in image_exts:
            tags.append("image")

        return list(set(tags))

    @staticmethod
    async def auto_index(file_path: str, asset: MaterialAsset) -> MaterialAsset:
        """对单个素材进行自动索引，返回增强的 Asset 对象。"""
        meta = await MediaIndexer.extract_metadata(file_path)
        tags = await MediaIndexer.classify(file_path, meta)

        # 更新 asset
        asset.tags = list(set(list(asset.tags) + tags))
        asset.duration_sec = asset.duration_sec or meta.get("duration", 0)
        asset.metadata["indexed"] = True
        asset.metadata["width"] = meta.get("width", 0)
        asset.metadata["height"] = meta.get("height", 0)
        asset.metadata["fps"] = meta.get("fps", 0)
        asset.metadata["codec"] = meta.get("codec", "")
        asset.metadata["auto_tags"] = tags

        return asset


class ClipMatcher:
    """CLIP 语义匹配器 — 计算文本与视频的语义匹配度。"""

    @staticmethod
    async def match(query: str, candidates: list[dict[str, Any]]) -> list[tuple[int, float]]:
        """计算 query 与每个候选素材的语义匹配度。
        目前使用标签匹配 + 关键词重叠作为 CLIP 的轻量替代。
        """
        query_words = set(query.lower().split())
        scored = []
        for i, c in enumerate(candidates):
            text_pool = " ".join([
                c.get("title", ""),
                " ".join(c.get("tags", []) or []),
                c.get("description", "") or "",
            ]).lower()
            tag_words = set(text_pool.split())
            if not query_words or not tag_words:
                scored.append((i, 0.0))
                continue
            overlap = len(query_words & tag_words) / max(len(query_words), 1)
            scored.append((i, min(1.0, overlap * 2)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
