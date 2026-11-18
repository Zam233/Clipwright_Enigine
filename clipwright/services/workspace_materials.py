"""工作区生成图素材源 —— 让主 Agent 生成/导入的图片被素材阶段检索到。

PRD（image-provider-plugin §5 验收第 4 条）：生成图落在 `<workspace>/materials/image/`，
不经过旧 `PluginData/generated.json` 旁路；`MaterialRegistry.search` 里需要一个能扫
工作区的来源。`image_generate` 工具在生成后调用 :func:`ensure_workspace_source`，
把该项目的素材源注册进 `MaterialRegistry`（幂等）。

只读、只扫图片；文件名/相邻 notes 不参与元数据编造——标题就是文件名。
"""

from __future__ import annotations

import re
from pathlib import Path

from clipwright.config import logger
from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialType

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_TOKEN = re.compile(r"[\s,，。;；、/|()（）\[\]_]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.split(text or "") if t}


class WorkspaceImageSource(MaterialSource):
    """把某个工作区 `materials/image/` 下的文件当作素材源。"""

    def __init__(self, root: Path, source_id: str) -> None:
        self.root = Path(root)
        self.source_id = source_id
        self.source_name = "项目工作区（生成图）"

    def _files(self) -> list[Path]:
        base = self.root / "materials" / "image"
        if not base.is_dir():
            return []
        return sorted(
            (p for p in base.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXT),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )[:200]

    def _asset(self, path: Path) -> MaterialAsset:
        rel = path.relative_to(self.root).as_posix()
        return MaterialAsset(
            id=f"ws_img_{rel}",
            title=path.stem,
            type=MaterialType.IMAGE,
            local_path=str(path),
            file_size_bytes=path.stat().st_size,
            tags=[path.stem],
            source=self.source_id,
            metadata={"source": "workspace", "rel_path": rel},
        )

    async def search(self, query: str, top_k: int = 10, **kwargs) -> list:
        q = _tokens(str(query or ""))
        out: list[tuple[MaterialAsset, float]] = []
        for path in self._files():
            name = path.stem.lower()
            score = 0.0
            if q:
                hit = len(q & _tokens(name))
                if not hit:
                    continue
                score = min(1.0, hit / max(1, len(q)))
            else:
                score = 0.1
            out.append((self._asset(path), round(score, 4)))
            if len(out) >= top_k:
                break
        return out

    async def count(self) -> int:
        return len(self._files())

    async def list_all(self) -> list[MaterialAsset]:
        return [self._asset(p) for p in self._files()]


def _source_id_for(root: Path) -> str:
    import hashlib

    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"workspace_generated_{digest}"


def ensure_workspace_source(root: str | Path) -> str:
    """注册（或复用）该工作区的图片素材源；返回 source_id。"""
    from clipwright.material.registry import MaterialRegistry

    path = Path(root)
    sid = _source_id_for(path)
    existing = MaterialRegistry.get(sid) if hasattr(MaterialRegistry, "get") else None
    if existing is None:
        try:
            MaterialRegistry.register(WorkspaceImageSource(path, sid))
        except Exception as exc:  # noqa: BLE001 — 注册失败只影响可见性，不阻断生成
            logger.warning("工作区素材源注册失败: %s", exc)
            return ""
    return sid
