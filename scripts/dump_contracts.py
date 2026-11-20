r"""生成 AgentResult v2 契约 fixture（X01a / X01b）。

fixture 必须**从 pydantic 模型生成**，不许手写 JSON 绕过校验：
- 后端消费：`tests/contracts/test_agent_result_v2.py::test_snapshot_roundtrip`
- 前端消费：`J:\Clipweight-Client\src\services\api\contracts\agent_result_v2\*.json`
两处必须**同字节**（X01b 用 SHA256 断言），因此统一由本脚本写出。

用法：
    python scripts/dump_contracts.py
    python scripts/dump_contracts.py --frontend-out J:\Clipweight-Client\src\services\api\contracts\agent_result_v2
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "contracts" / "agent_result_v2"


def _fixtures() -> dict[str, dict]:
    from clipwright.schema.agent import FailureReport, Need, RedoRequest
    from clipwright.schema.run import AgentResult

    common = {
        "run_id": "run_contract_ok",
        "kind": "stage",
        "task_id": "task_contract",
        "trace_id": "trace_contract",
        "attempt": 1,
        "stage": "structure",
        "role": "structure",
        "provider": "stub",
        "model": "stub-model",
        "duration_ms": 1234,
        "tokens": 900,
        "cost_usd": 0.0123,
        "cost_unknown": False,
        "tool_calls_total": 2,
        "trace_state": "complete",
        "tool_calls": [
            {"tool": "describe_llm_mg", "capability": "describe_mg", "kind": "tool",
             "status": "success", "duration_ms": 120, "seq_in_run": 1},
            {"tool": "media_probe", "capability": "media_probe", "kind": "tool",
             "status": "success", "duration_ms": 80, "seq_in_run": 2},
        ],
        "tool_calls_path": "reports/tool_calls/structure-1.json",
        "report_state": "written",
        "report_path": "reports/01-structure-1.md",
        "report_seq": 1,
        "report_sha256": "sha256:fixture-report",
        "handoff_summary": "3 个分镜已产出，素材引用可读，可直接进入素材阶段。",
        "brief": "为 ZAM 做一条 3 分钟数码测评，结尾给出购买建议。",
    }
    fixtures: dict[str, dict] = {}

    fixtures["ok"] = AgentResult(
        **common,
        status="ok",
        terminal_reason="completed",
        summary="分镜 3 个；工具 2 次",
        outputs={
            "script_skeleton": {"title": "测评", "scene_count": 3},
            "scenes": [{"id": "s1", "duration": 40.0}, {"id": "s2", "duration": 50.0},
                       {"id": "s3", "duration": 60.0}],
            # false / 0 / [] 必须原样存活
            "mg_dynamic": False,
            "warnings": 0,
            "dropped": [],
        },
        writes=["artifacts/art_structure.json"],
    ).model_dump(mode="json")

    fixtures["brief_empty"] = AgentResult(
        **{**common, "run_id": "run_contract_brief_empty", "brief": ""},
        status="ok",
        terminal_reason="completed",
        summary="无 brief 的兼容样例（键必须存在，值可为空串）",
        outputs={"script_skeleton": {}, "scenes": []},
    ).model_dump(mode="json")

    fixtures["degraded"] = AgentResult(
        **{**common, "run_id": "run_contract_degraded", "report_path": "",
           "report_state": "unavailable"},
        status="degraded",
        terminal_reason="scenes_fallback",
        summary="模板兜底产出 2 个分镜",
        outputs={"scenes": [{"id": "s1", "duration": 30.0}, {"id": "s2", "duration": 30.0}]},
        degradations=[{"kind": "scenes_fallback", "detail": "LLM 输出不可解析，用模板",
                       "scope": "scenes", "impact": "结构质量下降"}],
    ).model_dump(mode="json")

    fixtures["needs"] = AgentResult(
        **{**common, "run_id": "run_contract_needs", "report_path": "", "report_seq": 0,
           "report_state": "unavailable"},
        status="needs",
        terminal_reason="input_insufficient",
        summary="缺受众与目标时长，无法生成分镜",
        failure=FailureReport(
            category="input_insufficient",
            blocking_point="缺少 audience / target_duration_s",
            retryable="needs_upstream",
        ).model_dump(mode="json"),
        needs=[
            Need(field="audience", why="受众决定语气与信息密度", from_stage="requirements",
                 resolution="human").model_dump(mode="json"),
            Need(field="target_duration_s", why="时长决定场景数与节奏", from_stage="requirements",
                 resolution="human").model_dump(mode="json"),
        ],
    ).model_dump(mode="json")

    fixtures["failed"] = AgentResult(
        **{**common, "run_id": "run_contract_failed", "report_path": "",
           "report_state": "unavailable"},
        status="failed",
        terminal_reason="internal_defect",
        summary="解析器异常",
        failure=FailureReport(category="internal_defect", blocking_point="scene parser crash",
                              retryable="retry_same").model_dump(mode="json"),
    ).model_dump(mode="json")

    fixtures["refused"] = AgentResult(
        **{**common, "run_id": "run_contract_refused"},
        status="refused",
        terminal_reason="policy_refused",
        summary="深度 2 越权，拒绝派发",
        failure=FailureReport(category="policy_refused", blocking_point="depth>1",
                              retryable="permanent").model_dump(mode="json"),
    ).model_dump(mode="json")

    fixtures["cancelled"] = AgentResult(
        **{**common, "run_id": "run_contract_cancelled", "report_path": "",
           "report_state": "unavailable"},
        status="cancelled",
        terminal_reason="user_cancelled",
        summary="用户停止",
        failure=FailureReport(category="cancelled", blocking_point="用户主动停止",
                              retryable="permanent").model_dump(mode="json"),
    ).model_dump(mode="json")

    fixtures["dry_run"] = AgentResult(
        **{**common, "run_id": "run_contract_dry_run", "report_path": "", "report_seq": 0,
           "report_state": "not_applicable", "trace_state": "empty",
           "tool_calls": [], "tool_calls_total": 0},
        status="dry_run",
        terminal_reason="validated_only",
        summary="输入装配通过，未执行",
        outputs={"inputs": {"scenes": 3}},
    ).model_dump(mode="json")

    fixtures["redo_request"] = AgentResult(
        **{**common, "run_id": "run_contract_redo", "stage": "quality", "role": "quality"},
        status="failed",
        terminal_reason="quality_gate_failed",
        summary="字幕溢出 2 处，建议重做 edit",
        failure=FailureReport(category="quality_gate_failed",
                              blocking_point="subtitle overflow", retryable="retry_same").model_dump(mode="json"),
        issues=[{"issue_id": "i1", "severity": "error", "category": "subtitle",
                 "message": "底部字幕溢出", "clip_id": "c2"}],
        redo_request=RedoRequest(scope="clip:c2", target_stage="edit", issue_ids=["i1"],
                                 source_run_id="run_contract_quality_prev",
                                 base_artifact_sha="sha256:timeline").model_dump(mode="json"),
        redo_agent="edit",
        redo_hint="只重做 c2 的字幕位置，不要改时间线其余部分",
    ).model_dump(mode="json")

    return fixtures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--frontend-out", default="")
    args = parser.parse_args()

    fixtures = _fixtures()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in fixtures.items():
        path = out / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"wrote {len(fixtures)} fixtures -> {out}")

    if args.frontend_out:
        front = Path(args.frontend_out)
        front.mkdir(parents=True, exist_ok=True)
        for name, payload in fixtures.items():
            (front / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
        print(f"mirrored {len(fixtures)} fixtures -> {front}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
