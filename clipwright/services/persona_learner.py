"""自适应 Persona 模型 — 从用户编辑行为中学习偏好，实时更新 Persona。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings

LEARNER_DATA_DIR = Path("persona_learning")
LEARNER_DATA_DIR.mkdir(parents=True, exist_ok=True)


class PersonaLearner:
    """Persona 学习器 — 记录用户编辑行为，更新 Persona 参数。"""

    def __init__(self, persona_id: str):
        self.persona_id = persona_id
        self._data_path = LEARNER_DATA_DIR / f"{persona_id}.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self._data_path.exists():
            try:
                return json.loads(self._data_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "persona_id": self.persona_id,
            "created_at": datetime.now().isoformat(),
            "edit_count": 0,
            "preferences": {
                "shot_duration_ms": 5000,
                "transition_weights": {"hard_cut": 0.6, "dissolve": 0.2, "fade": 0.1, "crossfade": 0.1},
                "animation_density": "medium",
                "cut_profile": "even_flow",
                "brightness_adjustments": [],
                "contrast_adjustments": [],
                "color_adjustments": [],
                "text_style_preferences": {},
            },
            "edit_history": [],
        }

    def save(self) -> None:
        self._data["updated_at"] = datetime.now().isoformat()
        self._data_path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_edit(self, action: str, params: dict[str, Any]) -> None:
        """记录用户的编辑操作，用于学习偏好。"""
        self._data["edit_count"] += 1
        entry = {
            "time": datetime.now().isoformat(),
            "action": action,
            "params": params,
        }
        self._data["edit_history"].append(entry)
        # 只保留最近 100 条
        if len(self._data["edit_history"]) > 100:
            self._data["edit_history"] = self._data["edit_history"][-100:]

        # 学习偏好
        self._learn(action, params)
        self.save()

    def _learn(self, action: str, params: dict) -> None:
        """从单次编辑中学习。"""
        prefs = self._data["preferences"]

        if action == "apply_video_filter":
            if "brightness" in params:
                prefs["brightness_adjustments"].append(params["brightness"])
            if "contrast" in params:
                prefs["contrast_adjustments"].append(params["contrast"])
            if "saturation" in params:
                prefs["color_adjustments"].append(params["saturation"])

        elif action == "change_text_style":
            style_desc = params.get("text_description", "")
            if "font_size" in params:
                prefs["text_style_preferences"]["font_size"] = params["font_size"]
            if "font_color" in params:
                prefs["text_style_preferences"]["font_color"] = params["font_color"]
            if "粗" in style_desc or "标题" in style_desc:
                prefs["text_style_preferences"]["weight"] = "bold"

        elif action == "apply_transition":
            trans_type = params.get("transition_type", "")
            if trans_type:
                tw = prefs["transition_weights"]
                tw[trans_type] = tw.get(trans_type, 0) + 0.05
                # 归一化
                total = sum(tw.values())
                if total > 0:
                    for k in tw:
                        tw[k] = round(tw[k] / total, 2)

        elif action == "change_video_speed":
            speed = params.get("speed", 1.0)
            if speed < 0.8:
                prefs["shot_duration_ms"] = int(prefs["shot_duration_ms"] * 0.9)
            elif speed > 1.5:
                prefs["shot_duration_ms"] = int(prefs["shot_duration_ms"] * 1.1)

    def get_persona_updates(self) -> dict[str, Any]:
        """根据学习到的偏好生成 Persona 更新建议。"""
        prefs = self._data["preferences"]
        updates = {}

        # 镜头时长
        updates["shot_duration_ms"] = prefs["shot_duration_ms"]

        # 转场权重
        updates["transition_weights"] = prefs["transition_weights"]

        # 动画密度
        updates["animation_density"] = prefs["animation_density"]

        # 亮度/对比度平均偏好
        b = prefs.get("brightness_adjustments", [])
        c = prefs.get("contrast_adjustments", [])
        if b:
            updates["avg_brightness"] = round(sum(b) / len(b), 2)
        if c:
            updates["avg_contrast"] = round(sum(c) / len(c), 2)

        # 字体偏好
        if prefs.get("text_style_preferences"):
            updates["text_style"] = prefs["text_style_preferences"]

        return updates

    def to_dict(self) -> dict:
        return self._data


# 全局学习器缓存
_learners: dict[str, PersonaLearner] = {}


def get_learner(persona_id: str) -> PersonaLearner:
    if persona_id not in _learners:
        _learners[persona_id] = PersonaLearner(persona_id)
    return _learners[persona_id]
