"""暗区补齐（PRD modern-agent-review M2）：内部操作进统一轨迹。"""

from __future__ import annotations

import asyncio

from clipwright.services import agent_context as ac


def test_operation_records_success_with_duration():
    with ac.task_scope("t_ops", "animation"):
        with ac.operation("mg_generate:mg_data_bars", source="internal") as box:
            box["detail"] = "3 个元素"
        ops = ac.take_operations()
    assert len(ops) == 1
    entry = ops[0]
    assert entry["tool"] == "mg_generate:mg_data_bars"
    assert entry["status"] == "ok"
    assert entry["source"] == "internal"
    assert entry["output_brief"] == "3 个元素"
    assert entry["duration_ms"] >= 0


def test_operation_records_failure_and_reraises():
    with ac.task_scope("t_ops2", "material"):
        try:
            with ac.operation("material_search:pexels", source="internal"):
                raise RuntimeError("429 rate limited")
        except RuntimeError:
            pass
        ops = ac.take_operations()
    assert ops[0]["status"] == "error"
    assert "429" in ops[0]["error"]


def test_operations_are_scoped_per_task():
    with ac.task_scope("t_a", "edit"):
        ac.record_operation("skill:pv_review")
        assert len(ac.take_operations()) == 1
        assert ac.take_operations() == []          # 取走即清空
    assert ac.take_operations() == []              # 作用域外为空


def test_operations_do_not_leak_across_scopes():
    ac.record_operation("outside")
    with ac.task_scope("t_b", "audio"):
        ac.record_operation("inside")
        inner = ac.take_operations()
    assert [o["tool"] for o in inner] == ["inside"]
