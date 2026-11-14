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
