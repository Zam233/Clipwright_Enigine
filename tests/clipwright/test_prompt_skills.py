from clipwright.animation.prompt_skills import compose_skills, select_skills


def test_flow_skills_include_arrow_and_core() -> None:
    body = compose_skills(category="flow", shot_type="flow")
    assert "n-1" in body
    assert "bbox" in body


def test_text_skills_do_not_include_flow_arrow() -> None:
    body = compose_skills(category="text", shot_type="quote")
    assert "text_align" in body
    assert "n-1" not in body


def test_plugin_prompt_becomes_prompt_skill() -> None:
    from clipwright.plugins.prompt_registry import PluginPromptRegistry
    from clipwright.animation.prompt_skills import SKILLS
    PluginPromptRegistry.register("test_prompt_plugin", "animation", "PLUGIN_FLOW_RULE", priority=5)
    try:
        body = compose_skills(category="flow", shot_type="flow")
        assert "PLUGIN_FLOW_RULE" in body
        assert any(k.startswith("plugin:test_prompt_plugin:") for k in SKILLS)
    finally:
        PluginPromptRegistry.unregister("test_prompt_plugin")
        for k in [k for k in SKILLS if k.startswith("plugin:test_prompt_plugin:")]:
            SKILLS.pop(k, None)
