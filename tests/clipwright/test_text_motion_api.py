"""MotionSpec API 契约测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from clipwright.api import text_animation as api

SPEC = {
    "schema": "motion_spec/1", "spec_id": "s1", "duration_sec": 2,
    "canvas": {"width": 640, "height": 360, "fps": 30}, "requires": ["text"],
    "layers": [{"id": "t", "type": "text", "content": "测试", "keyframes": [
        {"time": 0, "opacity": 0, "translate_y": 20}, {"time": 0.5, "opacity": 1, "translate_y": 0},
    ]}],
}


def test_validate_and_compile_do_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.config.settings.project_dir", tmp_path)
    ok = asyncio.run(api.validate_spec(api.SpecRequest(draft_spec=SPEC)))
    assert ok["ok"] is True
    assert ok["can_commit"] is True
    out = asyncio.run(api.compile_spec(api.SpecRequest(draft_spec=SPEC)))
    assert out["ok"] is True and out["spec_hash"]
    assert out["fidelity"] == "approx"


def test_invalid_spec_returns_errors():
    bad = dict(SPEC)
    bad["layers"] = [{"id": "t", "type": "text", "content": "x",
                      "keyframes": [{"time": 5, "opacity": 0}, {"time": 6, "opacity": 1}]}]
    out = asyncio.run(api.validate_spec(api.SpecRequest(draft_spec=bad)))
    assert out["ok"] is False
    assert out["errors"]


@pytest.mark.asyncio
async def test_author_task_fake_provider():
    from clipwright.animation.text_engine import TextMotionRegistry

    class P:
        id = "llm_motion"
        version = "test"
        async def author(self, req):
            from clipwright.animation.motion_spec import MotionSpec
            return MotionSpec.model_validate(SPEC)

    TextMotionRegistry.register(P(), plugin_id="test_api_author")
    start = await api.start_author(api.AuthorRequest(text="测试", duration_sec=2))
    await asyncio.sleep(0.05)
    st = await api.author_status(start["task_id"])
    assert st["status"] == "succeeded"
    assert st["spec_hash"]


def test_commit_and_conflict(tmp_path, monkeypatch):
    from clipwright.services.project_manager import ProjectManager
    monkeypatch.setattr("clipwright.config.settings.project_dir", tmp_path)
    m = ProjectManager()
    p = m.create(name="t")
    timeline = {"width": 640, "height": 360, "fps": 30, "duration_sec": 2, "tracks": [
        {"id": "tt", "kind": "text", "index": 0, "clips": [
            {"id": "tc", "kind": "text", "asset_id": "", "track_id": "tt",
             "start_sec": 0, "duration_sec": 2, "text": "测试", "metadata": {}},
        ]},
    ]}
    m.save(p["id"], {"timeline": timeline})
    r = asyncio.run(api.commit_spec(api.CommitRequest(
        project_id=p["id"], clip_id="tc", spec=SPEC)))
    assert r["ok"] is True and r["spec_hash"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.commit_spec(api.CommitRequest(
            project_id=p["id"], clip_id="tc", spec=SPEC, base_hash="wrong")))
    assert exc.value.status_code == 409


def test_author_tasks_persist_and_restart_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.config.settings.project_dir", tmp_path)
    api._AUTHOR_TASKS.clear()
    api._AUTHOR_TASKS["ma_test"] = {
        "task_id": "ma_test", "status": "authoring", "progress": 0,
        "created_at": 1.0, "cancelable": True,
    }
    api._persist_tasks()
    api._AUTHOR_TASKS.clear()
    api._load_tasks()
    assert api._AUTHOR_TASKS["ma_test"]["status"] == "failed"
    assert "重启" in api._AUTHOR_TASKS["ma_test"]["error"]


def test_author_tasks_with_request_requeue_on_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.config.settings.project_dir", tmp_path)
    api._AUTHOR_TASKS.clear()
    api._AUTHOR_TASKS["ma_resume"] = {
        "task_id": "ma_resume", "status": "authoring", "progress": 0,
        "created_at": 1.0, "cancelable": True,
        "request": {"text": "测试", "duration_sec": 2},
    }
    api._persist_tasks()
    api._AUTHOR_TASKS.clear()
    api._load_tasks()
    assert api._AUTHOR_TASKS["ma_resume"]["status"] == "queued"
    assert api._AUTHOR_TASKS["ma_resume"]["request"]["text"] == "测试"


def test_preview_frame_route(tmp_path, monkeypatch):
    from clipwright.services.project_manager import ProjectManager
    from clipwright.services import mg_frame
    monkeypatch.setattr("clipwright.config.settings.project_dir", tmp_path)
    m = ProjectManager(); p = m.create(name="t")
    timeline = {"width": 640, "height": 360, "fps": 30, "duration_sec": 2, "tracks": [
        {"id": "tt", "kind": "text", "index": 0, "clips": [
            {"id": "tc", "kind": "text", "start_sec": 0, "duration_sec": 2,
             "metadata": {"mg_def": {"elements": [{"type": "text", "content": "x"}]},
                          "motion_spec_hash": "h1"}},
        ]},
    ]}
    m.save(p["id"], {"timeline": timeline})
    async def fake_render(**kwargs):
        return b"\x89PNGfake", False
    monkeypatch.setattr(mg_frame.MgFrameRenderer, "render", fake_render)
    r = asyncio.run(api.preview_frame(api.PreviewFrameRequest(
        project_id=p["id"], clip_id="tc", time_sec=0.5, spec_hash="h1")))
    assert r.media_type == "image/png"
    assert r.body == b"\x89PNGfake"
    assert r.headers["x-mgframe-cache"] == "miss"
