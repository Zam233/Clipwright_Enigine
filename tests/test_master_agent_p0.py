"""P0 验收（PRD docs/master-agent-prd-2026-10-26.md §6 / §7）。

覆盖：能力目录含编排能力（B6）、视觉 degraded 语义（B2）、
工作区事件改 JSON（B8）、下载默认落 materials（B5）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.services import workspace as ws_mod
from clipwright.services import workspace_index as wi
from clipwright.services.workspace import Workspace


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "workspaces")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "workspace_vision_enabled", True)
    monkeypatch.setattr(settings, "vision_provider", "llm")
    ws_mod.clear_events()


# ── B6：能力目录必须含编排能力 ──────────────────────────────


def test_capability_catalog_includes_meta_capabilities() -> None:
    from clipwright.services.agent_kernel import _capability_catalog

    catalog = _capability_catalog()
    assert "spawn_subagent" in catalog
    assert "get_subagent_status" in catalog
    assert "编排" in catalog
    # 工具面本身还得在（不能为了塞编排能力把工具丢了）
    assert "read_file" in catalog or "ffmpeg" in catalog or len(catalog.splitlines()) > 40


# ── B2：视觉 status 不许假阳性 ──────────────────────────────


def _patch_label_shot(monkeypatch, visual: dict) -> None:
    async def _fake(frames, **kwargs):
        return {"visual": visual, "evidence": {}, "confidence": {}}

    monkeypatch.setattr("clipwright.services.content_vision.label_shot", _fake)


def test_vision_status_degraded_when_filename_fallback(tmp_path, monkeypatch) -> None:
    ws = Workspace("proj_p0_a")
    ws.ensure(name="P0", auto_index=False, vision=True)
    img = ws.dirs().materials / "image" / "x.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"not-a-real-png")
    # 帧抽取失败时 _vision_for 会直接 skipped；这里让它"拿到帧"
    monkeypatch.setattr(wi, "_grab_frames", lambda *a, **k: [str(img)])
    _patch_label_shot(monkeypatch, {
        "tags": ["x", "横版"],
        "description": "文件: x.png (1x1)",
        "source": "filename",
    })

    out = wi._vision_for(ws, img, "image", enabled=True) if False else None
    import asyncio

    out = asyncio.run(wi._vision_for(ws, img, "image", enabled=True))
    assert out["status"] == "degraded"
    assert out["source"] == "filename"
    assert out["degraded_reason"] == "filename_fallback"


def test_vision_not_false_positive_when_model_fails(tmp_path, monkeypatch) -> None:
    """模型真失败时**不许**报 ok（审计 A11）。

    `VisionService.analyze_image` 在 LLM 抛错时内部回退成文件名兜底
    （source=filename / model=filename / description="文件: x.png (640x360)"），
    旧实现把它当 LLM 结果 → status=ok。这里钉住新的判据。
    """
    from clipwright.services import vision as vision_mod

    async def _fallback(self, path, provider=None):
        return {"tags": ["x", "横版"], "labels": ["x"], "model": "filename",
                "description": f"文件: {Path(path).name} (640x360)", "source": "filename"}

    monkeypatch.setattr(vision_mod.VisionService, "analyze_image", _fallback)
    monkeypatch.setattr(settings, "content_index_visual_llm", True)
    monkeypatch.setattr(wi, "_grab_frames", lambda *a, **k: ["fake.png"])

    ws = Workspace("proj_a11")
    ws.ensure(name="A11", auto_index=False, vision=True)
    img = ws.dirs().materials / "image" / "橙色方块.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"x")

    import asyncio

    out = asyncio.run(wi._vision_for(ws, img, "image", enabled=True))
    assert out["status"] == "degraded"
    assert out["source"] == "filename"
    # summary 里出现文件名/尺寸就是兜底，不是"看懂画面"
    assert "640x360" not in (out["summary"] or "") or out["status"] == "degraded"


def test_vision_status_ok_when_model_really_looked(tmp_path, monkeypatch) -> None:
    ws = Workspace("proj_p0_b")
    ws.ensure(name="P0", auto_index=False, vision=True)
    img = ws.dirs().materials / "image" / "y.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"not-a-real-png")
    monkeypatch.setattr(wi, "_grab_frames", lambda *a, **k: [str(img)])
    _patch_label_shot(monkeypatch, {
        "tags": ["橙色背景", "白色正方形"],
        "description": "橙色背景中央有一个白色正方形。",
        "source": "llm",
        "evidence": {"visual.description": "llm/deepseek-chat"},
    })

    import asyncio

    out = asyncio.run(wi._vision_for(ws, img, "image", enabled=True))
    assert out["status"] == "ok"
    assert out["source"] == "llm"
    assert "白色正方形" in out["summary"]
    assert out["degraded_reason"] == ""


# ── B8：事件接口改 JSON，SSE 单独一个端点 ─────────────────────


def _client() -> TestClient:
    from clipwright.api import workspace as workspace_api

    app = FastAPI()
    app.include_router(workspace_api.router)
    return TestClient(app)


def test_workspace_events_returns_json_not_stream() -> None:
    client = _client()
    created = client.post("/api/workspace", json={"project_id": "proj_events_1"})
    assert created.status_code == 200
    resp = client.get("/api/workspace/proj_events_1/events?since=0")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["project_id"] == "proj_events_1"
    assert isinstance(body["events"], list)
    assert "last_seq" in body


# ── B5：下载默认落工作区 materials/<kind> ───────────────────


def test_video_download_defaults_to_workspace_materials(tmp_path, monkeypatch) -> None:
    import asyncio

    from clipwright.tool import video as video_tool

    ws_root = tmp_path / "ws"
    (ws_root / "materials" / "video").mkdir(parents=True)

    def _fake_run(cmd, **kwargs):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-mp4")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(video_tool.subprocess, "run", _fake_run)
    result = asyncio.run(video_tool.VideoDownloadTool().execute(
        "https://example.com/a/clip.mp4", workspace_root=str(ws_root)))
    assert str(result.status).lower().endswith("success")
    saved = Path(result.output["local_path"])
    assert saved.parent == ws_root / "materials" / "video"
    assert saved.is_file()


def test_video_download_falls_back_to_tempdir_without_workspace(monkeypatch) -> None:
    import asyncio
    import tempfile

    from clipwright.tool import video as video_tool

    def _fake_run(cmd, **kwargs):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(video_tool.subprocess, "run", _fake_run)
    result = asyncio.run(video_tool.VideoDownloadTool().execute("https://e.com/b.mp4"))
    saved = Path(result.output["local_path"])
    assert tempfile.gettempdir().lower() in str(saved).lower()


def test_provider_is_live_not_hardcoded(tmp_path, monkeypatch) -> None:
    """B4：元数据里的 provider 必须反映实时配置，且 status() 报告实时值。"""
    monkeypatch.setattr(settings, "vision_provider", "transformers")
    ws = Workspace("proj_provider")
    ws.ensure(name="P", auto_index=False, vision=True)
    meta = json.loads((ws.dirs().root / ".workspace.json").read_text(encoding="utf-8"))
    assert meta["index"]["provider"] == "transformers"

    monkeypatch.setattr(settings, "vision_provider", "llm")
    st = Workspace("proj_provider").status()
    assert st["index"]["provider"] == "llm"
