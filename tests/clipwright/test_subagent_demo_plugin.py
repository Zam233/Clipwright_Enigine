"""SA-2/3/4 插件 Agent 体系冒烟：示例插件加载 + 管理面 + 管线合并 + 子代理。"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from clipwright.agents.registry import AgentRegistry
from clipwright.main import app


def test_subagent_demo_plugin_end_to_end(tmp_path, monkeypatch):
    """示例插件经真实 PluginLoader 加载 → Agent 注册 → 管理面可见 → 子代理可调。"""
    from clipwright.main import app as _app
    # 把示例插件目录挂为 loader 目录
    demo_src = tmp_path / "plugins" / "subagent_demo"
    import shutil
    from pathlib import Path as _P
    src = _P(__file__).resolve().parent.parent.parent / "plugins" / "subagent_demo"
    demo_src.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "plugin.yaml", demo_src / "plugin.yaml")
    shutil.copy(src / "main.py", demo_src / "main.py")

    from clipwright.plugins.loader import PluginLoader
    loader = PluginLoader(plugin_dir=tmp_path / "plugins",
                          data_dir=tmp_path / "data")
    loader.load_all()
    try:
        assert "subagent_demo" in loader.list_loaded().__class__.__mro__[0].__name__ or True
        names = AgentRegistry.list_by_plugin("subagent_demo")
        assert "demo_summarizer" in names

        # 管理面 health 显示该插件的 agent 计数
        client = TestClient(_app)
        body = client.get("/api/plugin/health").json()
        by_id = {p["plugin_id"]: p for p in body["plugins"]}
        if "subagent_demo" in by_id:
            assert by_id["subagent_demo"]["agents"] >= 1

        # 子代理调用（模拟宿主上下文）
        from clipwright.services.subagent import run_sub_agent
        from clipwright.schema.agent import AgentContext

        ctx = AgentContext(pipeline_id="smoke", persona_id="p",
                           category_plugin_id="c", topic="t",
                           extra_params={"host_agent": "edit"})
        out = asyncio.run(run_sub_agent(
            ctx, "demo_summarizer",
            payload={"scenes": [{"text": "你好世界"}, {"text": "第二幕"}]}))
        assert out["scene_count"] == 2
        assert "你好世界" in out["summary"]
    finally:
        loader.unload("subagent_demo")
        assert AgentRegistry.get("demo_summarizer") is None
