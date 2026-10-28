"""工作区：项目唯一产物根（PRD docs/workspace-prd-2026-10-24.md）。

一个项目 = 一个目录，工程文件、素材、下载、成片、缓存全在里面，可整体备份：

    <workspace_dir>/<slug>-<project_id 尾段>/
    ├── project.json / timeline.json / .workspace.json
    ├── materials/{video,image,audio}   # 系统下载 + 用户手动投放（同一个口）
    ├── assets/{files,thumbnails,index.json}
    ├── renders/  cache/  logs/

设计约束（与 PRD 一致）：

- **只有 materials/ 是用户写入区**，其余由系统写；
- 新素材判定先看 ``(相对路径, size, mtime)``，命中已知路径才跳过；内容去重靠 sha256；
- 老项目（``projects/<id>/assets``）采用"先复制后切换"，不删用户数据，留 ``MIGRATED_TO`` 指针；
- 根目录必须在 ``settings.workspace_dir`` 之内（或显式白名单），不接受任意绝对路径写盘。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger, settings

#: 素材扩展名 → 种类（materials/ 里按这个归类；materials 根目录下的也认）
MATERIAL_EXT: dict[str, str] = {
    **{e: "video" for e in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".ts")},
    **{e: "image" for e in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")},
    **{e: "audio" for e in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")},
}
#: 单次扫描上限（防止把上万文件的目录拖死）
MAX_SCAN_FILES = 5000
#: 事件环大小（供 SSE/轮询读取）
_MAX_EVENTS = 200

_EVENTS: dict[str, list[dict[str, Any]]] = {}


def workspace_root() -> Path:
    """工作区根目录（相对路径按仓库根锚定，和 renders/ 一致的约定）。"""
    from clipwright.paths import anchor

    root = anchor(settings.workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def kind_of(path: Path | str) -> str:
    return MATERIAL_EXT.get(Path(path).suffix.lower(), "")


def _to_markdown(title: str, payload: dict[str, Any] | str) -> str:
    """把简报/规划书渲染成 markdown（dict → 键值/列表分节；str 原样）。"""
    if isinstance(payload, str):
        body = payload
    else:
        lines: list[str] = []
        for key, value in (payload or {}).items():
            label = str(key)
            if isinstance(value, list):
                lines.append(f"## {label}\n")
                for idx, item in enumerate(value, 1):
                    if isinstance(item, dict):
                        summary = "；".join(
                            f"{k}: {v}" for k, v in item.items() if v not in (None, "", [], {})
                        )
                        lines.append(f"{idx}. {summary}")
                    else:
                        lines.append(f"{idx}. {item}")
                lines.append("")
            elif isinstance(value, dict):
                lines.append(f"## {label}\n")
                lines.extend(f"- {k}: {v}" for k, v in value.items())
                lines.append("")
            else:
                lines.append(f"- **{label}**: {value}")
        body = "\n".join(lines).strip()
    stamp = time.strftime("%Y-%m-%d %H:%M")
    return f"# {title}\n\n> 由系统生成（{stamp}），可读可改；修改请走对话或主 agent 的 edit_file。\n\n{body}\n"


def _slug(name: str, fallback: str = "workspace") -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", str(name or "").strip())
    text = text.strip("-_")[:40]
    return text or fallback


def add_event(project_id: str, level: str, message: str, detail: dict[str, Any] | None = None) -> None:
    """工作区事件（扫描/索引进度）。带 seq 游标，前端可增量读。"""
    box = _EVENTS.setdefault(project_id, [])
    box.append({
        "seq": (box[-1]["seq"] + 1) if box else 1,
        "ts": time.time(),
        "level": level,
        "message": message,
        "detail": detail or {},
    })
    if len(box) > _MAX_EVENTS:
        del box[: len(box) - _MAX_EVENTS]


def events(project_id: str, since: int = 0) -> list[dict[str, Any]]:
    return [e for e in _EVENTS.get(project_id, []) if e["seq"] > since]


def clear_events(project_id: str = "") -> None:
    if project_id:
        _EVENTS.pop(project_id, None)
    else:
        _EVENTS.clear()


@dataclass(frozen=True)
class WorkspaceDirs:
    root: Path

    @property
    def materials(self) -> Path:
        return self.root / "materials"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def renders(self) -> Path:
        return self.root / "renders"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    # ── 文档协议（PRD §17.2）：docs/ 人读文档，reports/ 子 agent 汇报，artifacts/ 不可变产物 ──
    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def meta(self) -> Path:
        return self.root / ".workspace.json"

    @property
    def registry(self) -> Path:
        return self.assets / "materials.json"

    def ensure(self) -> None:
        for d in (self.materials, self.assets, self.renders, self.cache, self.logs,
                  self.docs, self.reports, self.artifacts,
                  self.materials / "video", self.materials / "image", self.materials / "audio",
                  self.assets / "files", self.assets / "thumbnails"):
            d.mkdir(parents=True, exist_ok=True)


class WorkspaceError(RuntimeError):
    """工作区不可用（路径不合法 / 不可写 / 迁移失败）。"""


class Workspace:
    """单个项目的工作区管理器。"""

    def __init__(self, project_id: str) -> None:
        pid = str(project_id or "").strip()
        if not pid:
            raise WorkspaceError("project_id 不能为空")
        self.project_id = pid
        self._meta: dict[str, Any] | None = None

    # ------------------------------------------------------------------ 定位
    @property
    def meta(self) -> dict[str, Any] | None:
        if self._meta is None:
            self._meta = self._read_meta()
        return self._meta

    @property
    def exists(self) -> bool:
        return bool(self.meta)

    def dirs(self) -> WorkspaceDirs:
        meta = self.meta or {}
        root = Path(meta.get("root") or self._default_root())
        return WorkspaceDirs(root=root)

    def _default_root(self) -> Path:
        return workspace_root() / self._dir_name()

    def _dir_name(self) -> str:
        # 名字稳定、可预期：<slug>-<project_id 后 6 位>。改名不改目录。
        return f"{_slug(self.project_id)}-{self.project_id[-6:]}"

    def _read_meta(self) -> dict[str, Any] | None:
        for candidate in (self._default_root(), *self._scan_known_roots()):
            path = candidate / ".workspace.json"
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    logger.warning("工作区元数据损坏 %s: %s", path, e)
                    continue
                if str(data.get("project_id") or "") == self.project_id:
                    return data
        return None

    def _scan_known_roots(self) -> list[Path]:
        root = workspace_root()
        out: list[Path] = []
        try:
            for child in root.iterdir():
                if child.is_dir() and child.name.endswith(self.project_id[-6:]):
                    out.append(child)
        except OSError:
            pass
        return out

    # ------------------------------------------------------------------ 创建
    def ensure(
        self,
        *,
        name: str = "",
        auto_index: bool | None = None,
        vision: bool | None = None,
        root: Path | str | None = None,
    ) -> dict[str, Any]:
        """创建或读取工作区；老项目首次调用时做一次迁移。

        `root`：显式指定工作区的**父目录**（PRD §17.8）。优先级 **显式父目录 > .workspace.json > 默认根**——
        旧实现只认 `_default_root()`，于是 API 收下 `root` 也白搭（审计 P0-3）。
        实际目录仍是 `<父目录>/<slug>-<id>`，避免两个项目写同一个目录。
        """
        if self.exists:
            meta = dict(self.meta or {})
            # 显式 root 与既有元数据不一致 → 明确报错（不静默忽略，否则"选了目录却没生效"）
            if root is not None:
                existing = Path(str(meta.get("root") or "")).expanduser()
                wanted = Path(root).expanduser()
                try:
                    same = existing.resolve() == wanted.resolve()
                except OSError:
                    same = str(existing) == str(wanted)
                if not same:
                    raise WorkspaceError(
                        f"工作区已存在且根目录不同：{existing}（收到 {wanted}）；"
                        "换根只对新建工作区生效"
                    )
            changed = False
            if auto_index is not None and bool(meta["scan"]["auto"]) != bool(auto_index):
                meta["scan"]["auto"] = bool(auto_index)
                changed = True
            if vision is not None and bool(meta["index"]["vision"]) != bool(vision):
                meta["index"]["vision"] = bool(vision)
                changed = True
            if changed:
                self._write_meta(meta)
            self.dirs().ensure()
            return meta

        root_path = Path(root).expanduser() if root else None
        if root_path is not None:
            # 显式 root 是**父目录**：真正的工作区仍是 <root>/<slug>-<id>（避免两个项目撞同一目录）
            if root_path.parent == root_path:
                raise WorkspaceError(f"不接受盘根作为工作区父目录: {root_path}")
            root_path.mkdir(parents=True, exist_ok=True)
            root_path = root_path / self._dir_name()
        else:
            root_path = self._default_root()
        root_path.mkdir(parents=True, exist_ok=True)
        root = root_path
        dirs = WorkspaceDirs(root=root)
        dirs.ensure()
        meta = {
            "schema": 1,
            "project_id": self.project_id,
            "name": str(name or self.project_id),
            "root": str(root),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dirs": {"materials": "materials", "assets": "assets",
                     "renders": "renders", "cache": "cache"},
            "scan": {
                "auto": bool(settings.workspace_scan_enabled if auto_index is None else auto_index),
                "interval_sec": int(settings.workspace_scan_interval_sec or 20),
                "last_scan_at": "",
                "pending": 0,
            },
            "index": {
                "vision": bool(settings.workspace_vision_enabled if vision is None else vision),
                # provider 必须取**实时配置**：旧实现硬编码 "transformers"，
                # 设置页改成 llm 后面板还显示 transformers，用户看到的行为与配置不符。
                "provider": str(getattr(settings, "vision_provider", "none") or "none"),
                "provider_snapshot": str(getattr(settings, "vision_provider", "none") or "none"),
                "daily_budget_usd": float(settings.workspace_vision_daily_budget_usd or 0),
                "spent_today_usd": 0.0,
                "date": time.strftime("%Y-%m-%d"),
            },
            "limits": {
                "cache_max_gb": float(settings.workspace_cache_max_gb or 0),
                "logs_keep_days": 7,
            },
        }
        self._write_meta(meta)
        self._meta = meta
        add_event(self.project_id, "info", f"工作区已创建：{root}")
        self.migrate_legacy()
        return meta

    def _write_meta(self, meta: dict[str, Any]) -> None:
        root = Path(meta.get("root") or self._default_root())
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / f".workspace.json.{uuid.uuid4().hex[:6]}.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, root / ".workspace.json")
        self._meta = meta

    # ------------------------------------------------------------------ 迁移
    def migrate_legacy(self) -> dict[str, Any] | None:
        """把老布局 ``projects/<id>/assets`` 复制进工作区（不移动、不删源）。

        失败不抛：工作区仍可用，但返回 ``{"ok": False, "error": ...}``，
        调用方据此把项目标成"仍在旧布局"。
        """
        legacy = Path(settings.project_dir) / self.project_id / "assets"
        if not legacy.is_dir():
            return None
        target = self.dirs().assets
        if (target / "index.json").exists() or (target / "files").is_dir() and any((target / "files").iterdir()):
            return {"ok": True, "skipped": True}
        try:
            for sub in ("files", "thumbnails"):
                src = legacy / sub
                if src.is_dir():
                    shutil.copytree(src, target / sub, dirs_exist_ok=True)
            index = legacy / "index.json"
            if index.is_file():
                shutil.copy2(index, target / "index.json")
            (legacy.parent / "MIGRATED_TO").write_text(str(self.dirs().root) + "\n", encoding="utf-8")
            add_event(self.project_id, "info", "已从旧布局迁移素材到工作区（源目录保留）")
            return {"ok": True, "from": str(legacy), "to": str(target)}
        except Exception as e:  # noqa: BLE001 — 迁移失败要如实返回，不静默
            logger.warning("工作区迁移失败 %s: %s", self.project_id, e)
            add_event(self.project_id, "error", f"迁移失败：{e}")
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        meta = self.meta
        if not meta:
            return {"project_id": self.project_id, "exists": False, "health": "missing"}
        dirs = self.dirs()
        files = list(self.materials_files())
        by_kind = {"video": 0, "image": 0, "audio": 0, "other": 0}
        for f in files:
            by_kind[f["kind"] or "other"] = by_kind.get(f["kind"] or "other", 0) + 1
        registry = self.load_registry()
        known = registry.get("paths") or {}
        pending = [f for f in files
                   if (known.get(f["rel"]) or {}).get("mtime") != f["mtime"]
                   or (known.get(f["rel"]) or {}).get("size") != f["size"]]
        health = "ok"
        try:
            dirs.ensure()
        except OSError:
            health = "degraded"
        index_meta = dict(meta.get("index") or {})
        # 面板显示的必须是**当前生效**的 provider（元数据里的旧快照只作对照）
        live_provider = str(getattr(settings, "vision_provider", "none") or "none")
        if index_meta.get("provider") != live_provider:
            index_meta["provider_stale"] = index_meta.get("provider")
            index_meta["provider"] = live_provider
        return {
            "project_id": self.project_id,
            "exists": True,
            "name": meta.get("name", ""),
            "root": str(dirs.root),
            "counts": {
                "total": len(files),
                "video": by_kind["video"],
                "image": by_kind["image"],
                "audio": by_kind["audio"],
                "other": by_kind["other"],
                "indexed": len([m for m in (registry.get("materials") or {}).values()
                                if m.get("status") == "indexed"]),
                "pending": len(pending),
            },
            "index": index_meta,
            "scan": {**(meta.get("scan") or {}), "pending": len(pending)},
            "dirs": {k: str(dirs.root / v) for k, v in (meta.get("dirs") or {}).items()},
            "health": health,
        }

    # ------------------------------------------------------------------ 素材
    def materials_files(self) -> list[dict[str, Any]]:
        """列出 materials/ 下所有可识别素材（不读内容，只 stat）。"""
        dirs = self.dirs()
        base = dirs.materials
        if not base.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for current, _dirs, names in os.walk(base):
            for name in names:
                path = Path(current) / name
                kind = kind_of(path)
                if not kind:
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                out.append({
                    "path": str(path),
                    "rel": str(path.relative_to(base)).replace("\\", "/"),
                    "kind": kind,
                    "size": int(st.st_size),
                    "mtime": round(st.st_mtime, 3),
                })
                if len(out) >= MAX_SCAN_FILES:
                    add_event(self.project_id, "warning",
                              f"素材数量达到单次扫描上限 {MAX_SCAN_FILES}，其余下次再扫")
                    return out
        return out

    # ------------------------------------------------------------------ 注册表
    def load_registry(self) -> dict[str, Any]:
        path = self.dirs().registry
        if not path.is_file():
            return {"version": 1, "materials": {}, "paths": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏文件不阻断扫描，重建即可
            return {"version": 1, "materials": {}, "paths": {}}
        data.setdefault("materials", {})
        data.setdefault("paths", {})
        return data

    def save_registry(self, registry: dict[str, Any]) -> None:
        path = self.dirs().registry
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------ 工程文件
    def write_docs(self, brief: dict[str, Any] | str | None = None,
                   plan: dict[str, Any] | str | None = None) -> dict[str, str]:
        """把创意简报 / 制作规划书写成 markdown（PRD §17.4）。

        - `brief.md`：每次覆盖（它是"当前共识"，历史靠对话记录）；
        - `plan.md`：主版本；**每次修订前先把旧版复制成 `plan-vN.md`**（append-only，不丢历史）。
        返回 `{name: path}`；失败由调用方降级（`docs_error`）。
        """
        dirs = self.dirs()
        dirs.docs.mkdir(parents=True, exist_ok=True)
        out: dict[str, str] = {}

        if brief is not None:
            path = dirs.docs / "brief.md"
            path.write_text(_to_markdown("创意简报", brief), encoding="utf-8")
            out["brief"] = str(path)

        if plan is not None:
            plan_md = dirs.docs / "plan.md"
            if plan_md.is_file():
                # 版本化：旧版另存为 plan-vN.md（N 用独占创建占位，防并发撞名）
                archived = None
                for n in range(2, 1000):
                    candidate = dirs.docs / f"plan-v{n}.md"
                    try:
                        fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.close(fd)
                        archived = candidate
                        break
                    except FileExistsError:
                        continue
                    except OSError as exc:
                        logger.warning("规划书版本占位失败 %s: %s", candidate, exc)
                        break
                if archived is not None:
                    try:
                        shutil.copy2(plan_md, archived)
                        out["plan_prev"] = str(archived)
                    except OSError as exc:  # 复制失败不该阻断写入新版本
                        logger.warning("规划书旧版归档失败 %s: %s", archived, exc)
            plan_md.write_text(_to_markdown("制作规划书", plan), encoding="utf-8")
            out["plan"] = str(plan_md)

        if out:
            add_event(self.project_id, "info", "文档已更新：docs/",
                      {"files": list(out.keys())})
        return out

    def write_script(self, script_text: str) -> dict[str, str]:
        """把完整文案落成 `docs/script.md`（B8：文案 artifact 化）。

        为什么必须落盘：`TaskRequest.extra["script_text"]` 只有**截断后**的摘要能进
        上下文装载（预算有限），10k 字文案在提示词里活不下来。落成 artifact 后，
        上下文里给「路径 + 全长 + sha256 + 前 N 字」，子 agent 需要全文时按
        `input_refs` 取原文件，读完校验 hash 就知道有没有被截断或改过。

        返回 `{script: 绝对路径, script_chars: ..., script_sha256: ...}`（字符串值，
        便于直接并进 `extra`）。
        """
        dirs = self.dirs()
        dirs.docs.mkdir(parents=True, exist_ok=True)
        path = dirs.docs / "script.md"
        text = str(script_text or "")
        path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {
            "script": str(path),
            "script_chars": str(len(text)),
            "script_sha256": digest,
        }

    def write_project_files(self, project: dict[str, Any] | None = None,
                            timeline: dict[str, Any] | None = None) -> dict[str, str]:
        """把工程文件/时间线快照写到工作区（人可读、可 diff）。"""
        dirs = self.dirs()
        dirs.ensure()
        out: dict[str, str] = {}
        if project is not None:
            p = dirs.root / "project.json"
            p.write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
            out["project"] = str(p)
        if timeline is not None:
            p = dirs.root / "timeline.json"
            p.write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
            out["timeline"] = str(p)
        return out

    # ------------------------------------------------------------------ 日志
    def append_log(self, line: str, *, agent: str = "") -> str:
        """把一行日志追加到 ``logs/<YYYY-MM-DD>.log``（B11：logs/ 不再是空目录）。

        只做追加 + 单行清洗，不做轮转；轮转由 ``cleanup_logs`` 按 ``logs_keep_days`` 删除旧文件。
        """
        dirs = self.dirs()
        try:
            dirs.logs.mkdir(parents=True, exist_ok=True)
            path = dirs.logs / f"{time.strftime('%Y-%m-%d')}.log"
            stamp = time.strftime("%H:%M:%S")
            prefix = f"[{stamp}][{agent}] " if agent else f"[{stamp}] "
            text = str(line).replace("\r", " ").replace("\n", " ")[:2000]
            with path.open("a", encoding="utf-8") as fh:
                fh.write(prefix + text + "\n")
            return str(path)
        except OSError as exc:  # 日志失败绝不能影响主流程
            logger.debug("工作区日志写入失败 %s: %s", self.project_id, exc)
            return ""

    def cleanup_logs(self) -> int:
        """按 ``limits.logs_keep_days`` 删除过期日志；返回删除文件数。"""
        keep = int(((self.meta or {}).get("limits") or {}).get("logs_keep_days") or 7)
        logs = self.dirs().logs
        if keep <= 0 or not logs.is_dir():
            return 0
        cutoff = time.time() - keep * 86400
        removed = 0
        for path in logs.glob("*.log"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------------ 清理
    def cleanup_cache(self) -> int:
        """按 ``limits.cache_max_gb`` 从最旧开始删 cache/；返回删除字节数。"""
        meta = self.meta or {}
        limit_gb = float((meta.get("limits") or {}).get("cache_max_gb") or 0)
        if limit_gb <= 0:
            return 0
        cache = self.dirs().cache
        if not cache.is_dir():
            return 0
        files = sorted(
            (p for p in cache.rglob("*") if p.is_file()),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
        )
        total = sum(p.stat().st_size for p in files if p.exists())
        budget = int(limit_gb * (1 << 30))
        freed = 0
        for p in files:
            if total - freed <= budget:
                break
            try:
                size = p.stat().st_size
                p.unlink()
                freed += size
            except OSError:
                continue
        if freed:
            add_event(self.project_id, "info", f"已清理缓存 {freed / (1 << 20):.1f}MB")
        return freed


def ensure_workspace(project_id: str, *, name: str = "", auto_index: bool | None = None,
                     vision: bool | None = None) -> tuple[Workspace, dict[str, Any]]:
    """便捷入口：取/建工作区并确保目录存在。"""
    ws = Workspace(project_id)
    meta = ws.ensure(name=name, auto_index=auto_index, vision=vision)
    return ws, meta


def workspace_info(project_id: str) -> dict[str, Any]:
    try:
        return Workspace(project_id).status()
    except WorkspaceError as e:
        return {"project_id": project_id, "exists": False, "error": str(e), "health": "missing"}


__all__ = [
    "MATERIAL_EXT",
    "MAX_SCAN_FILES",
    "Workspace",
    "WorkspaceDirs",
    "WorkspaceError",
    "add_event",
    "clear_events",
    "ensure_workspace",
    "events",
    "kind_of",
    "workspace_info",
    "workspace_root",
]
