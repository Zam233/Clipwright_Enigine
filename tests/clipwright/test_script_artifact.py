"""B8（2026-10-28 PRD）：文案 artifact 化 + delegate `input_refs`。

要证伪的四件事：
1. proceed 侧把全文落 `docs/script.md`，并给出路径/字数/sha256；
2. `context_loader` 增加 `script` 段——只放前 N 字 + 全长 + hash，超长如实标 truncated，
   且被裁时 `trimmed` 里保留 `script` 键名；
3. `delegate_agent` 的 `input_refs` 校验存在性与工作区边界，非法就拒绝（不硬着头皮跑）；
4. 合法 refs → 阶段输入里的 `script_text` 与原文**逐字一致**，并带 sha256。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from clipwright.services import agent_run, context_loader
from clipwright.services.workspace import Workspace
from clipwright.tool import surface as sf


def _task(extra: dict[str, Any] | None = None, task_id: str = "task_b8") -> Any:
    return SimpleNamespace(
        id=task_id,
        request=SimpleNamespace(topic="做一期 3 分钟评测", extra=dict(extra or {})),
        plan=None,
    )


LONG_SCRIPT = "第一段旁白。" * 1400  # ≈ 8400 字，超过 context 预算


# ── ① 落盘 ────────────────────────────────────────────────


def test_write_script_creates_artifact_with_hash(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.config.settings.workspace_dir", tmp_path, raising=False)
    ws = Workspace("proj_b8")
    meta = ws.write_script(LONG_SCRIPT)

    path = Path(meta["script"])
    assert path.is_file()
    assert path.name == "script.md"
    assert path.read_text(encoding="utf-8") == LONG_SCRIPT
    assert int(meta["script_chars"]) == len(LONG_SCRIPT)
    assert meta["script_sha256"] == hashlib.sha256(LONG_SCRIPT.encode("utf-8")).hexdigest()


# ── ② 上下文装载 ──────────────────────────────────────────


def test_context_includes_script_segment(tmp_path):
    script = tmp_path / "script.md"
    script.write_text(LONG_SCRIPT, encoding="utf-8")
    ctx = context_loader.load(_task({"script_path": str(script)}))

    assert "## 文案（artifact）" in ctx["text"]
    assert "文件：" in ctx["text"] and "全文长度" in ctx["text"]
    src = next(s for s in ctx["sources"] if s["name"] == "script")
    assert src["chars"] == len(LONG_SCRIPT)
    assert src["truncated"] is True
    assert src["sha256"] == hashlib.sha256(LONG_SCRIPT.encode("utf-8")).hexdigest()
    # 上下文里只放前 LIMITS["script"] 字（不把 10k 字全塞进去）
    assert ctx["text"].count("第一段旁白") <= context_loader.LIMITS["script"] // 6 + 2


def test_context_marks_tiny_script_not_truncated(tmp_path):
    script = tmp_path / "script.md"
    script.write_text("短文案。", encoding="utf-8")
    ctx = context_loader.load(_task({"script_path": str(script)}))
    src = next(s for s in ctx["sources"] if s["name"] == "script")
    assert src["truncated"] is False and src["chars"] == 4


def test_context_falls_back_to_workspace_docs_script(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "script.md").write_text("工作区里的文案", encoding="utf-8")
    ctx = context_loader.load(_task({"workspace_root": str(tmp_path)}))
    assert any(s["name"] == "script" and s["chars"] == 7 for s in ctx["sources"])


def test_missing_script_path_is_reported_not_invented(tmp_path):
    ctx = context_loader.load(_task({"script_path": str(tmp_path / "nope.md")}))
    assert all(s["name"] != "script" for s in ctx["sources"])
    assert "## 文案（artifact）" not in ctx["text"]


def test_script_can_be_trimmed_under_pressure(tmp_path, monkeypatch):
    """预算极紧时 `script` 可能被裁——此时 `trimmed` 里必须留着它的键名。"""
    script = tmp_path / "script.md"
    script.write_text(LONG_SCRIPT, encoding="utf-8")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "brief.md").write_text("简报" * 300, encoding="utf-8")
    (tmp_path / "docs" / "plan.md").write_text("## scenes\n" + "场景行\n" * 200, encoding="utf-8")
    monkeypatch.setattr("clipwright.config.settings.context_budget_chars", 900, raising=False)

    ctx = context_loader.load(_task({"script_path": str(script),
                                     "workspace_root": str(tmp_path)}))
    assert "script" in ctx["trimmed"]
    assert isinstance(ctx["trimmed"], list)


# ── ③ input_refs 校验 ─────────────────────────────────────


def _ws_task(tmp_path: Path) -> Any:
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "script.md").write_text(LONG_SCRIPT, encoding="utf-8")
    return _task({"workspace_root": str(tmp_path)})


def test_input_refs_rejects_missing_file(tmp_path):
    out = asyncio.run(sf.execute(
        "delegate_agent",
        {"stage": "structure", "input_refs": ["docs/none.md"]},
        pipeline_id="task_b8", task=_ws_task(tmp_path),
    ))
    assert out["status"] == "refused"
    assert "不存在" in out["error"]
    assert out["failure"]["category"] == "needs_upstream"


def test_input_refs_rejects_outside_workspace(tmp_path):
    outside = Path(tmp_path).parent / "outside_secret.md"
    outside.write_text("secret", encoding="utf-8")
    out = asyncio.run(sf.execute(
        "delegate_agent",
        {"stage": "structure", "input_refs": [str(outside)]},
        pipeline_id="task_b8", task=_ws_task(tmp_path),
    ))
    assert out["status"] == "refused"
    assert "工作区内" in out["error"]


def test_input_refs_rejects_relative_without_workspace():
    out = asyncio.run(sf.execute(
        "delegate_agent",
        {"stage": "structure", "input_refs": ["docs/script.md"]},
        pipeline_id="task_b8", task=_task(),
    ))
    assert out["status"] == "refused"
    assert "workspace_root" in out["error"]


def test_input_refs_injects_full_script_text(tmp_path, monkeypatch):
    """验收核心：阶段拿到的 script_text 与原文逐字一致（不是截断摘要）。"""
    seen: list[Any] = []

    async def fake_run(spec, task):  # noqa: ANN001
        seen.append(spec)
        return agent_run.AgentResult(run_id="run_b8", kind=spec.kind, stage=spec.stage,
                                     status="ok", summary="已按文案拆场景")

    monkeypatch.setattr(agent_run, "run", fake_run)
    out = asyncio.run(sf.execute(
        "delegate_agent",
        {"stage": "structure", "brief": "按文案拆场景",
         "input_refs": ["docs/script.md"]},
        pipeline_id="task_b8", task=_ws_task(tmp_path),
    ))

    assert out["status"] == "ok"
    spec = seen[0]
    assert spec.inputs["script_text"] == LONG_SCRIPT
    assert spec.inputs["script_sha256"] == hashlib.sha256(LONG_SCRIPT.encode()).hexdigest()
    refs = spec.inputs["input_refs"]
    assert refs[0]["name"] == "script.md" and refs[0]["chars"] == len(LONG_SCRIPT)
    assert refs[0]["path"].endswith("script.md")


def test_input_refs_absent_keeps_old_behaviour(tmp_path, monkeypatch):
    seen: list[Any] = []

    async def fake_run(spec, task):  # noqa: ANN001
        seen.append(spec)
        return agent_run.AgentResult(run_id="r", kind=spec.kind, stage=spec.stage,
                                     status="ok", summary="ok")

    monkeypatch.setattr(agent_run, "run", fake_run)
    asyncio.run(sf.execute("delegate_agent", {"stage": "edit", "inputs": {"note": "x"}},
                           pipeline_id="task_b8", task=_ws_task(tmp_path)))
    assert seen[0].inputs == {"note": "x"}, "没给 input_refs 时不许篡改 inputs"


def test_delegate_schema_declares_input_refs():
    from clipwright.services.agent_loop import agent_tool_schemas

    schema = {s["function"]["name"]: s for s in agent_tool_schemas()}["delegate_agent"]
    props = schema["function"]["parameters"]["properties"]
    assert "input_refs" in props
    assert props["input_refs"]["type"] == "array"
