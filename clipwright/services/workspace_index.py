"""工作区素材索引（PRD docs/workspace-prd-2026-10-24.md §5）。

职责只有三件：

1. **发现**：把 ``materials/`` 里新增/变化的文件挑出来（``(路径,size,mtime)`` 快筛），
   用 sha256 做内容去重（改名/移动不产生新记录）；
2. **索引**：复用现成能力——``MediaIndexer.extract_metadata/classify``、
   ``AssetManager.import_file``（落 assets/files + 缩略图）、
   ``content_vision.label_shot``（CLIP 本地标签 / LLM 描述）；
3. **记账**：写 ``assets/materials.json`` 注册表；视觉按次计费的部分走日预算闸，
   超限的标 ``deferred``，**索引本身仍然完成**（元数据/标签可用）。

不新写算法，也不改 ``content_index`` 的既有索引格式——工作区注册表是**另一层**
（面向"这个文件，索引到哪一步了"），需要进检索时再走 ``AssetManager`` 的资产。
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Any

from clipwright.config import logger, settings
from clipwright.services.workspace import Workspace, add_event, kind_of

#: 单次视觉调用的成本估计（provider=llm 时用于日预算闸；真实账单以 provider 为准）
VISION_LLM_CALL_USD = 0.01
#: 单文件最多送几帧给视觉（控制成本）
MAX_FRAMES_PER_FILE = 3


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _roll_index_day(meta: dict[str, Any]) -> dict[str, Any]:
    index = dict(meta.get("index") or {})
    if index.get("date") != _today():
        index["date"] = _today()
        index["spent_today_usd"] = 0.0
    return index


def vision_budget(ws: Workspace) -> tuple[bool, float, float]:
    """返回 ``(是否还能花, 已花, 上限)``。"""
    meta = ws.meta or {}
    index = _roll_index_day(dict(meta))
    limit = float(index.get("daily_budget_usd") or 0)
    spent = float(index.get("spent_today_usd") or 0)
    if limit <= 0:
        return True, spent, limit  # 0 = 不限制
    return spent + VISION_LLM_CALL_USD <= limit, spent, limit


def _spend(ws: Workspace, amount: float) -> None:
    if amount <= 0:
        return
    meta = dict(ws.meta or {})
    index = _roll_index_day(meta)
    index["spent_today_usd"] = round(float(index.get("spent_today_usd") or 0) + amount, 6)
    meta["index"] = index
    try:
        ws._write_meta(meta)  # noqa: SLF001 — 同一模块族内复用（避免再开一份写盘逻辑）
    except Exception:  # noqa: BLE001 — 记账失败不影响索引结果
        pass


def _grab_frames(path: Path, kind: str, out_dir: Path, limit: int = MAX_FRAMES_PER_FILE) -> list[str]:
    """抽 1–3 帧送视觉；失败返回空（visual 标 skipped，不阻断索引）。

    帧文件名用 **sha8** 而不是素材 stem：中文 stem 会让下游 OpenCV/某些模型读图失败
    （实测 `can't open/read file`），而且同名不同素材会互相覆盖帧文件。
    """
    if kind == "image":
        return [str(path)]
    if kind != "video":
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        _tag = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:8]
    except Exception:  # noqa: BLE001
        _tag = "frame"
    frames: list[str] = []
    for i, at in enumerate(("00:00:01", "00:00:03", "00:00:06")[:limit]):
        out = out_dir / f"{_tag}_{i}.jpg"
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", at, "-i", str(path),
                 "-frames:v", "1", "-q:v", "3", str(out)],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:  # noqa: BLE001
            continue
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            frames.append(str(out))
    return frames


async def _vision_for(ws: Workspace, path: Path, kind: str, *, enabled: bool) -> dict[str, Any]:
    """视觉解读：provider=llm 才产生费用；transformers/none 只做本地。"""
    if not enabled:
        return {"status": "skipped", "reason": "vision_disabled"}
    provider = str(getattr(settings, "vision_provider", "none") or "none")
    if provider == "none":
        return {"status": "skipped", "reason": "provider=none"}

    allow, spent, limit = vision_budget(ws)
    if provider == "llm" and not allow:
        return {"status": "deferred", "reason": "daily_budget", "spent_today_usd": spent,
                "budget_usd": limit}

    frames = await asyncio.to_thread(_grab_frames, path, kind, ws.dirs().cache / "vision")
    if not frames:
        return {"status": "skipped", "reason": "no_frames"}
    try:
        from clipwright.services.content_vision import label_shot

        result = await label_shot(
            frames,
            use_clip=bool(getattr(settings, "content_index_clip_enabled", True)),
            use_llm=(provider == "llm"),
            # 工作区的核心诉求是"这个文件画面里有什么"，必须拿到一句描述，
            # 不能因为 CLIP 给了几个词就跳过描述（否则面板"画面"列永远空）
            want_description=True,
        )
    except Exception as e:  # noqa: BLE001 — 视觉失败不影响索引
        logger.warning("工作区视觉解读失败 %s: %s", path.name, e)
        return {"status": "failed", "provider": provider, "error": str(e)[:200]}

    if provider == "llm":
        _spend(ws, VISION_LLM_CALL_USD)
    visual = dict((result or {}).get("visual") or {})
    tags = [str(t) for t in (visual.get("tags") or [])][:12]
    summary = str(visual.get("description") or visual.get("summary") or "").strip()
    # 来源决定 status：只有 llm / clip / classifier 才算"真看过画面"；
    # filename 只是把文件名+宽高拼成一句话，旧实现把它报成 ok，等于骗人。
    source = str(visual.get("source") or "")
    if not source:
        src_evidence = str((visual.get("evidence") or {}).get("visual.tags") or "")
        source = "clip" if src_evidence.startswith("clip") else (
            "classifier" if src_evidence.startswith("classifier/") else "filename")
    degraded = source == "filename"
    return {
        "status": "degraded" if degraded else ("ok" if (tags or summary) else "empty"),
        "provider": provider,
        "source": source,
        "tags": tags,
        "summary": summary[:500],
        "confidence": (result or {}).get("confidence") or {},
        "evidence": visual.get("evidence") or {},
        "degraded_reason": "filename_fallback" if degraded else "",
        "cost_usd": VISION_LLM_CALL_USD if provider == "llm" else 0.0,
    }


async def index_one(ws: Workspace, item: dict[str, Any], registry: dict[str, Any],
                    *, vision: bool) -> dict[str, Any]:
    """索引单个素材（元数据 + 标签 + 资产登记 + 视觉）。返回注册表条目。"""
    path = Path(item["path"])
    sha = item["sha256"]
    entry: dict[str, Any] = {
        "sha256": sha,
        "path": str(path),
        "rel": item["rel"],
        "kind": item["kind"],
        "size": item["size"],
        "mtime": item["mtime"],
        "status": "indexed",
        "tags": [],
        "meta": {},
        "vision": {"status": "skipped", "reason": "not_run"},
        "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # 1) 元数据 + 粗标签（复用 MediaIndexer；失败只降级，不丢文件）
    try:
        from clipwright.services.indexer import MediaIndexer

        meta = await MediaIndexer.extract_metadata(str(path))
        entry["meta"] = dict(meta or {})
        try:
            entry["tags"] = [str(t) for t in await MediaIndexer.classify(str(path), entry["meta"])][:12]
        except Exception:  # noqa: BLE001
            entry["tags"] = []
    except Exception as e:  # noqa: BLE001
        entry["status"] = "failed"
        entry["error"] = f"metadata: {str(e)[:200]}"
        logger.warning("工作区索引元数据失败 %s: %s", path.name, e)

    # 2) 资产登记（落 workspace/assets；sha256 由 AssetManager 再去重一次）
    try:
        from clipwright.services.asset_manager import AssetManager

        info = await AssetManager(project_id=None, workspace_root=ws.dirs().root).import_file(path)
        if info is not None and getattr(info, "asset_id", ""):
            entry["asset_id"] = info.asset_id
            entry["asset_path"] = getattr(info, "file_path", "")
            entry["thumb_path"] = getattr(info, "thumbnail_path", "")
        else:
            entry["asset_error"] = str(getattr(info, "error", "") or "登记失败")
    except Exception as e:  # noqa: BLE001
        entry["asset_error"] = str(e)[:200]

    # 3) 视觉解读
    entry["vision"] = await _vision_for(ws, path, item["kind"], enabled=vision)

    registry.setdefault("materials", {})[sha] = entry
    registry.setdefault("paths", {})[item["rel"]] = {
        "sha256": sha, "size": item["size"], "mtime": item["mtime"],
    }
    return entry


async def scan_and_index(
    ws: Workspace,
    *,
    dry_run: bool = False,
    vision: bool | None = None,
) -> dict[str, Any]:
    """扫描 materials/ 并索引新增/变化文件。返回可展示的报告。"""
    from clipwright.services.content_index import sha256_of

    meta = ws.meta or {}
    enabled_vision = bool((meta.get("index") or {}).get("vision", True)) if vision is None else bool(vision)
    files = ws.materials_files()
    registry = ws.load_registry()
    known_paths = registry.get("paths") or {}

    candidates: list[dict[str, Any]] = []
    for f in files:
        seen = known_paths.get(f["rel"]) or {}
        if seen.get("size") == f["size"] and seen.get("mtime") == f["mtime"]:
            continue
        candidates.append(f)

    report: dict[str, Any] = {
        "scanned": len(files),
        "changed": len(candidates),
        "new": 0,
        "deduped": 0,
        "indexed": 0,
        "deferred": 0,
        "failed": 0,
        "dry_run": bool(dry_run),
        "items": [],
    }
    if not candidates:
        _touch_scan(ws)
        return report

    planned: list[dict[str, Any]] = []
    for f in candidates:
        try:
            sha = await asyncio.to_thread(sha256_of, f["path"])
        except Exception as e:  # noqa: BLE001
            report["failed"] += 1
            report["items"].append({"rel": f["rel"], "status": "failed", "error": str(e)[:120]})
            continue
        if sha in (registry.get("materials") or {}) or sha in {p.get("sha256") for p in known_paths.values()}:
            # 内容重复（改名/移动/重复投放）：只更新路径映射
            registry.setdefault("paths", {})[f["rel"]] = {
                "sha256": sha, "size": f["size"], "mtime": f["mtime"],
            }
            report["deduped"] += 1
            continue
        report["new"] += 1
        planned.append({**f, "sha256": sha})

    if dry_run:
        report["items"] = [{"rel": p["rel"], "kind": p["kind"], "status": "would_index"} for p in planned]
        return report

    for item in planned:
        entry = await index_one(ws, item, registry, vision=enabled_vision)
        if entry.get("vision", {}).get("status") == "deferred":
            report["deferred"] += 1
        if entry["status"] == "indexed":
            report["indexed"] += 1
        else:
            report["failed"] += 1
        report["items"].append({
            "rel": entry["rel"], "kind": entry["kind"], "status": entry["status"],
            "tags": entry.get("tags") or [],
            "vision": (entry.get("vision") or {}).get("status"),
        })
        if report["indexed"] and report["indexed"] % 5 == 0:
            add_event(ws.project_id, "info", f"已索引 {report['indexed']} 个素材")

    ws.save_registry(registry)
    _touch_scan(ws)
    add_event(
        ws.project_id, "info",
        f"扫描完成：{report['new']} 新增 / {report['indexed']} 已索引 / "
        f"{report['deduped']} 去重 / {report['failed']} 失败",
        {"report": {k: v for k, v in report.items() if k != "items"}},
    )
    # B11：logs/ 不能是空目录——索引结果留一份可追溯的流水
    ws.append_log(
        f"scan new={report['new']} indexed={report['indexed']} "
        f"deduped={report['deduped']} failed={report['failed']} "
        f"deferred={report.get('deferred', 0)} vision={'on' if enabled_vision else 'off'}",
        agent="workspace_index",
    )
    return report


def _touch_scan(ws: Workspace) -> None:
    meta = dict(ws.meta or {})
    scan = dict(meta.get("scan") or {})
    scan["last_scan_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    scan["pending"] = 0
    meta["scan"] = scan
    try:
        ws._write_meta(meta)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


def register_upload(ws: Workspace, path: Path, *, vision: bool | None = None) -> dict[str, Any]:
    """上传接口用：把文件放好后的同步登记（元数据/视觉走同一流水线，异步由调用方调度）。"""
    return {"path": str(path), "kind": kind_of(path), "vision": vision}


__all__ = [
    "MAX_FRAMES_PER_FILE",
    "VISION_LLM_CALL_USD",
    "index_one",
    "scan_and_index",
    "vision_budget",
]
