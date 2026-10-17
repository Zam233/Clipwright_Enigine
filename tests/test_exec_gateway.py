"""B9：执行网关 —— 硬底线 / 棘轮 / 独立判定 / 分流 / 沙盒 / 审计。

对应 PRD F11 与验收 A15（分级与弹窗）、A16（审计）、A20（自动模式）、
A24（判定输入隔离）、A25（就高不就低）、A26（fail-safe）。
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.supervisor import gateway as gw
from clipwright.supervisor import review as rv
from clipwright.supervisor.shell_policy import hard_floor, max_level, ratchet_level


@pytest.fixture(autouse=True)
def _clean():
    gw.clear_gateway()
    yield
    gw.clear_gateway()


def _assess(monkeypatch, risk: str = "low", *, raise_exc: bool = False, capture: dict | None = None):
    async def fake(blob, timeout=None):  # noqa: ANN001
        if capture is not None:
            capture["blob"] = dict(blob)
        if raise_exc:
            raise RuntimeError("judge down")
        return {"risk": risk, "reasons": ["测试判定"], "source": "llm"}

    monkeypatch.setattr(rv, "assess", fake)
    return fake


def _client():
    from clipwright.api import supervisor as supervisor_api

    app = FastAPI()
    app.include_router(supervisor_api.router)
    return TestClient(app)


# ── 硬底线（不经 LLM、不可授权）────────────────────────────


@pytest.mark.parametrize("cmd", [
    "cat .env",
    "cat ~/.ssh/id_rsa",
    "rm -rf /",
    "shutdown -h now",
    "format C:",
])
def test_hard_floor_patterns(cmd):
    assert hard_floor(cmd), f"应命中硬底线: {cmd}"


def test_hard_floor_rejects_without_calling_llm(monkeypatch):
    called = {"n": 0}

    async def fake(blob, timeout=None):  # noqa: ANN001
        called["n"] += 1
        return {"risk": "low", "reasons": ["ok"]}

    monkeypatch.setattr(rv, "assess", fake)
    out = asyncio.run(gw.run_command("pl_x", "cat .env"))
    assert out["status"] == "rejected"
    assert called["n"] == 0
    rec = gw.records("pl_x")[-1]
    assert rec["rejected"]["reason"].startswith("硬底线")


# ── 棘轮（就高不就低）──────────────────────────────────────


def test_ratchet_marks_destructive_seed_as_med():
    assert ratchet_level("del output.mp4") == "med"
    assert ratchet_level("pip install requests") == "med"
    assert ratchet_level("dir") == "low"
    assert max_level("low", "med") == "med"
    assert max_level("high", "med") == "high"


def test_low_llm_judgement_is_raised_by_seed(monkeypatch):
    """LLM 判 low 但命中危险种子 → 强制 med 等授权（A25）。"""
    _assess(monkeypatch, risk="low")
    out = asyncio.run(gw.run_command("pl_x", "del output.mp4"))
    assert out["status"] == "pending_approval"
    assert out["approval"]["riskLevel"] == "med"
    assert any("危险种子" in r for r in out["approval"]["riskReasons"])


# ── 分流与自动模式 ─────────────────────────────────────────


def test_low_risk_executes(monkeypatch):
    _assess(monkeypatch, risk="low")
    out = asyncio.run(gw.run_command(
        "pl_x", 'python -c "print(42)"',
    ))
    assert out["status"] != "pending_approval"
    assert out["exitCode"] == 0
    assert "42" in out["outputTail"]
    assert gw.records("pl_x")[-1]["exitCode"] == 0


def test_med_waits_for_approval_then_runs(monkeypatch):
    _assess(monkeypatch, risk="med")
    out = asyncio.run(gw.run_command("pl_x", 'python -c "print(7)"'))
    assert out["status"] == "pending_approval"
    approval_id = out["approval"]["id"]
    assert gw.pending_approvals("pl_x")[0]["id"] == approval_id

    done = asyncio.run(gw.approve("pl_x", approval_id))
    assert done["exitCode"] == 0
    assert gw.pending_approvals("pl_x") == []


def test_reject_records_rejection(monkeypatch):
    _assess(monkeypatch, risk="high")
    out = asyncio.run(gw.run_command("pl_x", 'python -c "print(1)"'))
    rejected = gw.reject("pl_x", out["approval"]["id"])
    assert rejected["status"] == "rejected"
    assert gw.records("pl_x")[-1]["rejected"]["reason"] == "用户拒绝授权"


def test_auto_mode_only_waives_med(monkeypatch):
    _assess(monkeypatch, risk="med")
    gw.set_auto_mode("pl_x", True)
    med = asyncio.run(gw.run_command("pl_x", 'python -c "print(2)"'))
    assert med["exitCode"] == 0  # med 免弹窗

    _assess(monkeypatch, risk="high")
    high = asyncio.run(gw.run_command("pl_x", "del important.txt"))
    assert high["status"] == "pending_approval"  # high 仍要授权


def test_critical_requires_confirm_on_approve(monkeypatch):
    _assess(monkeypatch, risk="critical")
    out = asyncio.run(gw.run_command("pl_x", "pip install requests"))
    aid = out["approval"]["id"]
    with pytest.raises(ValueError, match="二次确认"):
        asyncio.run(gw.approve("pl_x", aid))
    ok = asyncio.run(gw.approve("pl_x", aid, confirm_high_risk=True))
    assert "exitCode" in ok


# ── fail-safe 与输入隔离 ───────────────────────────────────


def test_judge_failure_is_fail_safe_high(monkeypatch):
    """判定器整体失败 → review.assess 自身 fail-safe 到 high → 等授权（A26）。"""

    async def boom(self, **kwargs):  # noqa: ANN001
        raise RuntimeError("judge down")

    from clipwright.services import llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMService, "structured_output", boom)
    out = asyncio.run(gw.run_command("pl_x", 'python -c "print(3)"'))
    assert out["status"] == "pending_approval"
    assert out["approval"]["riskLevel"] in ("high", "critical")


def test_gateway_passes_only_command_fields_to_judge(monkeypatch):
    capture: dict = {}
    _assess(monkeypatch, risk="low", capture=capture)
    asyncio.run(gw.run_command("pl_x", 'python -c "print(4)"'))
    assert set(capture["blob"]) == {"command", "cwd", "workspace"}


# ── 沙盒：密钥剥离与审计 ───────────────────────────────────


def test_env_secrets_are_stripped(monkeypatch):
    _assess(monkeypatch, risk="low")
    os.environ["DEMO_API_KEY"] = "super-secret"
    try:
        out = asyncio.run(gw.run_command(
            "pl_x",
            'python -c "import os;print(os.environ.get(\'DEMO_API_KEY\',\'MISSING\'))"',
        ))
        assert "MISSING" in out["outputTail"]
        assert "super-secret" not in out["outputTail"]
    finally:
        os.environ.pop("DEMO_API_KEY", None)


def test_every_execution_has_audit_record(monkeypatch):
    _assess(monkeypatch, risk="low")
    asyncio.run(gw.run_command("pl_x", 'python -c "print(5)"'))
    rec = gw.records("pl_x")[-1]
    for key in ("id", "ts", "kind", "command", "cwd", "riskLevel", "riskReasons",
                "autoMode", "exitCode", "durationMs", "outputTail"):
        assert key in rec


# ── 端点 ────────────────────────────────────────────────────


def test_exec_endpoints(monkeypatch):
    _assess(monkeypatch, risk="high")
    client = _client()

    pending = client.get("/api/pipeline/pl_e/exec/pending-approvals").json()
    assert pending["count"] == 0

    # 直接授权触发一次执行
    out = asyncio.run(gw.run_command("pl_e", 'python -c "print(6)"'))
    aid = out["approval"]["id"]

    lst = client.get("/api/pipeline/pl_e/exec/pending-approvals").json()
    assert lst["count"] == 1

    ok = client.post(f"/api/pipeline/pl_e/exec/{aid}/approve", json={"confirm_high_risk": True})
    assert ok.status_code == 200

    log = client.get("/api/pipeline/pl_e/exec").json()
    assert log["count"] >= 1
    assert "riskLevel" in log["records"][0]


def test_auto_mode_endpoint_roundtrip():
    client = _client()
    assert client.get("/api/pipeline/pl_auto/exec/auto-mode").json()["enabled"] is False
    put = client.put("/api/pipeline/pl_auto/exec/auto-mode", json={"enabled": True})
    assert put.status_code == 200
    assert client.get("/api/pipeline/pl_auto/exec/auto-mode").json()["enabled"] is True


def test_reject_endpoint_404_for_unknown():
    client = _client()
    assert client.post("/api/pipeline/pl_x/exec/ap_missing/reject").status_code == 404


def test_post_exec_runs_low_risk_shell(monkeypatch):
    """主代理/用户的执行入口：低风险直接执行并返回结果。"""
    _assess(monkeypatch, risk="low")
    client = _client()
    out = client.post("/api/pipeline/pl_req/exec", json={
        "kind": "shell", "command": 'python -c "print(7)"',
    }).json()
    assert out["status"] in ("done", "failed")
    assert out["exitCode"] == 0
    assert "7" in out["outputTail"]
    assert out["pipeline_id"] == "pl_req"


def test_post_exec_python_kind_accepts_code(monkeypatch):
    _assess(monkeypatch, risk="low")
    client = _client()
    out = client.post("/api/pipeline/pl_req/exec", json={
        "kind": "python", "code": "print('api-py')",
    }).json()
    assert out["exitCode"] == 0
    assert "api-py" in out["outputTail"]
    assert out["kind"] == "python"


def test_post_exec_high_risk_returns_pending_approval(monkeypatch):
    _assess(monkeypatch, risk="high")
    client = _client()
    out = client.post("/api/pipeline/pl_req/exec", json={
        "kind": "shell", "command": 'python -c "print(8)"',
    }).json()
    assert out["status"] == "pending_approval"
    assert out["approval"]["riskLevel"] == "high"
    assert client.get("/api/pipeline/pl_req/exec/pending-approvals").json()["count"] == 1


def test_post_exec_rejects_unknown_kind(monkeypatch):
    _assess(monkeypatch, risk="low")
    client = _client()
    r = client.post("/api/pipeline/pl_req/exec", json={"kind": "ruby", "command": "puts 1"})
    assert r.status_code == 400


def test_post_exec_hard_floor_is_not_authorizable(monkeypatch):
    _assess(monkeypatch, risk="low")
    client = _client()
    out = client.post("/api/pipeline/pl_req/exec", json={
        "kind": "shell", "command": "cat .env",
    }).json()
    assert out["status"] == "rejected"


# ── run_python（E5）与禁网 ──────────────────────────────────


def test_run_python_executes_script(monkeypatch):
    _assess(monkeypatch, risk="low")
    out = asyncio.run(gw.run_python("pl_x", "print('py-ok')"))
    assert out["exitCode"] == 0
    assert "py-ok" in out["outputTail"]


def test_run_python_checks_code_for_hard_floor(monkeypatch):
    _assess(monkeypatch, risk="low")
    out = asyncio.run(gw.run_python("pl_x", "print(open('.env').read())"))
    assert out["status"] == "rejected"
    assert "硬底线" in out["record"]["rejected"]["reason"]


def test_run_python_rejects_empty():
    out = asyncio.run(gw.run_python("pl_x", "   "))
    assert out["status"] == "rejected"


def test_run_python_temp_script_lands_in_cwd_when_given(monkeypatch, tmp_path):
    """给了 cwd 就落该目录（脚本要能读相对路径素材）。"""
    _assess(monkeypatch, risk="low")
    out = asyncio.run(gw.run_python("pl_x", "print('in-cwd')", cwd=str(tmp_path)))
    assert out["exitCode"] == 0
    scripts = tmp_path / ".supervisor" / "scripts"
    assert scripts.is_dir() and list(scripts.glob("*.py"))


def test_run_python_temp_script_not_written_into_process_cwd(monkeypatch, tmp_path):
    """没给 cwd 时落系统临时目录——不得把脚本写进仓库/安装目录。"""
    import tempfile
    from pathlib import Path

    _assess(monkeypatch, risk="low")
    monkeypatch.chdir(tmp_path)
    out = asyncio.run(gw.run_python("pl_x", "print('no-cwd')"))
    assert out["exitCode"] == 0
    assert not (tmp_path / ".supervisor").exists()
    landed = Path(tempfile.gettempdir()) / "clipwright-supervisor" / ".supervisor" / "scripts"
    assert landed.is_dir() and list(landed.glob("*.py"))


def test_network_env_is_stripped_by_default(monkeypatch):
    _assess(monkeypatch, risk="low")
    os.environ["HTTP_PROXY"] = "http://proxy.local:8080"
    try:
        out = asyncio.run(gw.run_python(
            "pl_x", "import os;print(os.environ.get('HTTP_PROXY','MISSING'))",
        ))
        assert "MISSING" in out["outputTail"]
    finally:
        os.environ.pop("HTTP_PROXY", None)