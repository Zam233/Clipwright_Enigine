"""幂等键必须按参数区分（E2E 2026-10-30 实测的假回放）。

现场：旧键 `f"{task_id}:{name}:{len(calls)}"` 里 `calls` 是**本轮** loop 的调用列表，
续跑一轮从 0 重数 → "本轮第 3 次 delegate_agent"命中上一轮第 3 次的结果，
**参数完全不同也回放**（派 structure 拿回 edit 的历史结果）。
"""

from __future__ import annotations

from clipwright.tool.surface import idem_key_for


def test_same_args_same_key():
    a = idem_key_for("task_1", "delegate_agent", {"stage": "edit", "brief": "x"})
    b = idem_key_for("task_1", "delegate_agent", {"brief": "x", "stage": "edit"})
    assert a == b, "参数顺序不同不算不同调用"


def test_different_stage_different_key():
    a = idem_key_for("task_1", "delegate_agent", {"stage": "edit"})
    b = idem_key_for("task_1", "delegate_agent", {"stage": "structure"})
    assert a != b, "换阶段必须换幂等键，否则会拿回别的阶段的历史结果"


def test_different_task_different_key():
    a = idem_key_for("task_1", "delegate_agent", {"stage": "edit"})
    b = idem_key_for("task_2", "delegate_agent", {"stage": "edit"})
    assert a != b


def test_unserializable_args_do_not_crash():
    key = idem_key_for("task_1", "tool", {"obj": object()})
    assert key.startswith("task_1:tool:")
