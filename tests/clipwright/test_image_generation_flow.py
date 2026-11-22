"""图像生成端到端（服务 + 工具 + 工作区素材可见性 + 时间线插入）。

用假 provider，不联网、不计费；工作区用 tmp_path。
"""

from __future__ import annotations

import asyncio

from pathlib import Path

import pytest

from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.providers.base import (
    GeneratedImage,
    ImageProviderCapabilities,
)
from clipwright.schema.timeline import Timeline
from clipwright.services import provider_sources as ps
from clipwright.services import task_blackboard as bb
from plugins.image_generate.service import ImageGenerationService
from clipwright.services.workspace_materials import ensure_workspace_source
from plugins.image_generate.tool import ImageGenerateTool
from clipwright.tool.timeline_insert_image import TimelineInsertImageTool

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


class FakeProvider:
    name = "fake_img"

    def __init__(self) -> None:
        self.calls = 0
        self.batch_calls = 0

    def default_model(self) -> str:
        return "fake-model"

    def capabilities(self, model: str = "") -> ImageProviderCapabilities:
        return ImageProviderCapabilities(
            sizes=("1024x1024",),
            max_n=2,
            cost_per_image_usd=0.01,
            supports_batch=True,
        )

    async def probe(self, config=None) -> bool:
        return True

    async def generate(self, request):
        self.calls += 1
        return [GeneratedImage(data=PNG, mime="image/png")]

    async def generate_batch(self, requests):
        self.batch_calls += 1
        return [[GeneratedImage(data=PNG, mime="image/png")] for _ in requests]


@pytest.fixture
def fake_provider(monkeypatch):
    provider = FakeProvider()
    ContributionRegistry.declared_scope("test_img", ["image_provider:fake_img"])
    ContributionRegistry.register(
        "test_img",
        [Contribution(ContributionKind.IMAGE_PROVIDER, "fake_img", provider)],
        dispatch=False,
    )
    monkeypatch.setattr(ps, "get_roles",
                        lambda: {"image": {"source_id": "src_1", "model": "fake-model"}})
    monkeypatch.setattr(ps, "get_source", lambda sid, public=True: {
        "id": "src_1", "name": "fake", "kind": "image", "provider": "fake_img",
        "models": [{"id": "fake-model", "enabled": True}],
    })
    monkeypatch.setattr(ps, "resolve_for_source",
                        lambda sid: {"provider": "fake_img", "api_key": "k",
                                     "base_url": "https://gw.example/v1"})
    try:
        yield provider
    finally:
        ContributionRegistry.unregister_plugin("test_img")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    (root / "materials" / "image").mkdir(parents=True)
    (root / "cache").mkdir(parents=True)
    return root


@pytest.mark.asyncio
async def test_generate_writes_workspace_and_reuses(workspace, fake_provider):
    svc = ImageGenerationService(workspace)
    first = await svc.generate(prompt="a cat", size="1024x1024")
    assert first.ok, first.error
    assert first.rel_path.startswith("materials/image/")
    assert (workspace / first.rel_path).is_file()
    assert first.cost_usd == 0.01

    second = await svc.generate(prompt="a cat", size="1024x1024")
    assert second.ok and second.reused is True
    assert second.rel_path == first.rel_path
    assert fake_provider.calls == 1  # 同参数不重复调用 provider


@pytest.mark.asyncio
async def test_generate_many_uses_provider_batch(workspace, fake_provider):
    svc = ImageGenerationService(workspace)
    out = await svc.generate_many(prompts=["a", "b"], size="1024x1024")
    assert [o.ok for o in out] == [True, True]
    assert fake_provider.batch_calls == 1
    assert fake_provider.calls == 0


@pytest.mark.asyncio
async def test_generate_rejects_bad_size(workspace, fake_provider):
    svc = ImageGenerationService(workspace)
    outcome = await svc.generate(prompt="a cat", size="800x600")
    assert outcome.ok is False
    assert "不受支持" in outcome.error


def test_plan_does_not_touch_disk(workspace, fake_provider):
    svc = ImageGenerationService(workspace)
    plan = svc.plan(prompt="a cat", size="1024x1024")
    assert plan["provider"] == "fake_img"
    assert plan["model"] == "fake-model"
    assert not list((workspace / "materials" / "image").iterdir())


@pytest.mark.asyncio
async def test_tool_generates_and_registers_workspace_source(workspace, fake_provider):
    tool = ImageGenerateTool()
    result = await tool.execute(prompt="a dog", size="1024x1024",
                                workspace_root=str(workspace))
    assert str(result.status) == "success", result.error
    rel = result.output["rel_path"]
    assert (workspace / rel).is_file()

    sid = ensure_workspace_source(workspace)
    assert sid
    from clipwright.material.registry import MaterialRegistry

    src = MaterialRegistry.get(sid)
    hits = await src.search("a dog", top_k=5)
    assert hits and Path(hits[0][0].local_path).name == Path(rel).name
    MaterialRegistry.unregister(sid)


def test_tool_dry_run_reports_plan(workspace, fake_provider):
    import asyncio

    tool = ImageGenerateTool()
    result = asyncio.run(tool.execute(prompt="x", workspace_root=str(workspace),
                                      dry_run=True))
    assert str(result.status) == "success"
    assert result.output["planned"]["provider"] == "fake_img"
    assert not list((workspace / "materials" / "image").iterdir())


def _seed_task(task_id: str, workspace: Path) -> None:
    bb.reset(task_id)
    timeline = Timeline(id="tl1", tracks=[])
    bb.put(task_id, "timeline", timeline.model_dump(mode="json"), by="edit")


@pytest.mark.asyncio
async def test_timeline_insert_image_adds_clip(workspace, fake_provider):
    img = workspace / "materials" / "image" / "shot.png"
    img.write_bytes(PNG)
    _seed_task("task_img_1", workspace)
    tool = TimelineInsertImageTool()

    dry = await tool.execute(task_id="task_img_1", asset_ref="materials/image/shot.png",
                             position="end", workspace_root=str(workspace), dry_run=True)
    assert str(dry.status) == "success", dry.error
    assert dry.output["dry_run"] is True

    res = await tool.execute(task_id="task_img_1", asset_ref="materials/image/shot.png",
                             position="end", workspace_root=str(workspace))
    assert str(res.status) == "success", res.error
    assert res.output["clip_id"]

    raw = bb.get("task_img_1")["timeline"]
    kinds = [c["kind"] for t in raw["tracks"] for c in t["clips"]]
    assert "image" in kinds
    bb.reset("task_img_1")


@pytest.mark.asyncio
async def test_timeline_insert_image_at_second(workspace, fake_provider):
    img = workspace / "materials" / "image" / "at.png"
    img.write_bytes(PNG)
    _seed_task("task_img_at", workspace)
    tool = TimelineInsertImageTool()
    res = await tool.execute(task_id="task_img_at", asset_ref="materials/image/at.png",
                             position="at:2.5", duration_sec=3,
                             workspace_root=str(workspace))
    assert str(res.status) == "success", res.error
    raw = bb.get("task_img_at")["timeline"]
    clips = [c for t in raw["tracks"] for c in t["clips"]]
    assert clips[0]["start_sec"] == 2.5
    bb.reset("task_img_at")


@pytest.mark.asyncio
async def test_image_generate_batch_partial_failure(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from plugins.image_generate import tool as tool_mod

    class FakeService:
        def __init__(self, _root):
            self.root = _root

        async def generate(self, *, prompt, **_kwargs):
            if "bad" in prompt:
                raise RuntimeError("provider failed")
            return SimpleNamespace(ok=True, rel_path=f"materials/image/{prompt}.png",
                                   sha256="x", mime="image/png", as_dict=lambda: {
                                       "ok": True, "rel_path": f"materials/image/{prompt}.png",
                                       "sha256": "x", "mime": "image/png",
                                   })

        async def generate_many(self, *, prompts, **_kwargs):
            out = []
            for prompt in prompts:
                try:
                    out.append(await self.generate(prompt=prompt))
                except Exception as exc:  # noqa: BLE001
                    msg = f"{type(exc).__name__}: {exc}"
                    out.append(SimpleNamespace(
                        ok=False,
                        as_dict=lambda msg=msg: {"ok": False, "error": msg},
                    ))
            return out

    monkeypatch.setattr(tool_mod, "ImageGenerationService", FakeService)
    tool = tool_mod.ImageGenerateBatchTool()
    out = await tool.execute(
        prompts=["good", "bad"], workspace_root=str(tmp_path), concurrency=2,
    )
    assert str(out.status) == "success"
    assert out.output["summary"] == {"total": 2, "ok": 1, "failed": 1}
    assert out.output["items"][1]["ok"] is False


@pytest.mark.asyncio
async def test_image_generate_batch_async_mode(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from plugins.image_generate import tool as tool_mod

    class FakeService:
        def __init__(self, _root):
            self.root = _root

        async def generate_many(self, *, prompts, **_kwargs):
            return [SimpleNamespace(ok=True, rel_path=f"materials/image/{p}.png",
                                    as_dict=lambda p=p: {"ok": True,
                                                         "rel_path": f"materials/image/{p}.png"})
                    for p in prompts]

    monkeypatch.setattr(tool_mod, "ImageGenerationService", FakeService)
    out = await tool_mod.ImageGenerateBatchTool().execute(
        prompts=["a"], workspace_root=str(tmp_path), async_mode=True,
    )
    assert out.output["status"] == "running" and out.output["job_id"]
    await asyncio.sleep(0.05)
    job = __import__("clipwright.services.tool_jobs", fromlist=["tool_jobs"]).get(out.output["job_id"])
    assert job is not None and job["status"] == "done"

