"""项目管理 — Pipeline 状态持久化（保存 / 加载 / 列表）。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger


class ProjectManager:
    """管理 Pipeline 执行状态的持久化。"""

    def __init__(self, projects_dir: str | Path = "projects") -> None:
        self._projects_dir = Path(projects_dir)
        self._projects_dir.mkdir(parents=True, exist_ok=True)

    # ── 保存 ──

    async def save(
        self,
        pipeline_id: str,
        state: dict[str, Any],
        name: str = "",
    ) -> str:
        """保存 Pipeline 状态到磁盘。"""
        project_id = pipeline_id or f"proj_{uuid.uuid4().hex[:12]}"
        project_dir = self._projects_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "project_id": project_id,
            "name": name or f"Project {project_id[:8]}",
            "pipeline_id": pipeline_id,
            "created_at": datetime.now().isoformat(),
            "state": state,
        }

        path = project_dir / "project.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("项目已保存: %s", project_id)
        return project_id

    # ── 加载 ──

    async def load(self, project_id: str) -> Optional[dict[str, Any]]:
        """加载 Pipeline 状态。"""
        path = self._projects_dir / project_id / "project.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("state")
        except Exception as e:
            logger.warning("项目加载失败 %s: %s", project_id, e)
            return None

    # ── 列表 ──

    async def list_projects(self) -> list[dict[str, Any]]:
        """列出所有已保存的项目。"""
        projects: list[dict[str, Any]] = []
        if not self._projects_dir.exists():
            return projects
        for d in sorted(self._projects_dir.iterdir()):
            if d.is_dir():
                path = d / "project.json"
                if path.exists():
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        projects.append({
                            "project_id": data.get("project_id", d.name),
                            "name": data.get("name", d.name),
                            "pipeline_id": data.get("pipeline_id", ""),
                            "created_at": data.get("created_at", ""),
                        })
                    except Exception:
                        pass
        return projects

    # ── 删除 ──

    async def delete(self, project_id: str) -> bool:
        """删除项目。"""
        path = self._projects_dir / project_id
        if not path.exists():
            return False
        import shutil
        shutil.rmtree(path, ignore_errors=True)
        logger.info("项目已删除: %s", project_id)
        return True
