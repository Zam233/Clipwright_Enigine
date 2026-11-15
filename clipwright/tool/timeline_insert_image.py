"""`timeline_insert_image` 工具 —— 把工作区图片插入任务时间线（第二期）。

走既有补丁引擎（`services.intent_apply`），不手搓时间线：
- `dry_run=true` 时只校验并把补丁报告返回给模型/用户（可拒绝）；
- 真正执行前要求当下时间线仍可被 `edit` 阶段写（动画/音频定稿后拒绝，避免覆盖下游产物）；
- 素材必须是工作区真实存在的图片（不存在就不新增，不编路径）。
"""

from __future__ import annotations

import json
from pathlib import Path

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.services import task_blackboard as bb
from clipwright.services.intent_apply import (
    PatchOp,
    PatchPlan,
    apply_patch,
    validate_patch,
)
from clipwright.tool.base import BaseTool, ToolExecResult, ToolStatus


def _resolve_root(project_id: str = "", workspace_root: str = "") -> Path | None:
    if workspace_root:
        p = Path(str(workspace_root)).expanduser()
        if p.is_dir():
            return p
    if project_id:
        try:
            from clipwright.services.workspace import Workspace

            ws = Workspace(str(project_id))
            if ws.exists:
                return ws.dirs().root
        except Exception as exc:  # noqa: BLE001
            logger.debug("工作区定位失败 %s: %s", project_id, exc)
    return None


def _load_timeline(task_id: str) -> tuple[Timeline, str]:
    """从黑板取时间线（timeline 优先，其次 final_timeline）。"""
    board = bb.get(task_id)
    for key in ("final_timeline", "timeline"):
        raw = board.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict) and str(raw.get("path") or ""):
            data = json.loads(Path(str(raw["path"])).read_text(encoding="utf-8"))
        elif isinstance(raw, dict):
            data = raw
        else:
            continue
        return Timeline(**data), key
    raise ValueError(f"任务 {task_id} 的黑板里没有 timeline（需先跑素材/剪辑阶段）")


def _resolve_asset(asset_ref: str, root: Path | None) -> Path:
    ref = str(asset_ref or "").strip()
    if not ref:
        raise ValueError("asset_ref 不能为空（工作区相对路径，如 materials/image/xxx.png）")
    p = Path(ref).expanduser()
    if p.is_absolute():
        target = p
    else:
        if root is None:
            raise ValueError("相对路径需要 project_id 或 workspace_root 才能解析")
        target = root / p
    if not target.is_file():
        raise ValueError(f"素材不存在: {target}")
    if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
        raise ValueError(f"不是图片文件: {target.suffix}")
    return target.resolve()


class TimelineInsertImageTool(BaseTool):
    """把一张工作区图片按位置插入时间线（可先 dry-run 预览）。"""

    name = "timeline_insert_image"
    description = (
        "把工作区里的图片插入当前任务时间线：position 支持 start / end / after:<clip_id>；"
        "返回会新增的片段与落点。dry_run=true 只校验不落盘。"
    )
    capability = "timeline_insert_image"
    capability_priority = 10
    permission_tier = "write_local"
    agent_callable = True
    dry_run_capable = True
    dependencies: list[str] = []
    parameters_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "当前任务 id（时间线在黑板里）"},
            "asset_ref": {"type": "string", "description": "工作区相对路径，如 materials/image/abc.png"},
            "position": {"type": "string", "default": "end",
                         "description": "start / end / after:<clip_id>"},
            "duration_sec": {"type": "number", "default": 4, "description": "片段时长（秒）"},
            "motion": {"type": "string", "default": "ken_burns",
                       "description": "ken_burns（缓慢推近）或 still（静止）"},
            "project_id": {"type": "string", "description": "项目 id（解析相对路径用）"},
            "workspace_root": {"type": "string", "description": "工作区根（解析相对路径用）"},
        },
        "required": ["task_id", "asset_ref"],
    }

    async def execute(
        self,
        *,
        task_id: str = "",
        asset_ref: str = "",
        position: str = "end",
        duration_sec: float = 4.0,
        motion: str = "ken_burns",
        project_id: str = "",
        workspace_root: str = "",
        dry_run: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        tid = str(task_id or "").strip()
        if not tid:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="需要 task_id（时间线在黑板上）")
        root = _resolve_root(project_id, workspace_root)

        try:
            timeline, key = _load_timeline(tid)
            asset = _resolve_asset(asset_ref, root)
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=str(exc))

        pos = str(position or "end").strip().lower()
        if not (pos in ("start", "end") or pos.startswith("after:") or pos.startswith("at:")):
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"position 不支持: {position!r}（可用 start / end / after:<clip_id> / at:<sec>）")

        operation = PatchOp(
            op="add",
            new_clip={
                "kind": "image",
                "asset_id": str(asset),
                "position": pos,
                "duration_sec": max(1.0, float(duration_sec or 4.0)),
                "metadata": {
                    "source": "generated",
                    "kenburns": str(motion or "ken_burns").lower() != "still",
                },
            },
            source_index=0,
            note=f"timeline_insert_image:{asset.name}",
        )
        plan = PatchPlan(ops=[operation])

        if dry_run:
            try:
                report = validate_patch(timeline, plan)
            except Exception as exc:  # noqa: BLE001
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"预检失败: {exc}")
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"dry_run": True, "source_key": key,
                        "report": report.model_dump(),
                        "problems": [p.model_dump() for p in report.problems]},
            )

        owner = bb.owner_of(tid, "timeline")
        if owner and owner != "edit":
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=(f"时间线当前由 {owner} 阶段写入（已定稿），不能再插图片；"
                       "请先让下游阶段重跑或在编辑阶段插入"),
            )

        new_timeline, report = apply_patch(timeline, plan)
        if report.has_errors():
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="补丁未执行（存在硬错误）",
                output={"problems": [p.model_dump() for p in report.problems]},
            )
        try:
            bb.put(tid, "timeline", new_timeline.model_dump(mode="json"), by="edit")
        except Exception as exc:  # noqa: BLE001 — 黑板拒绝时如实回报
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"写回黑板失败: {exc}",
            )

        clip_id = str(report.added_clip_ids[0]) if report.added_clip_ids else ""
        for track in new_timeline.tracks:
            for clip in track.clips:
                if str(clip.id) == clip_id:
                    pass
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={
                "clip_id": clip_id,
                "position": pos,
                "duration_sec": max(1.0, float(duration_sec or 4.0)),
                "motion": str(motion or "ken_burns"),
                "added_clip_ids": list(report.added_clip_ids),
                "clips_before": report.clips_before,
                "clips_after": report.clips_after,
                "warnings": [p.model_dump() for p in report.problems if p.level == "warning"],
            },
        )
