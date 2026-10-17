"""B1 账本（EpisodeLedger）与账本端点测试。

覆盖 PRD F1 的可验证面：
- 每阶段尝试序号递增；
- 失败（F9 的 failure）与降级（B0 的 degradations）都进账本；
- 摘要含关键信息并可按 max_chars 截断；
- 注册表按 pipeline 隔离、可回收、有界；
- `GET /ledger` 在无账本时返回 found=false（不 404）。
"""

from __future__ import annotations

import pytest

from clipwright.schema.agent import Degradation, FailureReport
from clipwright.services.episode_ledger import (
    EpisodeLedger,
    clear_ledgers,
    drop_ledger,
    get_ledger,
    ledger_count,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_ledgers()
    yield
    clear_ledgers()


def _failure() -> FailureReport:
    return FailureReport(
        category="resource_missing",
        blocking_point="第 3 段无可用素材",
        retryable="needs_upstream",
        needs=[{"from_stage": "structure", "field": "material_refs", "why": "缺镜头引用"}],
    )


def test_attempts_increment_per_stage():
    ledger = EpisodeLedger("pl_1", persist=False)
    ledger.record(stage="structure", decision="pass")
    ledger.record(stage="structure", decision="fail", failure=_failure(), duration_ms=1200)
    ledger.record(stage="material", decision="pass")

    assert ledger.attempts("structure") == 2
    assert ledger.attempts("material") == 1
    assert ledger.attempts("edit") == 0
    assert [e.attempt for e in ledger.entries("structure")] == [1, 2]
    assert len(ledger.entries()) == 3


def test_entries_capture_failure_and_degradations():
    ledger = EpisodeLedger("pl_2", persist=False)
    entry = ledger.record(
        stage="material",
        decision="pass",
        degradations=[Degradation(kind="material_placeholder", detail="用了占位图",
                                  scope="scene:3", impact="该段为占位")],
        warnings=["素材源返回空"],
        model="anthropic/claude-sonnet",
        usage={"input_tokens": 10, "output_tokens": 5},
        duration_ms=800,
    )
    public = ledger.to_public()

    assert entry.degradations[0].kind == "material_placeholder"
    assert public["count"] == 1
    assert public["found"] is True
    assert public["attempts"] == {"material": 1}
    assert public["entries"][0]["model"] == "anthropic/claude-sonnet"
    assert public["entries"][0]["degradations"][0]["scope"] == "scene:3"


def test_entries_accept_raw_dicts():
    """账本入口要能吞 dict（step.result 里就是 dict），不能因脏数据崩。"""
    ledger = EpisodeLedger("pl_3", persist=False)
    ledger.record(stage="edit", decision="fail", failure={"category": "timeout"})
    ledger.record(stage="edit", decision="pass", degradations=[{"kind": "x"}])
    assert ledger.failures()[0].failure.category == "timeout"
    assert ledger.degradations()[0].kind == "x"


def test_summary_contains_actionable_fields_and_truncates():
    ledger = EpisodeLedger("pl_4", persist=False)
    ledger.record(stage="structure", decision="pass")
    ledger.record(
        stage="material", decision="fail", failure=_failure(),
        degradations=[Degradation(kind="material_placeholder", detail="占位", scope="scene:3")],
        warnings=["空结果"], duration_ms=900,
    )
    text = ledger.summary()

    assert "structure 第1次" in text
    assert "category=resource_missing" in text
    assert "第 3 段无可用素材" in text
    assert "需要上游=structure/material_refs" in text
    assert "降级 material_placeholder" in text

    short = ledger.summary(max_chars=80)
    assert len(short) <= 120
    assert "已截断" in short


def test_empty_summary_is_explicit():
    assert "账本为空" in EpisodeLedger("pl_5", persist=False).summary()


def test_registry_is_per_pipeline_and_bounded():
    a = get_ledger("pl_a", persist=False)
    b = get_ledger("pl_b", persist=False)
    assert a is not b
    assert get_ledger("pl_a", persist=False) is a
    assert ledger_count() == 2

    drop_ledger("pl_a")
    assert ledger_count() == 1


def test_record_does_not_touch_mongo_when_disconnected():
    """未连库时只留内存副本，绝不抛错。"""
    ledger = EpisodeLedger("pl_6", persist=True)
    entry = ledger.record(stage="audio", decision="pass")
    assert entry.stage == "audio"
    assert ledger.entries("audio")[0].id.startswith("le_")


def test_ledger_endpoint_returns_empty_shape_when_unknown():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import supervisor as supervisor_api

    app = FastAPI()
    app.include_router(supervisor_api.router)
    client = TestClient(app)

    resp = client.get("/api/pipeline/pl_unknown/ledger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["count"] == 0
    assert body["entries"] == []


def test_ledger_endpoint_serves_recorded_entries():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import supervisor as supervisor_api

    ledger = get_ledger("pl_x", persist=False)
    ledger.record(stage="structure", decision="pass")
    ledger.record(stage="edit", decision="fail", failure=_failure())

    app = FastAPI()
    app.include_router(supervisor_api.router)
    client = TestClient(app)

    body = client.get("/api/pipeline/pl_x/ledger").json()
    assert body["found"] is True
    assert body["count"] == 2
    assert body["attempts"] == {"structure": 1, "edit": 1}

    only_edit = client.get("/api/pipeline/pl_x/ledger", params={"stage": "edit"}).json()
    assert only_edit["count"] == 1
    assert only_edit["entries"][0]["stage"] == "edit"
