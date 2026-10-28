"""工作区 API（PRD docs/workspace-prd-2026-10-24.md §7.2）。

端点：

- ``POST   /api/workspace``                      创建/取回（幂等）
- ``GET    /api/workspace/{project_id}``         状态（计数/索引/预算/健康）
- ``POST   /api/workspace/{project_id}/scan``    触发扫描（dry_run 只列将新增）
- ``GET    /api/workspace/{project_id}/materials`` 素材列表
- ``POST   /api/workspace/{project_id}/materials`` 上传（写 materials/ 后排索引）
- ``DELETE /api/workspace/{project_id}/materials/{sha256}`` 只删索引记录（purge 才删盘）
- ``GET    /api/workspace/{project_id}/events``  SSE（扫描/索引进度）

安全：``root`` 只能落在 ``settings.workspace_dir`` 内（不接受任意绝对路径写盘）；
上传文件名做 basename 清洗，不允许 ``../``。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from clipwright.config import logger, settings
from clipwright.services import workspace as ws_mod
from clipwright.services.workspace import Workspace, WorkspaceError, kind_of

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


class CreateWorkspaceBody(dict):
    """（仅为文档可读性；实际用 Body(...) 收 dict）"""


def _ws(project_id: str) -> Workspace:
    try:
        return Workspace(project_id)
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _safe_name(name: str) -> str:
    base = Path(str(name or "")).name.replace("\\", "/").split("/")[-1].strip()
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="非法文件名")
    return base


def workspace_roots() -> list[Path]:
    """可选的工作区父目录集合（PRD §17.8）：`settings.workspace_roots` + 默认 `workspace_dir`。"""
    roots: list[Path] = []
    try:
        roots.append(ws_mod.workspace_root().resolve())
    except Exception:  # noqa: BLE001 — 默认根不可用不影响其它候选
        pass
    for raw in (getattr(settings, "workspace_roots", None) or []):
        try:
            p = Path(str(raw)).expanduser().resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)
        except Exception:  # noqa: BLE001
            continue
    return roots


def _check_root_allowed(root: str) -> Path:
    """显式 root 必须落在**某个允许的父目录**内（PRD §17.8，白名单从 1 个变成 1..N 个）。"""
    p = Path(root).expanduser().resolve()
    for allowed in workspace_roots():
        try:
            p.relative_to(allowed)
            return p
        except ValueError:
            continue
    allowed_txt = "、".join(str(r) for r in workspace_roots()) or "（无可用根）"
    raise HTTPException(
        status_code=400,
        detail=f"root 必须位于允许的父目录之一内（{allowed_txt}），收到 {p}",
    )


@router.get("/roots")
async def list_workspace_roots() -> dict[str, Any]:
    """可选的工作区父目录（前端 04 步下拉用，PRD §17.8）。"""
    items = []
    for p in workspace_roots():
        items.append({"path": str(p), "writable": _is_writable(p), "default": p == (
            Path(str(ws_mod.workspace_root())).resolve())})
    return {"roots": items}


def _is_writable(path: Path) -> bool:
    """试写探测：真正建/删一个临时文件，而不是看权限位（Windows 上权限位不可靠）。"""
    probe = path / f".write_probe_{uuid.uuid4().hex[:6]}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


@router.post("")
async def create_workspace(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """创建或取回工作区（幂等：已存在只更新开关）。"""
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id 不能为空")
    root = str(payload.get("root") or "").strip()
    root_path = _check_root_allowed(root) if root else None
    ws = _ws(project_id)
    try:
        meta = ws.ensure(
            name=str(payload.get("name") or ""),
            auto_index=payload.get("auto_index"),
            vision=payload.get("vision"),
            # 关键：把 root 真的传下去（审计 P0-3：旧实现只校验、不落地）
            root=root_path,
        )
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"工作区不可写：{e}") from e
    return ws.status() | {"meta": meta}


@router.get("/{project_id}")
async def get_workspace(project_id: str) -> dict[str, Any]:
    ws = _ws(project_id)
    status = ws.status()
    if not status.get("exists"):
        raise HTTPException(status_code=404, detail="工作区不存在（先 POST /api/workspace 创建）")
    return status


@router.post("/{project_id}/enable")
async def enable_workspace(project_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """开关：自动扫描 / 视觉解读（不重建目录）。"""
    ws = _ws(project_id)
    if not ws.exists:
        raise HTTPException(status_code=404, detail="工作区不存在")
    try:
        meta = ws.ensure(auto_index=payload.get("auto_index"), vision=payload.get("vision"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ws.status() | {"meta": meta}


@router.post("/{project_id}/scan")
async def scan_workspace(project_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """触发扫描；``dry_run=true`` 只列出将新增的文件，不写注册表。"""
    from clipwright.services.workspace_index import scan_and_index

    ws = _ws(project_id)
    if not ws.exists:
        raise HTTPException(status_code=404, detail="工作区不存在")
    report = await scan_and_index(
        ws,
        dry_run=bool(payload.get("dry_run")),
        vision=payload.get("vision"),
    )
    return report


@router.get("/{project_id}/reports")
async def list_workspace_reports(project_id: str, limit: int = Query(default=50)) -> dict[str, Any]:
    """列出工作区报告（PRD §19.6 / docs 标签数据源）。

    只返回索引与 preview；正文用 `GET .../reports/{seq}`（`reports/INDEX.md` 只是目录，
    不是数据通道——子 agent 不得读它，只有主 agent/前端展示用）。
    """
    from clipwright.services import stage_report

    ws = _ws(project_id)
    if not ws.exists:
        raise HTTPException(status_code=404, detail="工作区不存在")
    items = stage_report.list_reports(ws.dirs().root, limit=int(limit or 50))
    return {"project_id": project_id, "root": str(ws.dirs().root),
            "count": len(items), "reports": items}


@router.get("/{project_id}/reports/{seq}")
async def read_workspace_report(project_id: str, seq: int,
                                max_chars: int = Query(default=20000)) -> dict[str, Any]:
    """读一份报告正文（默认 20k 字上限）。"""
    from clipwright.services import stage_report

    ws = _ws(project_id)
    if not ws.exists:
        raise HTTPException(status_code=404, detail="工作区不存在")
    for item in stage_report.list_reports(ws.dirs().root, limit=500):
        if int(item.get("seq") or 0) == int(seq):
            path = Path(str(item["path"]))
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                raise HTTPException(status_code=409, detail=f"读取报告失败：{e}") from e
            limit = max(200, int(max_chars or 20000))
            return {
                "project_id": project_id, "seq": seq, "path": str(path),
                "truncated": len(text) > limit, "content": text[:limit],
            }
    raise HTTPException(status_code=404, detail=f"报告 #{seq} 不存在")


@router.get("/{project_id}/docs")
async def list_workspace_docs(project_id: str, max_chars: int = Query(default=20000)) -> dict[str, Any]:
    """列出 `docs/*.md`（创意简报 / 制作规划书，PRD §17.4 / §19.6）。

    `doc_write` 事件到达时前端重新拉一次即可；内容只读（编辑权在主 agent 的 `edit_file`）。
    """
    ws = _ws(project_id)
    if not ws.exists:
        raise HTTPException(status_code=404, detail="工作区不存在")
    docs_dir = ws.dirs().docs
    items: list[dict[str, Any]] = []
    if docs_dir.is_dir():
        limit = max(200, int(max_chars or 20000))
        for path in sorted(docs_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            items.append({
                "name": path.name,
                "path": str(path),
                "bytes": len(text.encode("utf-8")),
                "updated_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)),
                "content": text[:limit],
                "truncated": len(text) > limit,
            })
    return {"project_id": project_id, "root": str(ws.dirs().root),
            "count": len(items), "docs": items}


@router.get("/{project_id}/materials")
async def list_materials(project_id: str, kind: str = Query(default="")) -> dict[str, Any]:
    ws = _ws(project_id)
    if not ws.exists:
        raise HTTPException(status_code=404, detail="工作区不存在")
    registry = ws.load_registry()
    items = list((registry.get("materials") or {}).values())
    if kind:
        items = [m for m in items if m.get("kind") == kind]
    items.sort(key=lambda m: str(m.get("indexed_at") or ""), reverse=True)
    return {
        "project_id": project_id,
        "root": str(ws.dirs().root),
        "count": len(items),
        "items": items[:500],
        "pending": ws.status()["counts"]["pending"],
    }


@router.post("/{project_id}/materials")
async def upload_material(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """上传到 ``materials/<kind>/``，随后同步排一次索引（单文件）。"""
    from clipwright.services.workspace_index import scan_and_index

    ws = _ws(project_id)
    if not ws.exists:
        raise HTTPException(status_code=404, detail="工作区不存在")
    name = _safe_name(file.filename or f"upload_{uuid.uuid4().hex[:6]}")
    kind = kind_of(name)
    if not kind:
        raise HTTPException(status_code=400, detail=f"不支持的素材类型：{name}")
    target = ws.dirs().materials / kind / name
    target.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    target.write_bytes(data)
    ws_mod.add_event(project_id, "info", f"收到上传：{name}", {"kind": kind, "bytes": len(data)})
    report = await scan_and_index(ws)
    return {"project_id": project_id, "saved": str(target), "kind": kind, "scan": report}


@router.delete("/{project_id}/materials/{sha256}")
async def delete_material(project_id: str, sha256: str,
                          purge: bool = Query(default=False)) -> dict[str, Any]:
    """默认**只删索引记录**（磁盘文件保留）；``purge=true`` 才删文件。"""
    ws = _ws(project_id)
    registry = ws.load_registry()
    entry = (registry.get("materials") or {}).get(sha256)
    if not entry:
        raise HTTPException(status_code=404, detail="素材不存在")
    removed_file = ""
    if purge:
        path = Path(str(entry.get("path") or ""))
        try:
            if path.is_file():
                path.unlink()
                removed_file = str(path)
        except OSError as e:
            raise HTTPException(status_code=409, detail=f"删除文件失败：{e}") from e
    (registry.get("materials") or {}).pop(sha256, None)
    for rel, seen in list((registry.get("paths") or {}).items()):
        if seen.get("sha256") == sha256:
            (registry.get("paths") or {}).pop(rel, None)
    ws.save_registry(registry)
    ws_mod.add_event(project_id, "info", f"已移除素材记录 {sha256[:8]}",
                     {"purge": purge, "file": removed_file})
    return {"ok": True, "sha256": sha256, "purged": bool(purge), "removed_file": removed_file}


@router.get("/{project_id}/events")
async def workspace_events(project_id: str, since: int = Query(default=0)) -> dict[str, Any]:
    """拉取工作区事件（**JSON，立即返回**）。

    旧实现只有 SSE 版本，普通 GET 会挂到客户端超时（实测 120s 挂死）。
    这里拆成两个端点：本端点返回数组，`/events/stream` 才是长连接。
    """
    _ws(project_id)
    batch = ws_mod.events(project_id, since=int(since or 0))
    return {
        "project_id": project_id,
        "events": batch,
        "last_seq": int(batch[-1]["seq"]) if batch else int(since or 0),
    }


@router.get("/{project_id}/events/stream")
async def workspace_events_stream(project_id: str,
                                  since: int = Query(default=0)) -> StreamingResponse:
    """SSE：和 pipeline trace 同构（seq 游标 + data: 帧）。仅在显式订阅时挂起。"""
    _ws(project_id)

    async def gen():
        cursor = int(since or 0)
        idle = 0
        while True:
            batch = ws_mod.events(project_id, since=cursor)
            for ev in batch:
                cursor = ev["seq"]
                yield f"id: {cursor}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                idle = 0
            if not batch:
                idle += 1
                if idle % 15 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


__all__ = ["router"]
