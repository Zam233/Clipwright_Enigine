"""R5：candidate_clips 的通用查询/粗排/去重（不绑定素材源）。"""

from __future__ import annotations

from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _board_clips(task_id: str) -> list[dict[str, Any]]:
    if not task_id:
        return []
    from clipwright.services import task_blackboard

    board = task_blackboard.get(str(task_id)) or {}
    rows: list[dict[str, Any]] = []
    for c in board.get("candidate_clips") or []:
        if not isinstance(c, dict):
            continue
        for a in c.get("suggested_assets") or []:
            if isinstance(a, dict):
                rows.append({**a, "scene_index": c.get("scene_index")})
    return rows


class CandidateQueryTool(BaseTool):
    name = "candidate_query"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "查询候选素材：按场景/类型/关键词过滤，返回有界候选列表；可读黑板候选。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "candidates": {"type": "array", "items": {"type": "object"}},
            "task_id": {"type": "string"},
            "scene_index": {"type": "integer"},
            "kind": {"type": "string"},
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
    }

    async def execute(self, *, candidates: list[dict[str, Any]] | None = None,
                      task_id: str = "", scene_index: int = -1,
                      kind: str = "", query: str = "", limit: int = 50,
                      **kwargs: Any) -> ToolExecResult:
        rows = [c for c in (candidates if candidates is not None else _board_clips(task_id))
                if isinstance(c, dict)]
        if scene_index >= 0:
            rows = [c for c in rows if c.get("scene_index") == scene_index]
        if kind:
            rows = [c for c in rows if str(c.get("type") or c.get("kind") or "") == kind]
        q = str(query or "").strip().lower()
        if q:
            rows = [c for c in rows
                    if q in str(c.get("title") or "").lower()
                    or q in str(c.get("description") or "").lower()
                    or q in str(c.get("url") or "").lower()]
        cap = max(1, min(200, int(limit or 50)))
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"items": rows[:cap], "total": len(rows),
                                      "truncated": len(rows) > cap})


class CandidateRerankTool(BaseTool):
    name = "candidate_rerank"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "按场景语义与候选评分粗排 top-K（确定性，不调用 LLM）。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "candidates": {"type": "array", "items": {"type": "object"}},
            "scene_context": {"type": "object"},
            "top_k": {"type": "integer", "default": 8},
        },
        "required": ["candidates"],
    }

    async def execute(self, *, candidates: list[dict[str, Any]] | None = None,
                      scene_context: dict[str, Any] | None = None,
                      top_k: int = 8, **kwargs: Any) -> ToolExecResult:
        ctx = scene_context or {}
        words = {str(w).lower() for w in (ctx.get("keywords") or []) if str(w).strip()}
        title = str(ctx.get("title") or "").lower()

        def _score(c: dict[str, Any]) -> float:
            score = float(c.get("score") or 0.0)
            text = " ".join(str(c.get(k) or "") for k in ("title", "description", "url", "path")).lower()
            if words:
                score += sum(1 for w in words if w in text) * 0.2
            if title and title in text:
                score += 0.3
            return score

        rows = sorted((c for c in (candidates or []) if isinstance(c, dict)),
                      key=_score, reverse=True)
        cap = max(1, min(50, int(top_k or 8)))
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"items": rows[:cap], "total": len(rows)})


class MaterialDedupeTool(BaseTool):
    name = "material_dedupe"
    permission_tier = "read"
    agent_callable = True
    dependencies: list[str] = []
    description = "按 asset_id/url/local_path 去重候选素材，返回唯一项与被移除项。"
    parameters_schema = {
        "type": "object",
        "properties": {"candidates": {"type": "array", "items": {"type": "object"}}},
        "required": ["candidates"],
    }

    async def execute(self, *, candidates: list[dict[str, Any]] | None = None,
                      **kwargs: Any) -> ToolExecResult:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for c in candidates or []:
            if not isinstance(c, dict):
                continue
            key = str(c.get("asset_id") or c.get("url") or c.get("local_path")
                      or c.get("path") or "")
            if not key:
                continue
            if key in seen:
                removed.append(c)
                continue
            seen.add(key)
            unique.append(c)
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"items": unique, "removed": removed,
                                      "total": len(unique)})
