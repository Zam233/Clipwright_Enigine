"""B14：动画 MG runtime 收窄 —— 不补生成、视觉能力分流、无 handler 工具移除。"""

from __future__ import annotations

import pytest

from clipwright.animation.agentic.contracts import Observation
from clipwright.animation.agentic_loop import LoopResult
from clipwright.animation import agentic_runtime as rt_mod
from clipwright.config import settings


def _seeds(sha: str = ""):
    mg_def = {"elements": []}
    sha = sha or rt_mod.mg_fingerprint(mg_def)
    mg = {"mg_def": mg_def, "ok": True, "evidence_sha": sha}
    return [
        Observation("generate_mg", "ok", data=mg),
        Observation("preview_html", "ok",
                    data={"path": "renders/x/index.html", "evidence_sha": sha}),
        Observation("capture_preview_frames", "ok",
                    data={"frames": ["f1.png"], "evidence_sha": sha}),
    ]


@pytest.mark.asyncio
async def test_no_post_loop_autogeneration(monkeypatch):
    calls: list[str] = []

    async def fake_tool(name, **kwargs):
        calls.append(name)
        return {"ok": True}

    async def fake_loop(**kwargs):
        return LoopResult("ok", observations=[])

    monkeypatch.setattr(rt_mod, "call_animation_tool", fake_tool)
    monkeypatch.setattr(rt_mod, "run_agentic_loop", fake_loop)

    async def decide(_obs):
        return rt_mod.AgentAction("tool", "generate_mg", {})

    runtime = rt_mod.AnimationAgentRuntime(require_mg=True)
    result = await runtime.run(decide)
    assert result.status == "failed"
    assert result.error == "no_mg_def"
    assert calls == [], "循环结束后不得偷偷补生成"
    assert any(isinstance(o, dict) and o.get("kind") == "failure" for o in result.observations)


@pytest.mark.asyncio
async def test_vision_unavailable_allows_final_but_marks_degraded(monkeypatch):
    monkeypatch.setattr(settings, "vision_llm_model", "", raising=False)

    async def fake_tool(name, **kwargs):  # 不应被调用（证据已齐，只差 vision）
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(rt_mod, "call_animation_tool", fake_tool)

    async def decide(_obs):
        return rt_mod.AgentAction("final", "")

    runtime = rt_mod.AnimationAgentRuntime(require_mg=True)
    result = await runtime.run(decide, seed_observations=_seeds())
    assert result.status == "degraded"
    assert any(isinstance(o, dict) and o.get("kind") == "unverified_visual"
               for o in result.observations)


@pytest.mark.asyncio
async def test_vision_available_requires_inspect(monkeypatch):
    monkeypatch.setattr(settings, "vision_llm_model", "vision-model", raising=False)
    calls: list[str] = []

    async def fake_tool(name, **kwargs):
        calls.append(name)
        return {"findings": []}

    monkeypatch.setattr(rt_mod, "call_animation_tool", fake_tool)

    async def decide(_obs):
        return rt_mod.AgentAction("final", "")

    runtime = rt_mod.AnimationAgentRuntime(require_mg=True)
    result = await runtime.run(decide, seed_observations=_seeds())
    assert "inspect_frames" in calls
    assert result.status == "ok"


def test_ask_user_removed_from_tools():
    from clipwright.animation.agent_tools import list_animation_tools

    names = {t.name for t in list_animation_tools()}
    assert "ask_user" not in names, "无 handler 的工具不得出现在工具面"


@pytest.mark.asyncio
async def test_stale_visual_evidence_is_rejected(monkeypatch):
    """视觉证据必须钉在当前 mg_def 版本上：旧版本 inspect 不能放行 final。"""
    monkeypatch.setattr(settings, "vision_llm_model", "vision-model", raising=False)
    calls: list[str] = []

    async def fake_tool(name, **kwargs):
        calls.append(name)
        return {"findings": [], "frames": kwargs.get("frames") or ["f1.png"]}

    monkeypatch.setattr(rt_mod, "call_animation_tool", fake_tool)

    async def decide(_obs):
        return rt_mod.AgentAction("final", "")

    stale_sha = rt_mod.mg_fingerprint({"elements": ["old"]})
    seeds = _seeds(sha=stale_sha)
    seeds.append(Observation("inspect_frames", "ok",
                             data={"findings": [], "evidence_sha": stale_sha}))
    runtime = rt_mod.AnimationAgentRuntime(require_mg=True, max_rounds=6)
    result = await runtime.run(decide, seed_observations=seeds)
    # 旧证据不匹配当前版本 → 必须重新 preview/inspect，而不是直接 final
    assert "preview_html" in calls or "inspect_frames" in calls
    assert result.status in ("ok", "degraded")


def test_mg_fingerprint_is_stable_and_version_sensitive():
    a = {"elements": [{"type": "text", "text": "A"}]}
    b = {"elements": [{"type": "text", "text": "B"}]}
    assert rt_mod.mg_fingerprint(a) == rt_mod.mg_fingerprint(dict(a))
    assert rt_mod.mg_fingerprint(a) != rt_mod.mg_fingerprint(b)
