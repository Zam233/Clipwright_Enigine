"""收据闸只拦"影响成片"的降级（E2E 2026-10-30 实测）。

现场：一条 60 秒片子的时间线已经出来了，但 `llm_json_invalid` / `mg_critique_skipped`
这类**信息型**降级没有收据 → 任务被判 `degradation_unresolved` failed。
信息型降级要照实上报（事件流里有），但不该拦任务。
"""

from __future__ import annotations

import pytest

from clipwright.services import degradation_ledger


@pytest.fixture(autouse=True)
def _clean():
    degradation_ledger.clear("task_x")
    yield
    degradation_ledger.clear("task_x")


def test_informational_kinds_do_not_block_finish():
    degradation_ledger.register("task_x", [
        {"kind": "llm_json_invalid", "scope": "mg"},
        {"kind": "mg_critique_skipped", "scope": "mg"},
        {"kind": "text_repair", "scope": "text"},
    ])
    assert degradation_ledger.outstanding("task_x") == []


def test_deliverable_affecting_kinds_still_require_receipt():
    degradation_ledger.register("task_x", [
        {"kind": "material_placeholder", "scope": "material"},
        {"kind": "text_motion_weak", "scope": "text"},
        {"kind": "stage_tool_failures", "scope": "edit"},
    ])
    kinds = sorted(d["kind"] for d in degradation_ledger.outstanding("task_x"))
    assert kinds == ["material_placeholder", "stage_tool_failures", "text_motion_weak"]


def test_accept_clears_only_required_kinds():
    degradation_ledger.register("task_x", [{"kind": "material_placeholder", "scope": "material"}])
    n = degradation_ledger.accept("task_x", ["material_placeholder"],
                                  reason="图库无版权素材，用示意图替代并标注来源")
    assert n == 1
    assert degradation_ledger.outstanding("task_x") == []
