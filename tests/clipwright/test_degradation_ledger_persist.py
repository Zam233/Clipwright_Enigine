"""D1′：降级台账任务级持久化（PRD §4.2，重启后 pending/receipts 一致）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipwright.services import degradation_ledger as L


@pytest.fixture(autouse=True)
def _clean():
    L.clear()
    yield
    L.clear()


def test_ledger_survives_process_restart(tmp_path, monkeypatch):
    """登记 + 接受收据之后重启（清内存但留盘），读回来必须一致。"""
    monkeypatch.setattr("clipwright.services.workspace.workspace_root",
                        lambda: tmp_path / "workspaces", raising=False)
    L.clear("task_persist")

    L.register("task_persist", [
        {"kind": "text_motion_weak", "scope": "animation"},
        {"kind": "material_placeholder", "scope": "material"},
    ])
    L.accept("task_persist", ["material_placeholder"], reason="图库无版权素材，用示意图替代")
    before_pending = [d["kind"] for d in L.outstanding("task_persist")]
    before_receipts = [r["mode"] for r in L.receipts("task_persist")]
    assert before_pending == ["text_motion_weak"]
    assert before_receipts == ["accepted"]

    # 模拟重启：清内存（但保住磁盘），再读
    L._PENDING.clear(); L._RECEIPTS.clear(); L._LOADED.clear()  # noqa: SLF001

    assert [d["kind"] for d in L.outstanding("task_persist")] == before_pending
    receipts = L.receipts("task_persist")
    assert [r["mode"] for r in receipts] == before_receipts
    assert receipts[0]["reason"].startswith("图库无版权素材")
    assert L.summary("task_persist")["pending"] == 1


def test_remediated_receipt_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.services.workspace.workspace_root",
                        lambda: tmp_path / "workspaces", raising=False)
    L.clear("task_p2")
    L.register("task_p2", [{"kind": "stage_tool_failures", "scope": "edit"}])
    assert L.mark_remediated("task_p2", "edit") == 1

    L._PENDING.clear(); L._RECEIPTS.clear(); L._LOADED.clear()  # noqa: SLF001

    assert L.outstanding("task_p2") == []
    assert [r["mode"] for r in L.receipts("task_p2")] == ["remediated"]


def test_broken_ledger_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.services.workspace.workspace_root",
                        lambda: tmp_path / "workspaces", raising=False)
    L.clear("task_bad")
    path = L._path("task_bad")  # noqa: SLF001
    assert path is not None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("{ this is not json", encoding="utf-8")

    L._PENDING.clear(); L._RECEIPTS.clear(); L._LOADED.clear()  # noqa: SLF001
    assert L.outstanding("task_bad") == [], "坏台账按空台账继续，不抛"
    # 仍能正常登记
    assert len(L.register("task_bad", [{"kind": "x", "scope": "edit"}])) == 1


def test_ledger_file_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.services.workspace.workspace_root",
                        lambda: tmp_path / "workspaces", raising=False)
    L.clear("task_shape")
    L.register("task_shape", [{"kind": "a", "scope": "edit"}])
    path = L._path("task_shape")  # noqa: SLF001
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["task_id"] == "task_shape"
    assert data["pending"][0]["kind"] == "a"
    assert "updated_at" in data


def test_clear_forgets_persisted_file(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.services.workspace.workspace_root",
                        lambda: tmp_path / "workspaces", raising=False)
    L.register("task_clr", [{"kind": "a", "scope": "edit"}])
    path = L._path("task_clr")  # noqa: SLF001
    assert Path(path).is_file()
    L.clear("task_clr")
    assert not Path(path).exists(), "clear 是「忘掉」：内存与磁盘都要清"
    assert L.outstanding("task_clr") == []


def test_exempt_kind_gains_ask_user_timeout():
    """ask_user 超时默认项属于已知取舍，不该拦收尾。"""
    assert not L.needs_receipt({"kind": "ask_user_timeout_default"})
    assert L.needs_receipt({"kind": "text_motion_weak"})
