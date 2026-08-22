"""MG 动画存储层 — 持久化生成结果和管理模板。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class MGStorage:
    """管理 LLM 生成的 MG 动画的持久化和模板化。"""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent
        self._base = base_dir
        self._generations_dir = base_dir / "generations"
        self._templates_dir = base_dir / "templates"
        self._generations_dir.mkdir(parents=True, exist_ok=True)
        self._templates_dir.mkdir(parents=True, exist_ok=True)

    def save_generation(self, mg_def: dict, generation_id: str = "") -> dict:
        """保存一次生成结果。返回 {generation_id, path}。"""
        if not generation_id:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            short_uid = uuid.uuid4().hex[:8]
            generation_id = f"gen_{ts}_{short_uid}"

        record = {
            "generation_id": generation_id,
            "mg_def": mg_def,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._generations_dir / f"{generation_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"generation_id": generation_id, "path": str(path)}

    def load_generation(self, generation_id: str) -> dict | None:
        """加载一次生成结果。"""
        path = self._generations_dir / f"{generation_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_as_template(self, generation_id: str, custom_name: str = "") -> str:
        """将生成结果保存为可复用模板。返回模板文件路径。"""
        record = self.load_generation(generation_id)
        if record is None:
            raise FileNotFoundError(f"Generation {generation_id} not found")

        mg_def = record["mg_def"]
        anim_id = mg_def.get("animation_id", "")

        existing = self.get_template_ids()
        if anim_id in existing or not anim_id:
            short_uid = uuid.uuid4().hex[:6]
            base_name = anim_id or "mg_custom"
            anim_id = f"{base_name}_{short_uid}"

        mg_def["animation_id"] = anim_id
        if custom_name:
            mg_def["name"] = custom_name

        path = self._templates_dir / f"{anim_id}.json"
        path.write_text(json.dumps(mg_def, ensure_ascii=False, indent=2), encoding="utf-8")

        gen_path = self._generations_dir / f"{generation_id}.json"
        if gen_path.exists():
            gen_path.unlink()

        return str(path)

    def get_template_ids(self) -> list[str]:
        """获取所有模板 ID。"""
        ids = []
        if self._templates_dir.exists():
            for f in self._templates_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    aid = data.get("animation_id", "")
                    if aid:
                        ids.append(aid)
                except Exception:
                    pass
        return ids

    def get_templates(self) -> list[dict]:
        """获取所有模板元信息。"""
        templates = []
        if self._templates_dir.exists():
            for f in sorted(self._templates_dir.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    templates.append({
                        "animation_id": data.get("animation_id", ""),
                        "name": data.get("name", ""),
                        "description": data.get("description", ""),
                        "duration_sec": data.get("duration_sec", 3.0),
                        "params": list(data.get("params", {}).keys()),
                    })
                except Exception:
                    pass
        return templates

    def load_template(self, anim_id: str) -> dict | None:
        """按 ID 加载模板。"""
        path = self._templates_dir / f"{anim_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_generations(self) -> list[dict]:
        """列出所有未保存的生成记录。"""
        gens = []
        if self._generations_dir.exists():
            for f in sorted(self._generations_dir.glob("*.json"), reverse=True):
                try:
                    record = json.loads(f.read_text(encoding="utf-8"))
                    mg_def = record.get("mg_def", {})
                    gens.append({
                        "generation_id": record.get("generation_id", ""),
                        "name": mg_def.get("name", ""),
                        "created_at": record.get("created_at", ""),
                    })
                except Exception:
                    pass
        return gens
