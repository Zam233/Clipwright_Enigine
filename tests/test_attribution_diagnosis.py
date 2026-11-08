"""B2 影子秘书测试：归因规则（R1–R4）、LLM 兜底、诊断书结构、端点。

对应 PRD F2（归因）与 F5（诊断书）、验收 A1/A2/A4 的可测部分。
"""

from __future__ import annotations

import pytest

from clipwright.schema.agent import Degradation, FailureReport
from clipwright.schema.supervisor import Attribution, LedgerEntry
from clipwright.services.attribution import (
    CATEGORY_TO_STAGE,
    attribute,
    attribute_with_llm,
)
from clipwright.services.diagnosis import build_diagnosis
from clipwright.services.episode_ledger import (
    EpisodeLedger,
    clear_ledgers,
    get_ledger,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_ledgers()
    yield
    clear_ledgers()


def _entry(stage: str = "material", attempt: int = 1, *, failure=None,
           degradations=None, model: str = "") -> LedgerEntry:
    return LedgerEntry(
        id=f"le_{stage}{attempt}",
        stage=stage,
        attempt=attempt,
        decision="fail" if failure else "pass",
        failure=failure,
        degradations=degradations or [],
        model=model,
    )


def _failure(category: str = "timeout", *, retryable: str = "retry_same",
             needs=None, blocking: str = "缺东西") -> FailureReport:
    return FailureReport(
        category=category, blocking_point=blocking,
        retryable=retryable, needs=needs or [],
    )


# ── R1 资源缺失 ─────────────────────────────────────────────


def test_r1_resource_missing_is_unrepairable():
    entries = [
        _entry("material", degradations=[
            Degradation(kind="material_placeholder", detail="素材源两次无结果",
                        scope="scene:3", impact="占位图"),
        ]),
    ]
    attr = attribute(entries)
    assert attr.primary_stage == "material"
    assert attr.unrepairable is True
    assert attr.confidence == "high"
    assert "scene:3" in attr.reason
    assert attr.evidence_refs == ["le_material1"]


# ── R3 上游申报 ─────────────────────────────────────────────


def test_r3_needs_upstream_points_to_declared_stage():
    entries = [
        _entry("material", failure=_failure(
            "input_insufficient", retryable="needs_upstream",
            needs=[{"from_stage": "structure", "field": "material_refs", "why": "缺镜头引用"}],
        )),
    ]
    attr = attribute(entries)
    assert attr.primary_stage == "structure"
    assert attr.confidence == "mid"
    assert attr.unrepairable is False
    assert "structure.material_refs" in attr.reason


# ── R2 上游失败未消化 ───────────────────────────────────────


def test_r2_upstream_failure_with_symptom_stage():
    entries = [
        _entry("material", failure=_failure("timeout", blocking="素材检索超时")),
        _entry("edit"),
    ]
    issues = [{"severity": "error", "category": "duration", "message": "成片时长不对"}]
    attr = attribute(entries, issues=issues)
    assert attr.primary_stage == "material"
    assert attr.confidence == "high"


def test_r2_needs_symptom_stage_otherwise_no_guess():
    """没有质检症状时不硬判上游——交给 R4 / LLM 兜底。"""
    entries = [_entry("material", failure=_failure("timeout"))]
    attr = attribute(entries)
    assert attr.primary_stage == ""


# ── R4 类别表兜底 ───────────────────────────────────────────


@pytest.mark.parametrize("category,expected", list(CATEGORY_TO_STAGE.items()))
def test_r4_category_table(category, expected):
    issues = [{"severity": "error", "category": category, "message": "x"}]
    attr = attribute([_entry("edit")], issues=issues)
    assert attr.primary_stage == expected
    assert attr.confidence == "low"


def test_empty_ledger_is_explicit():
    attr = attribute([])
    assert attr.primary_stage == ""
    assert attr.source == "fallback"
    assert "账本为空" in attr.reason


# ── LLM 兜底 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_fallback_used_only_when_rules_miss(monkeypatch):
    calls = {"n": 0}

    async def fake_structured(self, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        return {"primary_stage": "audio", "confidence": "mid", "reason": "音频轨静音"}

    from clipwright.services import llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMService, "structured_output", fake_structured)

    entries = [_entry("edit", failure=_failure("timeout"))]
    attr = await attribute_with_llm(entries)
    assert calls["n"] == 1
    assert attr.primary_stage == "audio"
    assert attr.source == "llm"


@pytest.mark.asyncio
async def test_llm_fallback_failure_returns_rule_result(monkeypatch):
    async def boom(self, **kwargs):  # noqa: ANN001
        raise RuntimeError("llm down")

    from clipwright.services import llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMService, "structured_output", boom)

    entries = [_entry("edit", failure=_failure("timeout"))]
    attr = await attribute_with_llm(entries)
    assert attr.primary_stage == ""
    assert attr.source == "fallback"


@pytest.mark.asyncio
async def test_llm_not_called_when_rule_hits(monkeypatch):
    async def boom(self, **kwargs):  # noqa: ANN001
        raise AssertionError("规则命中时不得调用 LLM")

    from clipwright.services import llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMService, "structured_output", boom)

    entries = [_entry("material", degradations=[Degradation(kind="resource_missing")])]
    attr = await attribute_with_llm(entries)
    assert attr.primary_stage == "material"


# ── 诊断书 ──────────────────────────────────────────────────


def test_diagnosis_structure_for_repairable_failure():
    entries = [
        _entry("edit", attempt=1, failure=_failure("timeout", blocking="剪辑超时")),
        _entry("edit", attempt=2, failure=_failure("timeout", blocking="剪辑又超时")),
    ]
    attr = Attribution(primary_stage="edit", confidence="high", reason="剪辑超时两次",
                       source="rule")
    report = build_diagnosis(entries, attribution=attr, issues=[
        {"severity": "error", "category": "duration", "message": "时长不对"},
    ], mode="shadow", pipeline_id="pl_1")

    assert "剪辑" in report.conclusion
    assert len(report.problems) == 2
    assert all(p["severity"] == "error" for p in report.problems)
    assert report.attempted and "第 2 次尝试" in report.attempted[0]["action"]
    actions = [s["action"] for s in report.suggestions]
    assert "redo_stage" in actions
    assert "retry_with_model" in actions
    assert report.mode == "shadow"
    assert report.pipeline_id == "pl_1"


def test_diagnosis_unrepairable_offers_accept():
    entries = [_entry("material", degradations=[
        Degradation(kind="material_placeholder", detail="无可用素材", scope="scene:1"),
    ])]
    attr = attribute(entries)
    report = build_diagnosis(entries, attribution=attr)
    actions = [s["action"] for s in report.suggestions]
    assert "accept_degraded" in actions
    assert "重试" in report.conclusion or "不会变好" in report.conclusion


def test_diagnosis_empty_ledger_is_quiet():
    report = build_diagnosis([])
    assert "没有记录到失败" in report.conclusion
    assert report.problems == []


# ── 端点 ────────────────────────────────────────────────────


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import supervisor as supervisor_api

    app = FastAPI()
    app.include_router(supervisor_api.router)
    return TestClient(app)


def test_attribution_endpoint_empty():
    body = _client().get("/api/pipeline/pl_none/attribution").json()
    assert body["found"] is False
    assert body["primary_stage"] == ""


def test_attribution_and_diagnosis_endpoints_with_ledger():
    ledger = get_ledger("pl_b2", persist=False)
    ledger.record(
        stage="material", decision="fail",
        failure=_failure("input_insufficient", retryable="needs_upstream",
                         needs=[{"from_stage": "structure", "field": "material_refs"}]),
        degradations=[Degradation(kind="material_placeholder", detail="占位", scope="scene:2")],
    )

    client = _client()
    attr = client.get("/api/pipeline/pl_b2/attribution").json()
    assert attr["found"] is True
    assert attr["primary_stage"] == "material"      # R1 优先于 R3
    assert attr["unrepairable"] is True

    diag = client.get("/api/pipeline/pl_b2/diagnosis").json()
    assert diag["found"] is True
    assert diag["conclusion"]
    assert diag["problems"]
    assert diag["suggestions"]


def test_supervisor_mode_defaults_off():
    from clipwright.config import settings

    assert getattr(settings, "supervisor_mode", "off") == "off"
