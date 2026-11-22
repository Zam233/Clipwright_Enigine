"""read_image / read_image_batch：所有 Agent 可用的读图工具。"""

from __future__ import annotations

import asyncio

from clipwright.tool.stubs import ReadImageTool
from clipwright.tool.vision_batch import ReadImageBatchTool


def test_names_and_agent_callable() -> None:
    from clipwright.tool import register_builtin_tools
    from clipwright.tool.registry import ToolRegistry

    register_builtin_tools()

    assert ToolRegistry.get("vision_llm") is None
    assert ToolRegistry.get("vision_llm_batch") is None
    assert ToolRegistry.get("read_image") is not None
    assert ToolRegistry.get("read_image_batch") is not None
    assert ReadImageTool.agent_callable is True
    assert ReadImageBatchTool.agent_callable is True


def test_read_image_accepts_local_image(monkeypatch, tmp_path) -> None:
    from PIL import Image

    image = tmp_path / "grid.png"
    Image.new("RGB", (320, 180), "#222222").save(image)

    async def fake_analyze(_self, _path: str, **_kwargs):
        return {"description": "一张动画网格", "tags": ["动画"], "labels": ["grid"]}

    monkeypatch.setattr("clipwright.services.vision.VisionService.analyze_image",
                        fake_analyze)
    out = asyncio.run(ReadImageTool().execute(path=str(image), frame_count=1))
    assert out.status == "success"
    assert out.output["description"] == "一张动画网格"
    assert out.output["path"] == str(image)
    assert image.exists()  # 直接读图不能删除源文件


def test_all_agents_get_read_image_tools(monkeypatch) -> None:
    from clipwright.agents.base import BaseAgent
    from clipwright.tool import register_builtin_tools

    register_builtin_tools()

    class DummyAgent(BaseAgent):
        agent_name = "dummy"

        async def execute(self, input_data, context):
            raise NotImplementedError

    names = {s["function"]["name"] for s in DummyAgent().tool_schemas(include_skills=False)}
    assert {"read_image", "read_image_batch"} <= names
