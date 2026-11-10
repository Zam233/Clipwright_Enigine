"""插件作用域工作区（M2）—— 只暴露通用文件操作，不解释插件领域格式。

边界（ADR-1）：
- 根目录 = `<project-root>/plugins/<plugin_id>/`；engine 的项目根快照
  `project.json` / `timeline.json` / `.workspace.json` 在上一级，插件不可触及。
- 所有路径都是**插件命名空间内的相对路径**；绝对路径、`..`、盘符、UNC、
  reparse point 逃逸一律拒绝。
- 写是原子的（临时文件 + `os.replace`），并返回内容 revision（sha256），
  支持 `expected_revision` 乐观并发。
- 不解析文件内容：`.tsx`、`.json`、`.py` 对这里都只是字节。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.services.workspace import Workspace

#: 单文件读取上限（字节）；插件协议帧上限 8MiB，这里留出 JSON 包装余量
MAX_READ_BYTES = 4 * 1024 * 1024
#: 单次写入上限
MAX_WRITE_BYTES = 4 * 1024 * 1024
#: 目录列举上限
MAX_LIST_ENTRIES = 2000


class WorkspaceScopeError(PermissionError):
    """路径越界 / 插件与项目不匹配。"""


class WorkspaceRevisionConflict(RuntimeError):
    """写入时 expected_revision 与当前内容不一致。"""


@dataclass(frozen=True)
class PluginScope:
    """一次调用的归属：由宿主从 tool/render 请求的 scope 派生，插件不可伪造。"""

    plugin_id: str
    project_id: str
    task_id: str = ""
    lease_id: str = ""

    @classmethod
    def from_payload(cls, scope: dict[str, Any] | None, *, expected_plugin: str) -> "PluginScope":
        data = dict(scope or {})
        plugin_id = str(data.get("plugin_id") or expected_plugin or "").strip()
        if plugin_id != expected_plugin:
            raise WorkspaceScopeError(
                f"scope.plugin_id={plugin_id!r} 与当前插件 {expected_plugin!r} 不匹配"
            )
        project_id = str(data.get("project_id") or "").strip()
        if not project_id:
            raise WorkspaceScopeError("scope.project_id 不能为空")
        return cls(
            plugin_id=plugin_id,
            project_id=project_id,
            task_id=str(data.get("task_id") or ""),
            lease_id=str(data.get("lease_id") or ""),
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject(reason: str) -> None:
    raise WorkspaceScopeError(reason)


class PluginWorkspace:
    """`<project-root>/plugins/<plugin_id>/` 的受限读写视图。"""

    def __init__(self, scope: PluginScope) -> None:
        self.scope = scope
        project = Workspace(scope.project_id)
        # 项目工作区可能尚未创建：dirs() 会回落到默认 root，随后按需 mkdir。
        self.project_root = project.dirs().root
        self.root = self.project_root / "plugins" / scope.plugin_id

    # ── 路径解析 ──────────────────────────────────────
    def resolve(self, relative: str, *, must_exist_parent: bool = True) -> Path:
        raw = str(relative or "").strip().replace("\\", "/")
        if not raw:
            _reject("路径不能为空")
        if raw.startswith("/") or ":" in raw.split("/")[0]:
            _reject(f"拒绝绝对路径或盘符: {relative!r}")
        parts = [p for p in raw.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            _reject(f"拒绝路径穿越: {relative!r}")

        self.root.mkdir(parents=True, exist_ok=True)
        root_real = os.path.realpath(self.root)
        target = Path(os.path.realpath(self.root / Path(*parts)))
        try:
            target.relative_to(root_real)
        except ValueError:
            _reject(f"路径逃逸出插件命名空间: {relative!r}")
        if must_exist_parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def rel_of(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # ── 读写 ──────────────────────────────────────────
    def list(self, relative: str = ".") -> list[dict[str, Any]]:
        base = self.resolve(relative) if relative not in ("", ".") else self._ensure_root()
        if not base.exists():
            return []
        if not base.is_dir():
            _reject(f"不是目录: {relative!r}")
        out: list[dict[str, Any]] = []
        for child in sorted(base.iterdir(), key=lambda p: p.name)[:MAX_LIST_ENTRIES]:
            try:
                stat = child.stat()
            except OSError:
                continue
            out.append({
                "name": child.name,
                "path": self.rel_of(child),
                "type": "dir" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else 0,
                "mtime": stat.st_mtime,
            })
        return out

    def read(self, relative: str) -> dict[str, Any]:
        path = self.resolve(relative)
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在: {relative}")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise ValueError(f"文件超过读取上限 {MAX_READ_BYTES} 字节: {relative}")
        data = path.read_bytes()
        return {
            "path": self.rel_of(path),
            "size": size,
            "sha256": _sha256(data),
            "data": data.decode("utf-8", errors="replace"),
        }

    def write_atomic(
        self,
        relative: str,
        data: str | bytes,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        if len(payload) > MAX_WRITE_BYTES:
            raise ValueError(f"内容超过写入上限 {MAX_WRITE_BYTES} 字节")
        path = self.resolve(relative)
        current = path.read_bytes() if path.is_file() else None
        current_rev = _sha256(current) if current is not None else None
        if expected_revision is not None and expected_revision != current_rev:
            raise WorkspaceRevisionConflict(
                f"revision 冲突: 期望 {expected_revision!r}，实际 {current_rev!r}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".cw-tmp-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return {
            "path": self.rel_of(path),
            "size": len(payload),
            "sha256": _sha256(payload),
            "revision": _sha256(payload),
        }

    def stat(self, relative: str) -> dict[str, Any]:
        path = self.resolve(relative)
        if not path.exists():
            return {"path": relative, "exists": False}
        st = path.stat()
        return {
            "path": self.rel_of(path),
            "exists": True,
            "type": "dir" if path.is_dir() else "file",
            "size": st.st_size,
            "mtime": st.st_mtime,
        }

    def hash(self, relative: str) -> dict[str, Any]:
        path = self.resolve(relative)
        if not path.is_file():
            return {"path": relative, "exists": False, "sha256": None}
        return {"path": self.rel_of(path), "exists": True, "sha256": _sha256(path.read_bytes())}

    def mkdir(self, relative: str) -> dict[str, Any]:
        path = self.resolve(relative)
        path.mkdir(parents=True, exist_ok=True)
        return {"path": self.rel_of(path), "created": True}

    def delete(self, relative: str) -> dict[str, Any]:
        path = self.resolve(relative)
        if not path.exists():
            return {"path": relative, "deleted": False}
        if path.is_dir():
            import shutil

            shutil.rmtree(path)
        else:
            path.unlink()
        return {"path": relative, "deleted": True}

    def rename(self, src: str, dst: str) -> dict[str, Any]:
        source = self.resolve(src)
        target = self.resolve(dst)
        if not source.exists():
            raise FileNotFoundError(f"源不存在: {src}")
        if target.exists():
            raise FileExistsError(f"目标已存在: {dst}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        return {"from": src, "to": dst}

    def _ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


class PluginArtifactRegistry:
    """通用 artifact 登记（M2）。

    元数据由宿主计算（size / sha256 / mime），插件不能自报；登记表放
    `<project-root>/.engine/artifacts/<plugin_id>.json`，不在插件可写命名空间内。
    """

    #: MVP MIME allowlist（与 PRD FR-9 一致）
    ALLOWED_MIME = {
        "video/mp4", "video/quicktime", "image/png", "image/jpeg",
        "image/webp", "text/plain", "application/json", "text/markdown",
        "application/zip",
    }

    def __init__(self, workspace: PluginWorkspace) -> None:
        self.workspace = workspace
        self._path = (
            workspace.project_root / ".engine" / "artifacts"
            / f"{workspace.scope.plugin_id}.json"
        )

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"artifacts": []}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("artifacts"), list):
                return data
        except Exception as e:  # noqa: BLE001
            logger.warning("artifact 登记表损坏 %s: %s", self._path, e)
        return {"artifacts": []}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def register(
        self,
        relative: str,
        *,
        role: str = "render",
        mime: str = "",
        job_id: str = "",
        retention: str = "task",
        status: str = "ready",
    ) -> dict[str, Any]:
        path = self.workspace.resolve(relative)
        if not path.is_file():
            raise FileNotFoundError(f"artifact 文件不存在: {relative}")
        size = path.stat().st_size
        digest = _sha256(path.read_bytes())
        guessed = mime or _guess_mime(path)
        if guessed not in self.ALLOWED_MIME:
            guessed = "application/octet-stream"
        entry = {
            "artifact_id": f"art_{digest[:16]}",
            "plugin_id": self.workspace.scope.plugin_id,
            "task_id": self.workspace.scope.task_id,
            "project_id": self.workspace.scope.project_id,
            "job_id": job_id,
            "path": self.workspace.rel_of(path),
            "mime": guessed,
            "size": size,
            "sha256": digest,
            "role": role,
            "status": status,
            "retention": retention,
            "created_at": time.time(),
        }
        data = self._load()
        data["artifacts"] = [a for a in data["artifacts"] if a.get("artifact_id") != entry["artifact_id"]]
        data["artifacts"].append(entry)
        self._save(data)
        return entry

    def list(self, *, status: str = "", role: str = "") -> list[dict[str, Any]]:
        items = self._load()["artifacts"]
        if status:
            items = [a for a in items if a.get("status") == status]
        if role:
            items = [a for a in items if a.get("role") == role]
        return sorted(items, key=lambda a: a.get("artifact_id", ""), reverse=True)

    def get(self, artifact_id: str) -> dict[str, Any]:
        for item in self._load()["artifacts"]:
            if item.get("artifact_id") == artifact_id:
                return item
        raise KeyError(f"artifact 不存在: {artifact_id}")

    def raw_path(self, artifact_id: str) -> Path:
        entry = self.get(artifact_id)
        path = self.workspace.resolve(str(entry.get("path") or ""))
        if not path.is_file():
            raise FileNotFoundError(f"artifact 文件已不存在: {artifact_id}")
        if _sha256(path.read_bytes()) != entry.get("sha256"):
            raise ValueError(f"artifact 内容与登记 hash 不一致: {artifact_id}")
        return path


def _guess_mime(path: Path) -> str:
    import mimetypes

    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"
