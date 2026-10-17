"""P6 测试：插件页/界面贡献的可见性策略。

产品确认的策略（2026-09-13）：
- **默认**：插件贡献的页面/界面**所有登录用户**可访问（不改变既有行为）；
- **可选**：插件在贡献 meta 里声明 `admin_only: true`，则该贡献只投递给管理员
  （后端直接不投递——非管理员拿不到定义，前端无从渲染，比前端隐藏更可靠）。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import plugin as plugin_api
from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)


def _client(monkeypatch, *, role: str | None) -> TestClient:
    """构造带指定角色的请求（None = off/token 模式，视为管理员语义）。"""
    app = FastAPI()
    app.include_router(plugin_api.router)

    @app.middleware("http")
    async def _inject_role(request, call_next):
        # 模拟鉴权中间件写入 request.state（jwt 模式：user_id 与 role 同时存在；
        # off/token 模式下 user_id 为 None，按仓库既有语义视为管理员）
        request.state.user_role = role
        request.state.user_id = None if role is None else "u_1"
        return await call_next(request)

    monkeypatch.setattr(plugin_api, "_maybe_refresh_builtin_mirror", lambda: None)
    return TestClient(app)


def _page_slot(cid: str, *, admin_only: bool) -> Contribution:
    meta = {"slot": "page", "title": cid, "payload": {"path": cid}}
    if admin_only:
        meta["admin_only"] = True
    return Contribution(ContributionKind.UI_SLOT, cid, None, meta=meta)


def _body(client: TestClient) -> dict:
    resp = client.get("/api/plugin/contributions", params={"slot": "page"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_default_visible_to_all_logged_in_users(monkeypatch):
    """默认策略：普通用户也能拿到插件页贡献（既有行为不变）。"""
    ContributionRegistry.clear_all()
    ContributionRegistry.register("plug_open", [_page_slot("open_page", admin_only=False)],
                                  dispatch=False)
    body = _body(_client(monkeypatch, role="user"))
    assert [c["id"] for c in body["contributions"]] == ["open_page"]


def test_admin_only_hidden_from_regular_users(monkeypatch):
    ContributionRegistry.clear_all()
    ContributionRegistry.register("plug_admin", [_page_slot("admin_page", admin_only=True)],
                                  dispatch=False)
    body = _body(_client(monkeypatch, role="user"))
    assert body["contributions"] == []


def test_admin_only_visible_to_admins(monkeypatch):
    ContributionRegistry.clear_all()
    ContributionRegistry.register("plug_admin", [_page_slot("admin_page", admin_only=True)],
                                  dispatch=False)
    body = _body(_client(monkeypatch, role="admin"))
    assert [c["id"] for c in body["contributions"]] == ["admin_page"]


def test_off_token_mode_treated_as_admin(monkeypatch):
    """off/token 模式没有角色信息（user_role=None）——本地/单机部署不受影响。"""
    ContributionRegistry.clear_all()
    ContributionRegistry.register("plug_admin", [_page_slot("admin_page", admin_only=True)],
                                  dispatch=False)
    body = _body(_client(monkeypatch, role=None))
    assert [c["id"] for c in body["contributions"]] == ["admin_page"]


def test_mixed_contributions_filtered_independently(monkeypatch):
    ContributionRegistry.clear_all()
    ContributionRegistry.register("plug_mix", [
        _page_slot("open_page", admin_only=False),
        _page_slot("admin_page", admin_only=True),
    ], dispatch=False)
    user_ids = [c["id"] for c in _body(_client(monkeypatch, role="user"))["contributions"]]
    admin_ids = [c["id"] for c in _body(_client(monkeypatch, role="admin"))["contributions"]]
    assert user_ids == ["open_page"]
    assert admin_ids == ["admin_page", "open_page"]
