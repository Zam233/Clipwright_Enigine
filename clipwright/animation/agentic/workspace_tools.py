"""Read-only workspace tools for AnimationAgent."""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any


def _safe(root: str, rel: str) -> Path:
    base = Path(root).resolve()
    path = (base / rel).resolve()
    if path != base and base not in path.parents:
        raise ValueError("path escapes workspace")
    return path


def workspace_tree(root: str = ".", glob: str = "**/*", max_depth: int = 6,
                   include_dirs: bool = False) -> dict[str, Any]:
    base = Path(root).resolve()
    out: list[dict[str, Any]] = []
    for p in base.rglob("*"):
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        if len(rel.parts) > max_depth:
            continue
        if p.is_dir():
            if include_dirs:
                out.append({"path": str(rel), "kind": "dir"})
            continue
        if not fnmatch.fnmatch(str(rel), glob):
            continue
        kind = "image" if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else "file"
        out.append({"path": str(rel), "kind": kind, "bytes": p.stat().st_size})
    return {"root": str(base), "entries": out[:5000]}


def read_file(root: str, path: str, mode: str = "text") -> dict[str, Any]:
    p = _safe(root, path)
    if not p.is_file():
        raise FileNotFoundError(path)
    if mode == "image" or p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        from PIL import Image
        with Image.open(p) as im:
            width, height = im.size
        return {"path": str(p.relative_to(Path(root).resolve())), "kind": "image",
                "mime": f"image/{p.suffix.lstrip('.').lower()}", "width": width, "height": height,
                "artifact_ref": str(p)}
    data = p.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": str(p), "kind": "binary", "bytes": len(data), "artifact_ref": str(p)}
    return {"path": str(p), "kind": "text", "text": text, "bytes": len(data)}
