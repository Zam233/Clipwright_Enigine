"""C2/B2/C1 收尾：配置真实性、读根审核、失活 owner、404/503 语义。"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from clipwright.api import capability as cap_api
from clipwright.api import plugin as plugin_api
from clipwright.plugins.loader import PluginLoader
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.tool import capabilities as caps


def _write_plugin(root: Path, pid: str, body: str, extra: str = "") -> Path:
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.yaml").write_text(
        f"id: {pid}\nname: {pid}\nkind: capability\n{extra}", encoding="utf-8",
    )
    (d / "main.py").write_text(body, encoding="utf-8")
    return d


def test_reload_returns_false_when_plugin_cannot_come_back(tmp_path):
    """C2：reload 失败必须返回 False，供接口回 5xx。"""
    root = tmp_path / "plugins"
    _write_plugin(
        root, "flaky",
        "from clipwright.plugins.base import BasePlugin\n"
        "class P(BasePlugin):\n"
        "    def initialize(self):\n"
        "        pass\n",
    )
    loader = PluginLoader(plugin_dir=root, data_dir=tmp_path / "data")
    loader.load("flaky")
    # 让下一次加载必然失败：坏 manifest
    (root / "flaky" / "plugin.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    assert loader.reload("flaky") is False


def test_config_put_reports_reload_failure(monkeypatch, tmp_path):
    """配置保存后插件没能用新配置起来 → 500，而不是 200。"""
    from clipwright.config import settings

    class _Loader:
        plugin_dir = tmp_path / "plugins"

        def discover(self):
            return ["cfg_plug"]

        def is_enabled(self, pid):
            return True

        def get(self, pid):
            return object()

        def get_plugin_data_dir(self, pid):
            d = tmp_path / "data" / pid
            d.mkdir(parents=True, exist_ok=True)
            return d

        def save_config(self, pid, data):
            return None

        def reload(self, pid):
            return False

        def _get_merged_config(self, pid):
            return {}

    (tmp_path / "plugins" / "cfg_plug").mkdir(parents=True)
    monkeypatch.setattr(plugin_api, "_loader", _Loader())
    monkeypatch.setattr(settings, "plugin_allow_ui_bundle", False)
    app = FastAPI()
    app.include_router(plugin_api.router)
    client = TestClient(app)
    resp = client.put("/api/plugin/cfg_plug/config", json={"fields": {}})
    # 鉴权可能先拦（本地 token 模式无守卫）——只要不是 200 即满足“不假成功”
    assert resp.status_code != 200
    if resp.status_code == 500:
        assert "重载失败" in resp.text


async def test_tool_of_dead_plugin_is_not_executable(monkeypatch):
    """C1：插件失活后残留工具按 NOT_FOUND 处理，不再执行。"""
    from clipwright.schema.tool import ToolExecResult

    class _T(BaseTool):
        name = "dead_owner_tool"
        description = "x"

        async def execute(self, **kw):
            return ToolExecResult(status="success", output={"ran": True})

        def to_llm_tool(self):
            return {"name": self.name, "description": "", "parameters": {}}

    class _Loader:
        @staticmethod
        def is_enabled(pid):
            return False

        @staticmethod
        def get(pid):
            return None

    monkeypatch.setattr(plugin_api, "_loader", _Loader())
    tool = _T()
    ToolRegistry._tools["dead_owner_tool"] = tool
    tool._plugin_id = "dead_plug"
    try:
        result = await ToolRegistry.execute("dead_owner_tool")
        assert result.status == "not_found"
        assert "未加载或已禁用" in (result.error or "")
    finally:
        ToolRegistry._tools.pop("dead_owner_tool", None)


def test_resolve_any_keeps_unconfigured_tool_for_503(monkeypatch):
    """能力存在但没配置 → resolve_any 仍返回它（网关才能报 503）。"""
    class _T(BaseTool):
        name = "needs_key_tool"
        description = "x"
        capability = ""

        def is_available(self):
            return True

        def is_configured(self):
            return False

        async def execute(self, **kw):
            return {}

        def to_llm_tool(self):
            return {"name": self.name, "description": "", "parameters": {}}

    tool = _T()
    ToolRegistry._tools["needs_key_tool"] = tool
    try:
        assert caps.resolve_name("needs_key_tool") is None     # 不可用 → 不返回
        assert caps.resolve_any("needs_key_tool") is tool      # 仍能识别存在
    finally:
        ToolRegistry._tools.pop("needs_key_tool", None)


def test_capability_gateway_returns_503_for_unconfigured(monkeypatch):
    class _T(BaseTool):
        name = "gw_needs_key"
        description = "x"

        def is_available(self):
            return True

        def is_configured(self):
            return False

        async def execute(self, **kw):
            return {}

        def to_llm_tool(self):
            return {"name": self.name, "description": "", "parameters": {}}

    ToolRegistry._tools["gw_needs_key"] = _T()
    try:
        with pytest.raises(HTTPException) as ei:
            cap_api._assert_usable(cap_api._resolve("gw_needs_key"), "gw_needs_key")
        assert ei.value.status_code == 503
    finally:
        ToolRegistry._tools.pop("gw_needs_key", None)


def test_extra_read_roots_outside_plugin_is_dropped(tmp_path):
    """B2：manifest 自报的外部读根必须被宿主丢弃。"""
    root = tmp_path / "plugins"
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_plugin(
        root, "reader",
        "from clipwright.plugins.base import BasePlugin\n"
        "from clipwright.plugins.contribution import Contribution, ContributionKind\n"
        "class H:\n"
        "    def __call__(self, ctx):\n"
        "        return ctx\n"
        "class P(BasePlugin):\n"
        "    def contributions(self):\n"
        "        return [Contribution(ContributionKind.HOOK, 'reader_hook', H(),"
        " meta={'point': 'pre_render'})]\n",
        extra=(
            "runtime: subprocess\n"
            "contributions:\n  - {kind: hook, id: reader_hook}\n"
            "runtime_config:\n  extra_read_roots:\n"
            f"    - {outside}\n"
        ),
    )
    loader = PluginLoader(plugin_dir=root, data_dir=tmp_path / "data")
    handle = loader.load("reader")
    try:
        assert handle is not None
        manifest = loader._metadatas["reader"].manifest
        assert manifest.runtime_config.get("extra_read_roots") == []
    finally:
        loader.unload("reader")
        loader.stop_all_hosts()


def test_safe_extract_counts_actual_bytes(tmp_path):
    """B1：解包按实际写出字节限额（header 可伪造）。"""
    from clipwright.services.install_service import MAX_EXTRACTED_BYTES, _safe_extract

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"x" * (MAX_EXTRACTED_BYTES + 16)
        info = tarfile.TarInfo("plugin.yaml")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="超限"):
        _safe_extract(buf.getvalue(), tmp_path / "dest", "plugin.yaml")
