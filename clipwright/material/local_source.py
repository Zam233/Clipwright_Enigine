"""本地文件夹素材源：把用户自己的素材目录接进多源检索（真实、可离线）。

为什么需要它：文档里一直写着"本地文件素材源 / 扫描指定目录"，但代码里只有
JSON 目录、URL 列表与 RAG 三种——**文档承诺了不存在的源**。这个模块把承诺补上。

设计取舍：

- **只索引、不搬文件**：素材留在原地，索引只记路径与轻量元数据；
- **不猜时长/分辨率**：能拿到就填（PIL/ffprobe 可选），拿不到留空，
  不用编造值污染后续排序；
- **按需重建**：目录 mtime 变了才重新扫描，避免每次搜索都遍历磁盘。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from clipwright.config import logger
from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialType

#: 认作视频 / 图片的扩展名
VIDEO_EXTS: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
IMAGE_EXTS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    """把文件名/目录名切成检索词（英文/数字按词切，中文按字符切）。

    中文不走分词器：素材名通常是短标签（"海面""夜景"），按字切已经够用，
    也避免为一个素材源引入分词依赖。
    """
    lowered = str(text or "").lower()
    words = set(_TOKEN_RE.findall(lowered))
    for char in lowered:
        if "\u4e00" <= char <= "\u9fff":
            words.add(char)
    return words


@dataclass
class _IndexedFile:
    path: Path
    media_type: str
    rel: str
    tokens: set[str] = field(default_factory=set)


class LocalFolderSource(MaterialSource):
    """扫描本地目录，按文件名/子目录名做关键词检索。"""

    source_id: str = "local_folder"
    source_name: str = "本地素材文件夹"

    def __init__(
        self,
        root: str | Path,
        *,
        source_id: str = "local_folder",
        source_name: str = "本地素材文件夹",
        max_files: int = 5000,
        recursive: bool = True,
    ) -> None:
        self.root = Path(root)
        self.source_id = source_id
        self.source_name = source_name
        self._max_files = max(1, int(max_files))
        self._recursive = bool(recursive)
        self._index: list[_IndexedFile] = []
        self._index_mtime: float | None = None

    # ── 索引 ────────────────────────────────────────────────────────

    def _iter_files(self) -> Iterable[Path]:
        if not self.root.is_dir():
            return []
        pattern = "**/*" if self._recursive else "*"
        count = 0
        for path in sorted(self.root.glob(pattern)):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in VIDEO_EXTS:
                kind = MaterialType.VIDEO.value
            elif suffix in IMAGE_EXTS:
                kind = MaterialType.IMAGE.value
            else:
                continue
            yield path, kind  # type: ignore[misc]
            count += 1
            if count >= self._max_files:
                return

    def refresh(self, *, force: bool = False) -> int:
        """重建索引（目录 mtime 未变且非 force 时复用）。返回索引条目数。"""
        if not self.root.is_dir():
            self._index = []
            self._index_mtime = None
            return 0
        try:
            mtime = self.root.stat().st_mtime
        except OSError:
            mtime = None
        if not force and self._index_mtime is not None and mtime == self._index_mtime:
            return len(self._index)

        index: list[_IndexedFile] = []
        for entry in self._iter_files():
            path, kind = entry  # type: ignore[misc]
            try:
                rel = str(path.relative_to(self.root))
            except ValueError:
                rel = path.name
            tokens = _tokens(rel) | _tokens(path.stem)
            index.append(_IndexedFile(path=path, media_type=kind, rel=rel, tokens=tokens))
        self._index = index
        self._index_mtime = mtime
        logger.info("[LocalFolder] 索引 %d 个素材（%s）", len(index), self.root)
        return len(index)

    # ── 检索 ────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        top_k: int = 10,
        media_type: str = "all",
        **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        self.refresh()
        wanted = set(_TOKEN_RE.findall(str(query or "").lower()))
        wanted |= {c for c in str(query or "") if "\u4e00" <= c <= "\u9fff"}
        if not self._index:
            return []

        scored: list[tuple[float, _IndexedFile]] = []
        for item in self._index:
            if media_type in ("video", "photo", "image"):
                want = MaterialType.VIDEO.value if media_type == "video" else MaterialType.IMAGE.value
                if item.media_type != want:
                    continue
            if not wanted:
                # 未给关键词：按路径序返回，分数给中性值（不冒充"相关"）
                scored.append((0.5, item))
                continue
            hit = len(wanted & item.tokens)
            if hit <= 0:
                continue
            score = min(0.95, 0.5 + 0.1 * hit)
            scored.append((score, item))

        scored.sort(key=lambda pair: (-pair[0], pair[1].rel))
        results: list[tuple[MaterialAsset, float]] = []
        for score, item in scored[: max(1, int(top_k))]:
            results.append((
                MaterialAsset(
                    id=f"{self.source_id}_{item.rel}",
                    title=Path(item.rel).stem,
                    type=MaterialType(item.media_type),
                    url=str(item.path),
                    thumbnail_url="",
                    tags=sorted(item.tokens)[:12],
                    source=self.source_id,
                    license="本地素材（授权由上传者负责）",
                    metadata={"local_path": str(item.path), "rel_path": item.rel},
                ),
                score,
            ))
        return results

    async def close(self) -> None:
        self._index = []


def source_from_env(env_var: str = "CLIPWRIGHT_LOCAL_MATERIAL_DIR") -> LocalFolderSource | None:
    """按环境变量建源；未配置或目录不存在返回 None（零贡献 + 可查原因）。"""
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.is_dir():
        logger.info("[LocalFolder] %s 指向的目录不存在：%s（不贡献素材）", env_var, raw)
        return None
    return LocalFolderSource(root)


__all__ = [
    "IMAGE_EXTS",
    "LocalFolderSource",
    "VIDEO_EXTS",
    "source_from_env",
]
