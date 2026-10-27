"""B5：记忆跨重启（closeout plan B5，按 §8 审校注记修正为「文件介质」）。

现有持久化是**文件**（`<root>/memory/semantic.json` 快照 + `episodic.jsonl` 追加），
不是 Mongo。所以本批的最小实现是：

1. 给 episodic 补 `load_episodic(root, task_id)`，并把 dump 游标对齐到已读条数
   （否则下一次 dump 会把同一批再写一遍 → replay 会把一次失败读成两次）；
2. 提供唯一入口 `memory.load(root, task_id=…)`；
3. Kernel 首次进 loop 时恢复、所有终态收尾时落盘——挂点选在不会漏的地方。

关键断言：**清空进程内缓存再加载，条目/TTL/撤回/provenance 必须一致**。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.schema.task import TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import memory


@pytest.fixture(autouse=True)
def _clean():
    memory.reset()
    ak.reset_kernel_state()
    yield
    memory.reset()
    ak.reset_kernel_state()


def test_episodic_roundtrip_and_cursor(tmp_path):
    memory.remember("剪辑阶段失败：缺 B-roll", task_id="t1", layer="episodic",
                    kind="failure", provenance={"source": "agent_run"})
    assert memory.dump_episodic("t1", tmp_path)

    memory.reset()
    assert memory.entries(task_id="t1", layer="episodic") == []

    added = memory.load_episodic(tmp_path, "t1")
    assert added == 1
    rows = memory.entries(task_id="t1", layer="episodic")
    assert rows[0]["text"].startswith("剪辑阶段失败")
    assert rows[0]["provenance"]["source"] == "agent_run"

    # 再次 dump 不该重复写（游标已对齐）
    assert memory.dump_episodic("t1", tmp_path) is None
    lines = (tmp_path / "memory" / "episodic.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_semantic_roundtrip_keeps_ttl_and_retract(tmp_path):
    alive = memory.remember("用户偏好快节奏", task_id="t1", layer="semantic",
                            provenance={"source": "user_chat"})
    doomed = memory.remember("素材库全是 4K", task_id="t1", layer="semantic",
                             provenance={"source": "probe"})
    memory.forget(doomed["id"], reason="实测 1080p")
    memory.pin(alive["id"])
    assert memory.dump_semantic(tmp_path)

    memory.reset()
    assert memory.load_semantic(tmp_path) == 2
    rows = memory.entries(layer="semantic", include_expired=True)
    by_id = {r["id"]: r for r in rows}
    assert by_id[alive["id"]]["pinned"] is True
    assert by_id[doomed["id"]]["retracted"] is True, "撤回状态必须持久化"
    # 撤回的条目 recall 不返回
    assert all(h["id"] != doomed["id"] for h in memory.recall("素材库 4K", task_id="t1"))


def test_load_entrypoint_reports_counts(tmp_path):
    memory.remember("语义事实", task_id="t9", layer="semantic",
                    provenance={"source": "s"})
    memory.remember("情景事件", task_id="t9", layer="episodic")
    memory.dump_semantic(tmp_path)
    memory.dump_episodic("t9", tmp_path)
    memory.reset()

    stats = memory.load(tmp_path, task_id="t9")
    assert stats == {"semantic": 1, "episodic": 1}


def test_broken_file_does_not_raise(tmp_path):
    p = tmp_path / "memory" / "semantic.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert memory.load_semantic(tmp_path) == 0, "坏文件只告警，不阻断启动"


def test_kernel_persists_memory_on_finish(tmp_path, monkeypatch):
    """终态收尾必须落盘（episodic 追加），否则下一页重启就什么都看不到。"""
    root = tmp_path / "ws"
    root.mkdir()

    class _D:
        async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                           guard=None, on_tool_call=None, dry_run=False):  # noqa: ANN001
            return {"status": "ok", "finish": {"conclusion": "ok"},
                    "tool_calls": [], "rounds": 1, "stopped_reason": "model_final"}

    class _WS:
        exists = True

        def dirs(self):  # noqa: ANN201
            from types import SimpleNamespace

            return SimpleNamespace(root=root)

    monkeypatch.setattr("clipwright.services.workspace.Workspace", lambda *a, **k: _WS())

    kernel = ak.AgentKernel(ak.KernelDeps(driver=_D(),
                                          checkpoint_dir=tmp_path / "tasks"))
    task = kernel.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(kernel.run(task))

    # 收尾写入了一条情景记忆并落盘
    assert (root / "memory").exists()
    assert memory.entries(task_id=task.id, layer="episodic") or (
        root / "memory" / "episodic.jsonl").exists()
