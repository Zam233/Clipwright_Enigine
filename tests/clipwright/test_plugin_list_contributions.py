"""P12 测试：/api/plugin/list 暴露每插件贡献清单与「声明未注册」项。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import plugin as plugin_api
from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.schema.plugin import ContributionDecl, PluginManifest, PluginMetadata


class _FakeLoader:
    plugin_dir = None

    def __init__(self, items):
        self._items = items

    def list_loaded(self):
        return list(self._items)


def _meta(pid: str, declared: list[tuple[str, str]]) -> PluginMetadata:
    return PluginMetadata(
        manifest=PluginManifest(
            id=pid, name=pid, version="1.0.0", kind="capability",
            contributions=[ContributionDecl(kind=k, id=i) for k, i in declared],
        ),
    )


def _client(monkeypatch, items) -> TestClient:
    monkeypatch.setattr(plugin_api, "_loader", _FakeLoader(items))
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


def test_list_reports_registered_and_declared_only(monkeypatch):
    ContributionRegistry.clear_all()
    monkeypatch.setattr(plugin_api, "_invalidate_plugin_caches", lambda: None)
    ContributionRegistry.register("plug_meta", [
        Contribution(ContributionKind.UI_SLOT, "declared_slot", None,
                     meta={"slot": "settings.section", "title": "T"}),
    ], dispatch=False)
    items = [
        _meta("plug_meta", [("ui_slot", "declared_slot"), ("tool", "missing_tool")]),
        _meta("plug_empty", []),
    ]
    with _client(monkeypatch, items) as _:
        pass
    client = _client(monkeypatch, items)
    body = client.get("/api/plugin/list").json()
    by_id = {p["manifest"]["id"]: p for p in body}

    assert by_id["plug_meta"]["contributions"] == [
        {"kind": "ui_slot", "id": "declared_slot"},
    ]
    # 声明了但没注册 → declared_only（管理面能看出「加载成功但零贡献」）
    assert by_id["plug_meta"]["declared_only"] == ["tool:missing_tool"]
    assert by_id["plug_empty"]["contributions"] == []
    assert by_id["plug_empty"]["declared_only"] == []


def test_list_without_loader_returns_empty(monkeypatch):
    monkeypatch.setattr(plugin_api, "_loader", None)
    app = FastAPI()
    app.include_router(plugin_api.router)
    assert TestClient(app).get("/api/plugin/list").json() == []
