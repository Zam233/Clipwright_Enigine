"""web_search / web_fetch 的可见性语义 + 执行通道（含子代理路径）。

背景（这次要修的真问题）：这两个工具此前 `is_available()` 返回"是否已配置搜索服务"，
于是未配置环境下它们**根本不进 Kernel 的工具面**——主 agent 连"我能联网"都不知道，
更不可能去决策"我需要搜一下"。正确语义：
- 工具本体可见（`is_available()` 只反映外部二进制依赖）；
- 配置缺失在**执行时**如实回报 DEPENDENCY_MISSING。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.tool import register_builtin_tools
from clipwright.tool.registry import ToolRegistry
from clipwright.tool.surface import build_surface


@pytest.fixture(autouse=True)
def _registered():
    register_builtin_tools()


def test_web_tools_are_available_and_in_surface():
    for name in ("web_search", "web_fetch"):
        tool = ToolRegistry.get(name)
        assert tool is not None
        assert tool.is_available() is True, "配置缺失不等于工具不可用"
        assert tool.agent_callable is True
    names = {e.name for e in build_surface()}
    assert {"web_search", "web_fetch"} <= names, "主 agent 的工具面必须包含联网能力"


def test_web_search_without_config_reports_dependency_missing(monkeypatch):
    from clipwright.tool import web_search_tool as tool_mod

    class _Unconfigured:
        def is_configured(self) -> bool:
            return False

    monkeypatch.setattr(tool_mod, "get_web_search_service", lambda: _Unconfigured())
    out = asyncio.run(ToolRegistry.get("web_search").execute(query="测试"))
    assert out.status == "dependency_missing"
    assert out.output["results"] == []


def test_web_fetch_without_config_reports_dependency_missing(monkeypatch):
    """注意：工具模块是 `from ... import get_web_fetch_service` 绑定的，
    必须 patch **工具模块里的那个名字**，否则 patch 不生效（会用真实服务联网）。"""
    from clipwright.tool import web_search_tool as tool_mod

    class _Unconfigured:
        async def fetch(self, url, max_chars=4000):  # noqa: ANN001, ARG002
            return ""

    monkeypatch.setattr(tool_mod, "get_web_fetch_service", lambda: _Unconfigured())
    out = asyncio.run(ToolRegistry.get("web_fetch").execute(url="https://example.com"))
    assert out.status == "dependency_missing"


def test_surface_execute_web_search_returns_honest_result(monkeypatch):
    """经 surface 调用：返回统一形状，status 不会是伪造的 ok。"""
    from clipwright.services import web_search as ws
    from clipwright.tool import surface as sf

    class _Unconfigured:
        def is_configured(self) -> bool:
            return False

    monkeypatch.setattr(ws, "get_web_search_service", lambda: _Unconfigured())
    out = asyncio.run(sf.execute("web_search", {"query": "x"}))
    assert out["status"] == "error"
    assert out["record"]["name"] == "web_search"


def test_worker_can_search_and_write(monkeypatch, tmp_path):
    """子代理侧：web_search / write_file 都在授权集内且能走通。"""
    from clipwright.supervisor import worker_tools as wt
    from clipwright.supervisor.worker_registry import SUPERVISOR_TOOLS

    assert {"web_search", "web_fetch", "write_file"} <= SUPERVISOR_TOOLS
    schemas = {s["function"]["name"] for s in wt.schemas_for(("web_search", "write_file"))}
    assert schemas == {"web_search", "write_file"}

    # 路径白名单在 test_fs_tools 里单独测；这里只验"工具真的能跑通"
    import clipwright.security as security

    monkeypatch.setattr(security, "assert_allowed_path", lambda p: p)

    ex = wt.build_executor("task_1", "sa_1", allowed=("write_file", "read_file"))
    out = asyncio.run(ex("write_file", {"path": str(tmp_path / "note.md"), "content": "结论"}))
    assert "error" not in out, out
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "结论"

    read_back = asyncio.run(ex("read_file", {"path": str(tmp_path / "note.md")}))
    assert "结论" in read_back["content"]


def test_worker_search_is_authorized_only_when_granted():
    from clipwright.supervisor import worker_tools as wt

    ex = wt.build_executor("task_1", "sa_1", allowed=("read_file",))
    out = asyncio.run(ex("web_search", {"query": "x"}))
    assert "不在该 worker 的授权范围" in out["error"]
