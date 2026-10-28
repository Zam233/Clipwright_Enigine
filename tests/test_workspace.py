"""工作区（PRD docs/workspace-prd-2026-10-24.md）服务层与 API 测试。

策略：不碰真实 ffmpeg/视觉模型——索引流水线的三个外部依赖
（``MediaIndexer`` / ``AssetManager`` / ``content_vision``）全部用 monkeypatch 替身；
工作区根目录用 tmp_path，绝不写仓库里的 ``workspaces/``。
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
def _isolated_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "workspaces")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "workspace_vision_enabled", True)
    monkeypatch.setattr(settings, "vision_provider", "none")
    ws_mod.clear_events()
    yield
    ws_mod.clear_events()


def _fake_indexers(monkeypatch, *, asset_ok: bool = True):
    from clipwright.services.asset_manager import AssetManager
    from clipwright.services.indexer import MediaIndexer

    async def _meta(path: str):
        return {"duration_sec": 1.5, "width": 640, "height": 360}

    async def _classify(path: str, metadata: dict):
        return ["测试标签"]

    async def _import(self, path):  # noqa: ANN001
        if not asset_ok:
            return SimpleNamespace(asset_id="", error="登记失败")
        return SimpleNamespace(asset_id="a_1", file_path=str(path), thumbnail_path="")

    monkeypatch.setattr(MediaIndexer, "extract_metadata", _meta)
    monkeypatch.setattr(MediaIndexer, "classify", _classify)
    monkeypatch.setattr(AssetManager, "import_file", _import)


# ────────────────────────────── 服务层 ──────────────────────────────


def test_create_status_and_dirs(tmp_path) -> None:
    ws = Workspace("proj_abc123")
    meta = ws.ensure(name="演示项目")
    root = Path(meta["root"])
    assert root.is_dir()
    for sub in ("materials/video", "materials/image", "materials/audio",
                "assets/files", "renders", "cache", "logs"):
        assert (root / sub).is_dir(), sub
    assert (root / ".workspace.json").is_file()

    (root / "materials" / "video" / "a.mp4").write_bytes(b"x" * 32)
    (root / "materials" / "image" / "b.jpg").write_bytes(b"y" * 16)
    status = ws.status()
    assert status["exists"] is True
    assert status["counts"]["total"] == 2
    assert status["counts"]["video"] == 1 and status["counts"]["image"] == 1
    assert status["counts"]["pending"] == 2


@pytest.mark.asyncio
async def test_scan_indexes_new_then_dedupes_rename(tmp_path, monkeypatch) -> None:
    _fake_indexers(monkeypatch)
    ws = Workspace("proj_abc123")
    meta = ws.ensure(vision=False)
    root = Path(meta["root"])
    video = root / "materials" / "video" / "a.mp4"
    video.write_bytes(b"AAAA" * 64)

    first = await wi.scan_and_index(ws)
    assert first["new"] == 1 and first["indexed"] == 1 and first["failed"] == 0
    reg = ws.load_registry()
    sha = next(iter(reg["materials"]))
    entry = reg["materials"][sha]
    assert entry["status"] == "indexed"
    assert entry["tags"] == ["测试标签"]
    assert entry["asset_id"] == "a_1"

    # 改名（同内容）→ 不产生新记录，只更新路径映射
    video.rename(root / "materials" / "video" / "b.mp4")
    second = await wi.scan_and_index(ws)
    assert second["new"] == 0 and second["deduped"] == 1
    reg2 = ws.load_registry()
    assert len(reg2["materials"]) == 1
    assert "b.mp4" in json.dumps(reg2["paths"])


@pytest.mark.asyncio
async def test_scan_dry_run_does_not_write(tmp_path, monkeypatch) -> None:
    _fake_indexers(monkeypatch)
    ws = Workspace("proj_abc123")
    meta = ws.ensure(vision=False)
    (Path(meta["root"]) / "materials" / "video" / "a.mp4").write_bytes(b"AAAA")
    report = await wi.scan_and_index(ws, dry_run=True)
    assert report["dry_run"] is True and report["new"] == 1
    assert report["items"][0]["status"] == "would_index"
    assert ws.load_registry()["materials"] == {}


@pytest.mark.asyncio
async def test_index_failure_is_recorded_not_raised(tmp_path, monkeypatch) -> None:
    from clipwright.services.indexer import MediaIndexer

    async def _boom(path: str):
        raise RuntimeError("ffprobe 崩了")

    async def _classify(path: str, metadata: dict):
        return []

    monkeypatch.setattr(MediaIndexer, "extract_metadata", _boom)
    monkeypatch.setattr(MediaIndexer, "classify", _classify)
    ws = Workspace("proj_abc123")
    meta = ws.ensure(vision=False)
    (Path(meta["root"]) / "materials" / "video" / "bad.mp4").write_bytes(b"ZZZZ")
    report = await wi.scan_and_index(ws)
    assert report["failed"] == 1 and report["indexed"] == 0
    entry = next(iter(ws.load_registry()["materials"].values()))
    assert entry["status"] == "failed" and "metadata" in entry["error"]


def test_migrate_legacy_copies_and_leaves_pointer(tmp_path) -> None:
    legacy = Path(settings.project_dir) / "proj_abc123" / "assets"
    (legacy / "files").mkdir(parents=True)
    (legacy / "files" / "old.mp4").write_bytes(b"old")
    (legacy / "index.json").write_text("{}", encoding="utf-8")

    ws = Workspace("proj_abc123")
    result = ws.migrate_legacy()
    assert result and result["ok"] is True
    root = ws.dirs().root
    assert (root / "assets" / "files" / "old.mp4").is_file()
    assert (Path(settings.project_dir) / "proj_abc123" / "MIGRATED_TO").is_file()
    # 源文件仍在（先复制后切换）
    assert (legacy / "files" / "old.mp4").is_file()


def test_cleanup_cache_respects_limit(tmp_path) -> None:
    ws = Workspace("proj_abc123")
    meta = ws.ensure()
    root = Path(meta["root"])
    cache = root / "cache"
    for i in range(3):
        p = cache / f"f{i}.bin"
        p.write_bytes(b"x" * (512 * 1024))
    meta["limits"]["cache_max_gb"] = 0.001  # ≈1MB
    ws._write_meta(meta)
    freed = ws.cleanup_cache()
    assert freed > 0
    remaining = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
    assert remaining <= 1.05 * (1 << 20)


def test_project_files_written(tmp_path) -> None:
    ws = Workspace("proj_abc123")
    ws.ensure()
    out = ws.write_project_files({"meta": {"title": "x"}}, {"tracks": []})
    assert Path(out["project"]).is_file() and Path(out["timeline"]).is_file()
    assert json.loads(Path(out["project"]).read_text(encoding="utf-8"))["meta"]["title"] == "x"


# ────────────────────────────── API ──────────────────────────────


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    from clipwright.api import workspace as workspace_api

    app = FastAPI()
    app.include_router(workspace_api.router)
    return TestClient(app)


def test_api_create_get_and_404(client) -> None:
    res = client.post("/api/workspace", json={"project_id": "proj_abc123", "name": "演示"})
    assert res.status_code == 200
    body = res.json()
    assert body["exists"] is True and Path(body["root"]).is_dir()
    assert body["counts"]["total"] == 0

    got = client.get("/api/workspace/proj_abc123")
    assert got.status_code == 200 and got.json()["root"] == body["root"]

    missing = client.get("/api/workspace/proj_nope00")
    assert missing.status_code == 404


def test_api_rejects_root_outside_workspace_dir(client) -> None:
    res = client.post("/api/workspace", json={"project_id": "p1", "root": "C:/Windows"})
    assert res.status_code == 400


def test_api_upload_and_delete_record(client, monkeypatch) -> None:
    client.post("/api/workspace", json={"project_id": "proj_abc123"})
    _fake_indexers(monkeypatch)

    files = {"file": ("clip.mp4", b"abcd" * 100, "video/mp4")}
    up = client.post("/api/workspace/proj_abc123/materials", files=files)
    assert up.status_code == 200
    body = up.json()
    assert Path(body["saved"]).is_file()

    listed = client.get("/api/workspace/proj_abc123/materials").json()
    assert listed["count"] == 1
    sha = listed["items"][0]["sha256"]
    path = Path(listed["items"][0]["path"])

    # 默认只删记录，不删盘
    deleted = client.delete(f"/api/workspace/proj_abc123/materials/{sha}")
    assert deleted.status_code == 200 and deleted.json()["purged"] is False
    assert path.is_file()
    assert client.get("/api/workspace/proj_abc123/materials").json()["count"] == 0


def test_api_rejects_unknown_extension(client) -> None:
    client.post("/api/workspace", json={"project_id": "proj_abc123"})
    res = client.post("/api/workspace/proj_abc123/materials",
                      files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 400


# ────────────────────────────── 主 Agent 双轨 ──────────────────────────────


@pytest.mark.asyncio
async def test_proceed_as_task_creates_task_and_workspace(tmp_path, monkeypatch) -> None:
    from clipwright.api import requirements as req_api
    from clipwright.schema.pipeline import PipelineRequest
    import clipwright.services.agent_kernel as kernel_mod

    created: dict[str, object] = {}

    class _FakeKernel:
        def create(self, request):
            created["request"] = request
            return SimpleNamespace(id="task_abcdef", plan=None)

        async def plan(self, task):
            created["planned"] = True

    monkeypatch.setattr(kernel_mod, "AgentKernel", _FakeKernel)
    monkeypatch.setattr(req_api, "_service",
                        SimpleNamespace(_persist=lambda *a, **k: None))

    from clipwright.api import task as task_api
    monkeypatch.setattr(task_api, "_register_owner", lambda *a, **k: None)
    monkeypatch.setattr(task_api, "_schedule", lambda *a, **k: created.setdefault("scheduled", True))

    pipeline_req = PipelineRequest(
        persona_id="default", category_plugin_id="knowledge_longform", topic="测试选题",
        extra_params={"script_text": "甲。乙。", "audio_duration_sec": 30.0},
    )
    out = await req_api._proceed_as_task(
        session={"messages": [], "creative_brief": {"x": 1}, "production_plan": {}, "user_inputs": {}},
        req=SimpleNamespace(session_id="sess_1", project_id="proj_abc123"),
        request=SimpleNamespace(headers={}, state=SimpleNamespace(user_id=None)),
        user_inputs={"workspace_name": "演示"},
        plan_data={"scene_count": 2},
        pipeline_req=pipeline_req,
        pipeline_id="pl_x",
    )

    assert out["task_id"] == "task_abcdef"
    assert out["pipeline_id"] == out["task_id"]       # 兼容字段
    assert out["mode"] == "task"
    assert Path(out["workspace_root"]).is_dir()
    assert created.get("planned") is True and created.get("scheduled") is True
    task_req = created["request"]
    assert task_req.extra["workspace_root"] == out["workspace_root"]
    assert task_req.extra["session_id"] == "sess_1"
    assert task_req.plan_only is False


def test_opt_bool_normalizes_strings() -> None:
    from clipwright.api.requirements import _opt_bool

    assert _opt_bool(True) is True
    assert _opt_bool(False) is False
    assert _opt_bool(None) is None
    assert _opt_bool("") is None
    assert _opt_bool("false") is False          # str 不能被 bool() 当真
    assert _opt_bool("true") is True
    assert _opt_bool("on") is True
