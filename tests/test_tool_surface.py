"""C1：Kernel 工具面（覆盖率 / 档位门禁 / 幂等 / 审计 / 预检）。

对应 PRD A4（权限档生效）、A7（能力覆盖）、A3（每次调用都有审计）。
这层的作用是让 C0 量出的「LLM 可见 21%」变成「可安全直调 100%」——**且不翻
`agent_callable`**（那会把 45 个能力顺手塞进管线阶段 Agent）。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.tool import surface as sf
from clipwright.tool.registry import ToolRegistry, ToolStatus


@pytest.fixture(autouse=True)
def _clean():
    sf.clear_records()
    sf.clear_cache()
    yield
    sf.clear_records()
    sf.clear_cache()


def _client() -> TestClient:
    from clipwright.api import tool_surface as api

    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


# ── 档位门禁 ─────────────────────────────────────────────


def test_dangerous_tools_never_enter_surface():
    names = {e.name for e in sf.build_surface()}
    assert "video_download" not in names
    assert "run_shell" not in names and "run_python" not in names


def test_cannot_build_surface_with_dangerous_tier():
    with pytest.raises(sf.SurfaceError, match="dangerous"):
        sf.build_surface(tiers=("read", "dangerous"))


def test_execute_refuses_dangerous_with_actionable_hint():
    out = asyncio.run(sf.execute("video_download", {"url": "https://example.com/a.mp4"}))
    assert out["status"] == "refused"
    assert "执行网关" in out["error"], "拒绝理由必须给出可执行的下一步"


def test_execute_refuses_unknown_tool():
    out = asyncio.run(sf.execute("__nope__", {}))
    assert out["status"] == "refused"
    assert "不在 Kernel 工具面" in out["error"]


# ── 覆盖面（A7 的可测部分）──────────────────────────────


def test_surface_covers_all_safe_available_tools():
    from clipwright.services.capability_matrix import build_matrix

    safe = [
        e.name for e in build_matrix()
        if e.kind == "tool" and e.tier in sf.SURFACE_TIERS and e.available
    ]
    surfaced = {e.name for e in sf.build_surface() if e.available}
    assert set(safe) <= surfaced, f"漏掉的可用工具: {set(safe) - surfaced}"
    stat = sf.surface_stats()
    # A7 门槛：可直调档位的能力必须全覆盖（PRD 要求 ≥90%）
    assert stat["coverage_ratio"] >= 0.90, stat
    assert stat["dangerous_excluded"], "dangerous 名单不能为空"


def test_schemas_are_llm_ready():
    items = sf.schemas()
    assert len(items) >= 40
    for item in items:
        assert item["type"] == "function"
        fn = item["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        # 档位提示必须进描述，模型才知道这步会不会花钱
        assert "［" in fn["description"]


def test_surface_stats_shape():
    stat = sf.surface_stats()
    for key in ("surface_count", "coverage_ratio", "by_tier", "dangerous_excluded",
                "dry_run_capable", "preflight_capable"):
        assert key in stat
    assert stat["by_tier"]["read"] > 0
    assert stat["by_tier"]["write_local"] > 0


# ── 预检（dry-run 的诚实版本）────────────────────────────


def test_dry_run_does_not_execute(monkeypatch):
    called = {"n": 0}

    async def boom(cls, name, **kwargs):  # noqa: ANN001, ARG001
        called["n"] += 1
        raise AssertionError("dry-run 不允许真跑")

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(boom))
    out = asyncio.run(sf.execute("media_probe", {"input_path": "x.mp4"}, dry_run=True))
    assert out["status"] == "dry_run"
    assert called["n"] == 0


def test_dry_run_reports_missing_required_args():
    out = asyncio.run(sf.execute("media_probe", {}, dry_run=True))
    assert out["status"] == "dry_run"
    assert out["ok"] is False
    assert any(c["check"] == "required_args" and not c["ok"] for c in out["checks"])


def test_dry_run_reports_missing_input_file():
    out = asyncio.run(sf.execute("media_probe", {"input_path": "definitely_missing.mp4"},
                                 dry_run=True))
    assert out["ok"] is False
    assert any(c["check"] == "input_exists" and not c["ok"] for c in out["checks"])


def test_dry_run_is_labeled_structural_not_semantic():
    out = asyncio.run(sf.execute("media_probe", {"input_path": "x.mp4"}, dry_run=True))
    assert out["fidelity"] == "structural"
    assert "未" in out["note"], "必须说清没验证什么"


# ── 幂等与审计 ───────────────────────────────────────────


def _fake_result(name: str) -> object:
    from clipwright.schema.tool import ToolExecResult

    return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=name, output={"ok": True})


def test_idempotency_key_prevents_double_execution(monkeypatch):
    calls: list[str] = []

    async def fake_execute(cls, name, **kwargs):  # noqa: ANN001, ARG001
        calls.append(name)
        return _fake_result(name)

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(fake_execute))
    first = asyncio.run(sf.execute("media_probe", {"input_path": "a.mp4"}, idem_key="k1"))
    second = asyncio.run(sf.execute("media_probe", {"input_path": "a.mp4"}, idem_key="k1"))
    assert calls == ["media_probe"], "同一 idem_key 只能真跑一次"
    assert first["replayed"] is False
    assert second["replayed"] is True


def test_audit_record_has_required_fields(monkeypatch):
    async def fake_execute(cls, name, **kwargs):  # noqa: ANN001, ARG001
        return _fake_result(name)

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(fake_execute))
    out = asyncio.run(sf.execute("media_probe", {"input_path": "a.mp4"},
                                 pipeline_id="pl_c1", idem_key="k2"))
    rec = out["record"]
    for key in ("name", "tier", "pipeline_id", "idem_key", "status", "duration_ms", "ts"):
        assert key in rec
    assert rec["pipeline_id"] == "pl_c1"
    assert sf.records("pl_c1")[0]["name"] == "media_probe"


def test_tool_exception_is_contained(monkeypatch):
    async def boom(cls, name, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("ffmpeg 炸了")

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(boom))
    out = asyncio.run(sf.execute("media_probe", {"input_path": "a.mp4"}))
    assert out["status"] == "error"
    assert "ffmpeg 炸了" in out["error"]
    assert out["record"]["status"] == "error"


# ── 目录端点 ─────────────────────────────────────────────


def test_tools_endpoint_lists_surface():
    body = _client().get("/api/tools").json()
    assert body["count"] >= 40
    assert body["stats"]["coverage_ratio"] >= 0.9
    assert any(i["name"] == "media_probe" for i in body["items"])
    # 目录不返回整份 schema（避免响应过大）
    assert "schema" not in body["items"][0]


def test_tools_endpoint_rejects_unknown_tier():
    resp = _client().get("/api/tools", params={"tier": "dangerous"})
    assert resp.status_code == 400
    assert "执行网关" in resp.json()["detail"]


def test_tools_schema_endpoint_matches_surface():
    body = _client().get("/api/tools/schema").json()
    assert body["count"] == len(sf.schemas())
    assert body["tools"][0]["type"] == "function"


def test_tools_records_endpoint_filters_by_pipeline():
    sf._RECORDS.append(  # noqa: SLF001 — 直接注入一条审计，避免依赖工具真跑
        sf.ToolCallRecord(name="media_probe", tier="read", pipeline_id="pl_x",
                          idem_key="", args_brief="{}", status="ok", duration_ms=1.0)
    )
    body = _client().get("/api/tools/records", params={"pipeline_id": "pl_x"}).json()
    assert body["count"] == 1
    assert body["items"][0]["name"] == "media_probe"
