import pytest

from clipwright.animation.agent_tools import (
    AnimationToolSpec, call_animation_tool, get_animation_tool, list_animation_tools,
    register_animation_tool,
)


def test_builtin_tools_registered() -> None:
    names = {t.name for t in list_animation_tools()}
    assert {"generate_mg", "validate_layout", "qa_mg", "repair_mg"}.issubset(names)


def test_builtin_cannot_be_overridden() -> None:
    with pytest.raises(ValueError):
        register_animation_tool(AnimationToolSpec("generate_mg", "x", handler=None))


@pytest.mark.asyncio
async def test_plugin_tool_call() -> None:
    async def handler(**kwargs):
        return {"ok": True, "kwargs": kwargs}
    register_animation_tool(AnimationToolSpec("test_echo", "echo", handler=handler, kind="plugin"))
    try:
        assert (await call_animation_tool("test_echo", x=1))["ok"] is True
    finally:
        from clipwright.animation import agent_tools
        agent_tools._TOOLS.pop("test_echo", None)


@pytest.mark.asyncio
async def test_handlers_validate_and_qa() -> None:
    from clipwright.animation.agent_tool_impl import install_animation_tool_handlers
    install_animation_tool_handlers()
    mg = {"shot_type": "comparison", "elements": [
        {"type": "shape", "x": 100, "y": 100, "width": 500, "height": 300},
        {"type": "text", "content": "A", "x": 100, "y": 200, "width": 500},
    ]}
    layout = await call_animation_tool("validate_layout", mg_def=mg)
    assert layout["ok"] is False
    qa = await call_animation_tool("qa_mg", mg_def=mg)
    assert "text_issues" in qa
