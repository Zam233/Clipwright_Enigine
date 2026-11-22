"""附加：分镜预算/改稿差异（纯算法，无 LLM）。"""

from __future__ import annotations

from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class SceneBudgetCheckTool(BaseTool):
    name = "scene_budget_check"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "核对分镜总时长、场景数、平均镜头时长与目标时长，返回超/欠与密度提示。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "scenes": {"type": "array", "items": {"type": "object"}},
            "target_duration_sec": {"type": "number"},
            "max_scenes": {"type": "integer", "default": 60},
        },
        "required": ["scenes"],
    }

    async def execute(self, *, scenes: list[dict[str, Any]] | None = None,
                      target_duration_sec: float = 0.0,
                      max_scenes: int = 60, **kwargs: Any) -> ToolExecResult:
        rows = [s for s in (scenes or []) if isinstance(s, dict)]
        durations = [float(s.get("duration_sec") or 0) for s in rows]
        total = round(sum(durations), 3)
        target = float(target_duration_sec or 0)
        issues: list[str] = []
        if target > 0:
            delta = round(total - target, 3)
            if abs(delta) > max(2.0, target * 0.05):
                issues.append(f"总时长 {total:.1f}s 与目标 {target:.1f}s 偏差 {delta:+.1f}s")
        if len(rows) > max_scenes:
            issues.append(f"分镜数 {len(rows)} 超过上限 {max_scenes}")
        avg = round(total / len(rows), 2) if rows else 0.0
        if rows and avg < 2.0:
            issues.append(f"平均镜头 {avg:.1f}s 过短，节奏会碎")
        if rows and avg > 12.0:
            issues.append(f"平均镜头 {avg:.1f}s 过长，信息密度可能不足")
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
            "scene_count": len(rows), "total_duration_sec": total,
            "average_shot_sec": avg, "target_duration_sec": target,
            "issues": issues, "ok": not issues,
        })


class ScriptSceneDiffTool(BaseTool):
    name = "script_scene_diff"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "比较两版分镜：按 title+voiceover 指纹输出新增/删除/修改，便于改稿后确认。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "old_scenes": {"type": "array", "items": {"type": "object"}},
            "new_scenes": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["old_scenes", "new_scenes"],
    }

    @staticmethod
    def _key(s: dict[str, Any]) -> str:
        return "|".join(str(s.get(k) or "") for k in ("title", "voiceover_script", "description"))

    async def execute(self, *, old_scenes: list[dict[str, Any]] | None = None,
                      new_scenes: list[dict[str, Any]] | None = None,
                      **kwargs: Any) -> ToolExecResult:
        old = [s for s in (old_scenes or []) if isinstance(s, dict)]
        new = [s for s in (new_scenes or []) if isinstance(s, dict)]
        old_keys = {self._key(s): s for s in old}
        new_keys = {self._key(s): s for s in new}
        added = [new_keys[k] for k in new_keys.keys() - old_keys.keys()]
        removed = [old_keys[k] for k in old_keys.keys() - new_keys.keys()]
        changed = [{"old": old_keys[k], "new": new_keys[k]}
                   for k in old_keys.keys() & new_keys.keys()
                   if old_keys[k].get("duration_sec") != new_keys[k].get("duration_sec")]
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={
            "added": added, "removed": removed, "changed": changed,
            "old_count": len(old), "new_count": len(new),
        })
