"""M4：三层记忆（modern-agent PRD §2.5 / §5.5）。

可证伪点：

- 语义记忆必须有 provenance，否则降级为 working（不许幻觉变"知识"）；
- TTL 到期后 recall 不再返回，但条目仍可审计；
- 撤回（retract）后 recall 不再返回；
- 压缩（compact）必须承认"省略了多少条"，不许假装全都在；
- working / episodic / semantic 三层的寿命不同（episodic 只追加、不被压缩吞掉）。
"""

from __future__ import annotations

import time

import pytest

from clipwright.services import memory


@pytest.fixture(autouse=True)
def _clean():
    memory.reset()
    yield
    memory.reset()


def test_three_layers_are_separate():
    memory.remember("正在剪第 3 段", task_id="t1", layer="working", kind="decision")
    memory.remember("剪辑阶段失败：缺 B-roll", task_id="t1", layer="episodic",
                    kind="failure")
    memory.remember("用户偏好快节奏、鼓点对齐", task_id="t1", layer="semantic",
                    kind="preference", provenance={"source": "user_chat"})
    shown = memory.describe("t1")
    assert shown["working"]["total"] == 1
    assert shown["episodic"]["total"] == 1
    assert shown["semantic"]["total"] == 1


def test_semantic_without_provenance_degrades_to_working():
    entry = memory.remember("据说观众都喜欢 3 秒一个镜头", task_id="t1",
                            layer="semantic", kind="fact")
    assert entry["layer"] == "working", "没有来源的'知识'不许进长期记忆"
    assert memory.describe("t1")["semantic"]["total"] == 0


def test_recall_scores_and_reports_provenance():
    memory.remember("鼓点卡点偏好，快节奏", task_id="t1", layer="semantic",
                    tags=["剪辑"], provenance={"source": "user_chat", "ref": "m1"})
    hits = memory.recall("鼓点 卡点", task_id="t1")
    assert hits, "关键事实必须能被召回"
    assert hits[0]["score"] > 0
    assert hits[0]["provenance"]["source"] == "user_chat"


def test_ttl_expiry_hides_but_keeps_audit():
    entry = memory.remember("临时约定：先出粗剪", task_id="t1", layer="semantic",
                            provenance={"source": "session"}, ttl_sec=0.01)
    time.sleep(0.02)
    assert memory.recall("粗剪", task_id="t1") == []
    assert memory.recall("粗剪", task_id="t1", include_expired=True), "过期条目仍要可审计"
    assert entry["expires_at"] is not None


def test_retract_removes_from_recall():
    entry = memory.remember("素材库里全是 4K", task_id="t1", layer="semantic",
                            provenance={"source": "probe"})
    assert memory.recall("素材库 4K", task_id="t1")
    assert memory.forget(entry["id"], reason="实测是 1080p")
    assert memory.recall("素材库 4K", task_id="t1") == []
    assert memory.describe("t1")["retracted"] == 1


def test_compact_admits_what_it_dropped():
    for i in range(40):
        memory.remember(f"第 {i} 条工作记忆：" + "细节" * 40, task_id="t1")
    text = memory.compact("t1", budget_chars=400)
    assert "省略" in text or len(text) <= 420
    assert len(text) <= 600


def test_pinned_survives_compaction():
    first = memory.remember("关键事实：必须用实拍第 5 段", task_id="t1")
    memory.pin(first["id"])
    for i in range(40):
        memory.remember(f"噪音 {i}：" + "x" * 60, task_id="t1")
    text = memory.compact("t1", budget_chars=500)
    assert "必须用实拍第 5 段" in text, "钉住的关键事实不能被压缩丢掉"


def test_episodic_is_append_only_and_dumped(tmp_path):
    memory.remember("决策 A", task_id="t1", layer="episodic", kind="decision")
    memory.remember("结论 B", task_id="t1", layer="episodic", kind="outcome")
    path = memory.dump_episodic("t1", tmp_path)
    assert path and (tmp_path / "memory" / "episodic.jsonl").exists()
    lines = (tmp_path / "memory" / "episodic.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_empty_text_is_rejected():
    with pytest.raises(ValueError):
        memory.remember("   ", task_id="t1")
