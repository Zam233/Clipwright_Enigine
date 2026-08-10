"""项目管理 — Backend-managed project CRUD with id, folders, tags, thumbnails."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _safe_id(project_id: str) -> str:
    """Validate project id — rejects path traversal, injection, empty."""
    if not isinstance(project_id, str) or not _ID_RE.match(project_id):
        raise ValueError(f"invalid project id: {project_id!r}")
    return project_id


class ProjectManager:
    """Manages project JSON files on disk."""

    def __init__(self, projects_dir: str | Path | None = None) -> None:
        self._projects_dir = Path(projects_dir) if projects_dir else settings.project_dir
        self._projects_dir.mkdir(parents=True, exist_ok=True)

    # ── helpers ──

    def _project_path(self, project_id: str) -> Path:
        return self._projects_dir / project_id / "project.json"

    def _read_json(self, project_id: str) -> dict[str, Any] | None:
        path = self._project_path(project_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("项目加载失败 %s: %s", project_id, e)
            return None

    def _write_json(self, project_id: str, data: dict[str, Any]) -> None:
        path = self._project_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入：先写临时文件，再 rename，防止并发写入导致 JSON 损坏
        import tempfile, os
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(path))
        except BaseException:
            try: os.unlink(tmp)
            except OSError: pass
            raise

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _summary(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return a lightweight summary dict (no timeline) for list views."""
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "folder": data.get("folder", ""),
            "tags": data.get("tags", []),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "persona_id": data.get("persona_id"),
            "plugin_id": data.get("plugin_id"),
            "track_count": len((data.get("timeline") or {}).get("tracks", [])),
            "duration_sec": (data.get("timeline") or {}).get("duration_sec", 0),
            "has_thumbnail": bool(data.get("thumbnail") and Path(data["thumbnail"]).exists()),
        }

    # ── CRUD ──

    def create(
        self,
        name: str = "",
        timeline: Any = None,
        persona_id: str | None = None,
        plugin_id: str | None = None,
        folder: str = "",
        tags: list[str] | None = None,
        agent_state: Any = None,
    ) -> dict[str, Any]:
        """Create a new project with backend-assigned id."""
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        now = self._now()
        data = {
            "id": project_id,
            "name": name or f"Project {project_id[-8:]}",
            "timeline": timeline,
            "persona_id": persona_id,
            "plugin_id": plugin_id,
            "folder": folder,
            "tags": tags or [],
            "agent_state": agent_state,
            "created_at": now,
            "updated_at": now,
        }
        self._write_json(project_id, data)
        logger.info("项目已创建: %s", project_id)
        return data

    def save(self, project_id: str, project_data: dict[str, Any]) -> dict[str, Any]:
        """Update existing project (create NOT allowed here). Returns full dict."""
        _safe_id(project_id)
        existing = self._read_json(project_id)
        if existing is None:
            raise FileNotFoundError(f"Project {project_id} not found")
        # Merge: caller data overrides, but id is immutable
        existing.update(project_data)
        existing["id"] = project_id  # enforce immutable
        existing["updated_at"] = self._now()
        self._write_json(project_id, existing)
        logger.info("项目已保存: %s", project_id)
        return existing

    def load(self, project_id: str) -> dict[str, Any] | None:
        """Load full project dict, or None."""
        _safe_id(project_id)
        return self._read_json(project_id)

    def delete(self, project_id: str) -> bool:
        """Delete project directory. Returns True if existed."""
        _safe_id(project_id)
        project_dir = self._projects_dir / project_id
        if not project_dir.exists():
            return False
        shutil.rmtree(project_dir, ignore_errors=True)
        logger.info("项目已删除: %s", project_id)
        return True

    def list_projects(
        self, folder: str | None = None, tag: str | None = None
    ) -> list[dict[str, Any]]:
        """List all projects, optionally filtered by folder/tag.

        Returns lightweight summary dicts (no timeline) sorted by updated_at
        descending (newest first).
        """
        projects: list[dict[str, Any]] = []
        if not self._projects_dir.exists():
            return projects
        for d in self._projects_dir.iterdir():
            if d.is_dir():
                data = self._read_json(d.name)
                if data is None:
                    continue
                if folder and data.get("folder", "") != folder:
                    continue
                if tag and tag not in data.get("tags", []):
                    continue
                projects.append(data)
        projects.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
        return [self._summary(d) for d in projects]

    # ── Metadata operations ──

    def rename(self, project_id: str, new_name: str) -> dict[str, Any]:
        _safe_id(project_id)
        data = self._read_json(project_id)
        if data is None:
            raise FileNotFoundError(f"Project {project_id} not found")
        data["name"] = new_name
        data["updated_at"] = self._now()
        self._write_json(project_id, data)
        return data

    def rename_folder(self, old: str, new: str) -> int:
        """Rename a folder label across all projects. Returns count changed."""
        count = 0
        if not self._projects_dir.exists():
            return count
        for d in self._projects_dir.iterdir():
            if not d.is_dir():
                continue
            data = self._read_json(d.name)
            if data is None:
                continue
            if data.get("folder", "") == old:
                data["folder"] = new
                data["updated_at"] = self._now()
                self._write_json(d.name, data)
                count += 1
        return count

    def delete_folder(self, name: str) -> int:
        """Unfile all projects in a folder (clear folder field, never delete projects).

        Returns the number of projects affected.
        """
        count = 0
        if not self._projects_dir.exists():
            return count
        for d in self._projects_dir.iterdir():
            if not d.is_dir():
                continue
            data = self._read_json(d.name)
            if data is None:
                continue
            if data.get("folder", "") == name:
                data["folder"] = ""
                data["updated_at"] = self._now()
                self._write_json(d.name, data)
                count += 1
        return count

    def duplicate(self, project_id: str) -> dict[str, Any]:
        """Deep-copy a project with a new id. Does NOT mutate the source."""
        _safe_id(project_id)
        source = self._read_json(project_id)
        if source is None:
            raise FileNotFoundError(f"Project {project_id} not found")

        import copy
        new_id = f"proj_{uuid.uuid4().hex[:12]}"
        data = copy.deepcopy(source)
        data["id"] = new_id
        data["name"] = f"{source.get('name', 'Untitled')} 副本"
        now = self._now()
        data["created_at"] = now
        data["updated_at"] = now

        # Copy thumbnail file if it exists
        src_thumb = source.get("thumbnail")
        if src_thumb and Path(src_thumb).exists():
            new_thumb_dir = self._projects_dir / new_id
            new_thumb_dir.mkdir(parents=True, exist_ok=True)
            new_thumb = new_thumb_dir / "thumbnail.jpg"
            shutil.copy2(src_thumb, new_thumb)
            data["thumbnail"] = str(new_thumb)
            if source.get("thumbnail_asset"):
                data["thumbnail_asset"] = source["thumbnail_asset"]
        else:
            data.pop("thumbnail", None)
            data.pop("thumbnail_asset", None)

        self._write_json(new_id, data)
        logger.info("项目已复制: %s → %s", project_id, new_id)
        return data

    def set_folder(self, project_id: str, folder: str) -> dict[str, Any]:
        _safe_id(project_id)
        data = self._read_json(project_id)
        if data is None:
            raise FileNotFoundError(f"Project {project_id} not found")
        data["folder"] = folder
        data["updated_at"] = self._now()
        self._write_json(project_id, data)
        return data

    def add_tag(self, project_id: str, tag: str) -> dict[str, Any]:
        _safe_id(project_id)
        data = self._read_json(project_id)
        if data is None:
            raise FileNotFoundError(f"Project {project_id} not found")
        tags = data.get("tags", [])
        if tag not in tags:
            tags.append(tag)
            data["tags"] = tags
            data["updated_at"] = self._now()
            self._write_json(project_id, data)
        return data

    def remove_tag(self, project_id: str, tag: str) -> dict[str, Any]:
        _safe_id(project_id)
        data = self._read_json(project_id)
        if data is None:
            raise FileNotFoundError(f"Project {project_id} not found")
        tags = data.get("tags", [])
        if tag in tags:
            tags.remove(tag)
            data["tags"] = tags
            data["updated_at"] = self._now()
            self._write_json(project_id, data)
        return data

    def set_thumbnail(self, project_id: str, thumbnail_path: str) -> dict[str, Any]:
        _safe_id(project_id)
        data = self._read_json(project_id)
        if data is None:
            raise FileNotFoundError(f"Project {project_id} not found")
        data["thumbnail"] = thumbnail_path
        data["updated_at"] = self._now()
        self._write_json(project_id, data)
        return data

    def regenerate_thumbnail(self, project_id: str, force: bool = False) -> str | None:
        """Generate thumbnail from first video/image clip using ffmpeg.

        Stale-aware: stores the asset id used for generation as
        ``data["thumbnail_asset"]``.  When the current first resolvable asset
        matches the stored one *and* the file exists, the existing thumbnail is
        returned without re-running FFmpeg — unless *force* is ``True``.

        Returns the thumbnail path or ``None`` if no resolvable asset found.
        """
        _safe_id(project_id)
        data = self._read_json(project_id)
        if data is None:
            return None

        timeline = data.get("timeline")
        if not timeline:
            return None

        # Find first video/image clip with a resolvable asset
        from clipwright.services.asset_manager import AssetManager

        asset_mgr = AssetManager()
        thumbnail_path = str(self._projects_dir / project_id / "thumbnail.jpg")

        # Resolve the current first resolvable asset id for staleness check
        current_asset_id: str | None = None
        current_src_path: str | None = None
        for track in timeline.get("tracks", []):
            for clip in track.get("clips", []):
                aid = clip.get("asset_id")
                if not aid:
                    continue
                try:
                    asset_info = asset_mgr.get(aid)
                    src_path = asset_info.file_path
                except Exception:
                    continue
                if src_path and Path(src_path).exists():
                    current_asset_id = aid
                    current_src_path = src_path
                    break
            if current_asset_id:
                break

        if current_asset_id is None:
            return None

        # Stale check: skip regeneration if thumbnail is up-to-date
        if (
            not force
            and data.get("thumbnail_asset") == current_asset_id
            and data.get("thumbnail")
            and Path(data["thumbnail"]).exists()
        ):
            return data["thumbnail"]

        # Generate thumbnail with ffmpeg
        try:
            import subprocess

            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(current_src_path),
                    "-ss", "0.5", "-vframes", "1",
                    "-vf", "scale=320:-1",
                    thumbnail_path,
                ],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0 and Path(thumbnail_path).exists():
                data["thumbnail"] = thumbnail_path
                data["thumbnail_asset"] = current_asset_id
                data["updated_at"] = self._now()
                self._write_json(project_id, data)
                logger.info("缩略图已生成: %s", thumbnail_path)
                return thumbnail_path
        except Exception as e:
            logger.warning("缩略图生成失败: %s", e)

        return None
