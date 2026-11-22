"""stage_inputs.preflight：缺上游不拦截，字段错误在派发前拦截。"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.services import stage_inputs


def _task():
    return SimpleNamespace(
        id="task_preflight",
        request=SimpleNamespace(
            persona_id="", category_plugin_id="", topic="t",
            duration_sec=60, extra={},
        ),
    )


def test_missing_keys_are_soft() -> None:
    errors = stage_inputs.preflight(
        "edit", task=_task(), board={}, inputs={},
    )
    assert errors
    assert {e["category"] for e in errors} == {"input_insufficient"}


def test_invalid_timeline_is_hard() -> None:
    errors = stage_inputs.preflight(
        "animation", task=_task(), board={"timeline": {"tracks": "bad"}}, inputs={},
    )
    assert errors
    assert errors[0]["category"] == "input_invalid"


def test_valid_edit_inputs_pass() -> None:
    errors = stage_inputs.preflight(
        "edit", task=_task(),
        board={"script_skeleton": {"scenes": []}, "candidate_clips": []},
        inputs={},
    )
    assert errors == []
