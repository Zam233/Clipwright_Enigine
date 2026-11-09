"""插件只读素材视图（M3）。

`<project>/materials/` 是用户/系统的唯一写入区；插件只能**读**：
- `list_materials`：名称/ref/mime/size/sha256；
- `read_material`：返回 base64 内容（限 4MB），供插件导入场景；
- 拒绝绝对路径、`..`、符号链接逃逸；写操作不提供。

engine 不解析素材语义，只做通用路径与大小校验。
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path

from clipwright.services.workspace import Workspace

#: 单次读取上限（与请求帧 8MiB 留出 base64 膨胀余量）
MAX_READ_BYTES = 4 * 1024 * 1024
#: 列表上限
MAX_LIST = 100


class MaterialError(PermissionError):
    pass


def materials_root(project_id: str) -> Path:
    return Workspace(project_id).dirs().materials


def _resolve(project_id: str, ref: str) -> Path:
    raw = str(ref or "").strip().replace("\\", "/")
    if not raw:
        raise MaterialError("素材 ref 不能为空")
    if raw.startswith("materials/"):
        raw = raw[len("materials/"):]
    if raw.startswith("/") or ":" in raw.split("/")[0]:
        raise MaterialError(f"拒绝绝对路径: {ref!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise MaterialError(f"拒绝路径穿越: {ref!r}")
    root = materials_root(project_id)
    root.mkdir(parents=True, exist_ok=True)
    target = (root / Path(*parts)).resolve()
    root_real = root.resolve()
    if not (target == root_real or root_real in target.parents):
        raise MaterialError(f"素材路径越界: {ref!r}")
    return target


def list_materials(project_id: str, *, limit: int = 32) -> list[dict]:
    if not project_id:
        raise MaterialError("project_id 不能为空")
    root = materials_root(project_id)
    if not root.is_dir():
        return []
    limit = max(1, min(int(limit or 32), MAX_LIST))
    items: list[dict] = []
    for path in sorted(root.rglob("*")):
        if len(items) >= limit:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
        except OSError:
            continue
        mime, _ = mimetypes.guess_type(str(path))
        items.append({
            "name": path.name,
            "ref": f"materials/{rel}",
            "mime": mime or "application/octet-stream",
            "size": size,
            "sha256": digest,
        })
    return items


def read_material(
    project_id: str, ref: str, *, max_bytes: int = MAX_READ_BYTES,
) -> dict:
    if not project_id:
        raise MaterialError("project_id 不能为空")
    path = _resolve(project_id, ref)
    if not path.is_file():
        raise FileNotFoundError(f"素材不存在: {ref}")
    size = path.stat().st_size
    limit = max(1, min(int(max_bytes or MAX_READ_BYTES), MAX_READ_BYTES))
    if size > limit:
        raise ValueError(f"素材超过读取上限 {limit} 字节: {ref}")
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(str(path))
    return {
        "name": path.name,
        "ref": f"materials/{path.relative_to(materials_root(project_id)).as_posix()}",
        "mime": mime or "application/octet-stream",
        "size": size,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
    }


__all__ = [
    "MaterialError", "MAX_LIST", "MAX_READ_BYTES",
    "list_materials", "materials_root", "read_material",
]
