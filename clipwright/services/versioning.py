"""版本管理 — Timeline 版本追踪 + Undo/Redo。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger

VERSIONS_DIR = Path("versions")
VERSIONS_DIR.mkdir(parents=True, exist_ok=True)


class VersionManager:
    """时间线版本管理器 — 支持 Undo/Redo 和版本对比。

    每个编辑操作创建一个版本快照，支持：
    - undo: 回退到上一个版本
    - redo: 前进到下一个版本
    - diff: 对比两个版本的差异
    - list: 列出所有版本
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._dir = VERSIONS_DIR / session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index = self._load_index()
        self._position = len(self._index) - 1  # 当前所在版本

    def _load_index(self) -> list[dict]:
        path = self._dir / "index.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return []

    def _save_index(self) -> None:
        path = self._dir / "index.json"
        path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def can_undo(self) -> bool:
        return self._position > 0

    @property
    def can_redo(self) -> bool:
        return self._position < len(self._index) - 1

    @property
    def current_version(self) -> Optional[dict]:
        if 0 <= self._position < len(self._index):
            return self._index[self._position]
        return None

    def snapshot(self, data: Any, label: str = "") -> str:
        """创建版本快照。"""
        # 如果当前不在最新位置，丢弃当前位置之后的版本
        if self._position < len(self._index) - 1:
            self._index = self._index[:self._position + 1]

        version_id = f"v_{uuid.uuid4().hex[:8]}"
        entry = {
            "version_id": version_id,
            "time": datetime.now().isoformat(),
            "label": label,
            "position": len(self._index),
        }
        self._index.append(entry)
        self._position = len(self._index) - 1

        # 保存快照数据
        snapshot_path = self._dir / f"{version_id}.json"
        snapshot_path.write_text(
            json.dumps({"data": data, "meta": entry}, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        self._save_index()
        logger.info("版本已创建: %s (%s)", version_id, label)
        return version_id

    def undo(self) -> Optional[dict]:
        """回退到上一个版本。"""
        if not self.can_undo:
            logger.info("无法 undo：已是最早版本")
            return None
        self._position -= 1
        return self._load_version(self._position)

    def redo(self) -> Optional[dict]:
        """前进到下一个版本。"""
        if not self.can_redo:
            logger.info("无法 redo：已是最新版本")
            return None
        self._position += 1
        return self._load_version(self._position)

    def goto(self, position: int) -> Optional[dict]:
        """跳转到指定版本位置。"""
        if position < 0 or position >= len(self._index):
            return None
        self._position = position
        return self._load_version(position)

    def _load_version(self, position: int) -> Optional[dict]:
        """加载指定位置的版本数据。"""
        if position < 0 or position >= len(self._index):
            return None
        entry = self._index[position]
        snapshot_path = self._dir / f"{entry['version_id']}.json"
        if not snapshot_path.exists():
            return None
        try:
            return json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("加载版本失败 %s: %s", entry['version_id'], e)
            return None

    def get_version_list(self) -> list[dict]:
        """获取版本列表。"""
        return [
            {
                "version_id": e["version_id"],
                "time": e["time"],
                "label": e["label"],
                "position": e["position"],
                "is_current": i == self._position,
            }
            for i, e in enumerate(self._index)
        ]

    def diff(self, pos_a: int, pos_b: int) -> dict:
        """对比两个版本的差异（简单字段级对比）。"""
        data_a = self._load_version(pos_a)
        data_b = self._load_version(pos_b)
        if not data_a or not data_b:
            return {"error": "版本不存在"}

        a = data_a.get("data", {})
        b = data_b.get("data", {})

        changes = []
        for key in set(list(a.keys()) + list(b.keys())):
            if a.get(key) != b.get(key):
                changes.append({
                    "key": key,
                    "from": str(a.get(key, ""))[:100],
                    "to": str(b.get(key, ""))[:100],
                })
        return {
            "version_a": pos_a,
            "version_b": pos_b,
            "changes": changes,
            "change_count": len(changes),
        }

    def delete_all(self) -> None:
        """删除所有版本数据。"""
        import shutil
        if self._dir.exists():
            shutil.rmtree(self._dir)
        logger.info("版本数据已清除: %s", self.session_id)


class EditHistory:
    """轻量级编辑历史 — 管理 pipeline 级别的 undo/redo。"""

    def __init__(self, max_history: int = 50):
        self._history: list[dict] = []
        self._position = -1
        self._max = max_history

    def push(self, action: str, state: dict) -> int:
        """添加编辑历史记录。"""
        if self._position < len(self._history) - 1:
            self._history = self._history[:self._position + 1]
        self._history.append({"action": action, "state": state, "time": datetime.now().isoformat()})
        if len(self._history) > self._max:
            self._history = self._history[-self._max:]
        self._position = len(self._history) - 1
        return self._position

    def undo(self) -> Optional[dict]:
        if self._position <= 0:
            return None
        self._position -= 1
        return self._history[self._position]

    def redo(self) -> Optional[dict]:
        if self._position >= len(self._history) - 1:
            return None
        self._position += 1
        return self._history[self._position]

    @property
    def can_undo(self) -> bool:
        return self._position > 0

    @property
    def can_redo(self) -> bool:
        return self._position < len(self._history) - 1

    def list(self) -> list[dict]:
        return self._history
