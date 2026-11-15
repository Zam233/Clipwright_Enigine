"""图像 provider 契约 / API / 插件（image-provider-plugin PRD M1/M2/M4/M5）。

覆盖：
- 合法 image_provider 能被加载并出现在门面；
- 缺 `generate`/`default_model` 的 provider 在注册期被拒且原因可读；
- `/api/providers/available` 暴露 `image_provider` 与 `role_kinds.image`；
- 两个插件的能力声明（sizes / default_model）符合 PRD。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import providers as providers_api
from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
from clipwright.plugins.loader import PluginLoadError, PluginLoader


@pytest.fixture
def loader(tmp_path):
    return PluginLoader(Path("plugins"), tmp_path / "PluginData")


@pytest.fixture
def client(monkeypatch, loader):
    # /available 会读全局门面；这里加载两个图像插件，验证"插件存在即可见"
    loader.load("image_providers")
    loader.load("image_gpt_images")
    app = FastAPI()
    app.include_router(providers_api.router)
    try:
        yield TestClient(app)
    finally:
        loader.unload("image_gpt_images")
        loader.unload("image_providers")


def test_image_providers_are_registered(loader):
    loader.load("image_providers")
    try:
        impl = ContributionRegistry.resolve(
            ContributionKind.IMAGE_PROVIDER, "openai_compatible")
        assert impl is not None
        caps = impl.object.capabilities()
        assert caps.sizes == "any"
        assert caps.size_dialect == "wxh"
        assert impl.object.default_model() == "gpt-image-1"
        assert ContributionRegistry.owner_of(
            ContributionKind.IMAGE_PROVIDER, "openai_compatible") == "image_providers"
    finally:
        loader.unload("image_providers")


def test_gpt_images_provider_capabilities(loader):
    loader.load("image_gpt_images")
    try:
        impl = ContributionRegistry.resolve(
            ContributionKind.IMAGE_PROVIDER, "openai_gpt_image")
        assert impl is not None
        caps = impl.object.capabilities()
        assert set(caps.sizes) == {"1024x1024", "1536x1024", "1024x1536", "auto"}
        assert caps.max_n == 10
        assert caps.size_dialect == "openai_size"
        assert impl.object.default_model() == "gpt-image-1"
    finally:
        loader.unload("image_gpt_images")


def test_invalid_image_provider_is_rejected(tmp_path):
    """缺 generate() 的 provider：注册期拒绝，错误可读（不连累其它贡献）。"""
    root = tmp_path / "plugins" / "bad_image_provider"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: bad_image_provider\nname: bad\nversion: 1.0.0\nkind: capability\n"
        "api_version: \"2\"\ncontributions:\n  - kind: image_provider\n    id: broken\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "from clipwright.plugins.base import BasePlugin\n"
        "from clipwright.plugins.contribution import Contribution, ContributionKind\n"
        "\n"
        "class Broken:\n"
        "    name = 'broken'\n"
        "    def default_model(self):\n"
        "        return 'x'\n"
        "    def capabilities(self, model=''):\n"
        "        return {}\n"
        "\n"
        "class BadPlugin(BasePlugin):\n"
        "    def contributions(self):\n"
        "        return [Contribution(ContributionKind.IMAGE_PROVIDER, 'broken', Broken())]\n",
        encoding="utf-8",
    )
    loader = PluginLoader(tmp_path / "plugins", tmp_path / "data")
    with pytest.raises(PluginLoadError) as exc:
        loader.load("bad_image_provider")
    assert "generate" in str(exc.value)


def test_available_exposes_image_kind(client):
    body = client.get("/api/providers/available").json()
    assert "openai_compatible" in body["image_provider"]
    assert "openai_gpt_image" in body["image_provider"]
    assert body["role_kinds"]["image"] == "image"


def test_schemas_for_image_kind(client):
    body = client.get("/api/providers/schemas", params={"kind": "image"}).json()
    ids = {item["id"] for item in body["schemas"]["image_provider"]}
    assert {"openai_compatible", "openai_gpt_image"} <= ids


# ── 插件 HTTP 行为（桩网关，不联网）──────────────────────────────

def _install_mock_httpx(monkeypatch, handler):
    import httpx

    class _FakeClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_openai_compatible_probe_uses_injected_config(monkeypatch):
    from plugins.image_providers.main import OpenAICompatibleImageProvider

    seen = {}

    def handler(request):
        import httpx as _httpx
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return _httpx.Response(200, json={"data": []})

    _install_mock_httpx(monkeypatch, handler)
    impl = OpenAICompatibleImageProvider()
    ok = await impl.probe({"base_url": "https://gw.example/v1", "api_key": "sk-x"})
    assert ok is True
    assert seen["url"].endswith("/v1/models")
    assert seen["auth"] == "Bearer sk-x"


@pytest.mark.asyncio
async def test_openai_compatible_generate_b64(monkeypatch):
    import base64
    import httpx

    from plugins.image_providers.main import OpenAICompatibleImageProvider

    payload = b"\x89PNG fake"

    def handler(request):
        body = __import__("json").loads(request.content)
        assert body["response_format"] == "b64_json"
        return httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(payload).decode()}]
        })

    _install_mock_httpx(monkeypatch, handler)
    impl = OpenAICompatibleImageProvider(base_url="https://gw.example/v1", api_key="sk-x")
    images = await impl.generate(_req())
    assert images and images[0].data == payload


@pytest.mark.asyncio
async def test_openai_compatible_generate_url_download_and_ssrf(monkeypatch):
    import httpx

    import clipwright.security as security
    from clipwright.providers.base import ImageGenerationRequest
    from plugins.image_providers.main import OpenAICompatibleImageProvider

    monkeypatch.setattr(security, "assert_public_url", lambda url: None)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.jpg"}]})
        return httpx.Response(200, content=b"JPEGDATA",
                              headers={"content-type": "image/jpeg"})

    _install_mock_httpx(monkeypatch, handler)
    impl = OpenAICompatibleImageProvider(base_url="https://gw.example/v1", api_key="sk-x")
    images = await impl.generate(ImageGenerationRequest(prompt="cat"))
    assert images[0].data == b"JPEGDATA"
    assert images[0].mime == "image/jpeg"


@pytest.mark.asyncio
async def test_openai_compatible_generate_401_raises(monkeypatch):
    import httpx

    from plugins.image_providers.main import OpenAICompatibleImageProvider

    def handler(request):
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    _install_mock_httpx(monkeypatch, handler)
    impl = OpenAICompatibleImageProvider(base_url="https://gw.example/v1", api_key="sk-x")
    with pytest.raises(RuntimeError) as exc:
        await impl.generate(_req())
    assert "401" in str(exc.value) and "invalid key" in str(exc.value)


@pytest.mark.asyncio
async def test_gpt_images_rejects_bad_size(monkeypatch):
    from plugins.image_gpt_images.main import GPTImagesProvider

    impl = GPTImagesProvider(base_url="https://gw.example/v1", api_key="sk-x")
    with pytest.raises(ValueError):
        await impl.generate(_req(size="800x600"))


def _req(size: str = ""):
    from clipwright.providers.base import ImageGenerationRequest

    return ImageGenerationRequest(prompt="a cat", size=size)


def test_image_source_test_endpoint_passes_resolved_config(client, monkeypatch):
    """/test 对 image 源：宿主注入源配置，插件 probe 才能真的带 key。"""
    import httpx

    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    _install_mock_httpx(monkeypatch, handler)
    created = client.post("/api/providers", json={
        "name": "img-gw", "kind": "image", "provider": "openai_compatible",
        "base_url": "https://gw.example/v1",
        "api_keys": ["sk-test"],
        "models": [{"id": "gpt-image-1", "name": "gpt-image-1", "enabled": True}],
    })
    assert created.status_code == 200, created.text
    sid = created.json()["id"]
    result = client.post(f"/api/providers/{sid}/test").json()
    assert result["ok"] is True, result
    assert seen["auth"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_image_model_listing_via_plugin_spec(loader, monkeypatch):
    """图像 provider 声明 model_listing_spec → 「获取模型列表」按 /models 拉取。"""
    import httpx

    from clipwright.providers.model_listing import list_models_result

    loader.load("image_providers")
    try:
        def handler(request):
            assert str(request.url).endswith("/models")
            assert request.headers.get("authorization") == "Bearer sk-x"
            return httpx.Response(200, json={"data": [{"id": "gpt-image-2.5"}]})

        _install_mock_httpx(monkeypatch, handler)
        res = await list_models_result(
            "openai_compatible",
            {"base_url": "https://gw.example/v1", "api_key": "sk-x"},
            kind="image",
        )
        assert res.status == "ok", res
        assert res.models == ["gpt-image-2.5"]
    finally:
        loader.unload("image_providers")
