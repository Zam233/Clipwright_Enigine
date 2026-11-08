"""工具轨迹无损（PRD modern-agent-review M1）。

现场：`last_tool_calls` 上限 200、payload 只采样 12 条 → edit 那种 100+ 次调用的阶段，
主 agent 看到的只是"最近一小段"。现在全量落 `reports/tool_calls/<stage>-<n>.json`，
payload 带路径，可回查完整 transcript。
"""

from __future__ import annotations

import json
from pathlib import Path

from clipwright.schema.run import AgentResult
from clipwright.services import stage_subagent


def test_dump_writes_full_trajectory(tmp_path: Path):
    calls = [{"tool": f"t{i}", "status": "success", "duration_ms": i} for i in range(150)]
    path = stage_subagent._dump_tool_calls(str(tmp_path), stage="edit", attempt=3, calls=calls)
    assert path.endswith("reports/tool_calls/edit-3.json") or path.endswith(
        "reports\\tool_calls\\edit-3.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["count"] == 150 and data["stage"] == "edit" and data["attempt"] == 3
    assert [c["tool"] for c in data["calls"][:3]] == ["t0", "t1", "t2"]


def test_dump_is_noop_without_root_or_calls(tmp_path: Path):
    assert stage_subagent._dump_tool_calls("", stage="edit", attempt=1, calls=[{"a": 1}]) == ""
    assert stage_subagent._dump_tool_calls(str(tmp_path), stage="edit", attempt=1, calls=[]) == ""


def test_payload_exposes_tool_calls_path_and_total():
    payload = AgentResult(
        run_id="r", kind="stage", status="ok", stage="edit",
        tool_calls=[{"tool": "video_trim", "status": "error"}],
        tool_calls_path="J:/ws/reports/tool_calls/edit-1.json",
    ).to_tool_payload()
    assert payload["tool_calls_path"].endswith("edit-1.json")
    assert payload["tool_calls_total"] == 1
    assert "完整轨迹见" in payload["tool_calls_hint"]
