"""展示层提示词接线：`prompts.py` 占位符 + **生产简报路径消费插件 PROMPT**。

这是"模型知道怎么写 spec"的唯一通道：插件 PROMPT 由
`requirements_service._brief_system_prompt()` 追加到 base 提示词末尾。
插件被禁用时只是少一段说明（A6 口径：禁用即无 spec，但业务写入照常）。
"""
from __future__ import annotations


def test_prompts_constant_has_placeholder():
    from clipwright import prompts

    assert "{{BRIEF_TEMPLATE}}" in prompts.CREATIVE_BRIEF_SYSTEM


def test_agent_renders_placeholder_at_runtime():
    from clipwright.agents.requirements_agent import _brief_system_prompt

    rendered = _brief_system_prompt()
    assert "{{BRIEF_TEMPLATE}}" not in rendered
    assert "| 字段 | 类型 | 说明 |" in rendered
    assert "display_specs" in rendered, "字段表应包含展示容器字段"


def test_service_prompt_consumes_plugin_registry(monkeypatch):
    from clipwright.plugins.prompt_registry import PluginPromptRegistry
    from clipwright.services import requirements_service as rs

    monkeypatch.setattr(
        PluginPromptRegistry, "get_for_agent",
        classmethod(lambda cls, agent: ["FAKE_GENUI_RULES"]),
    )
    prompt = rs._brief_system_prompt("CTX")
    assert "FAKE_GENUI_RULES" in prompt
    assert "CTX" in prompt
    assert prompt.rstrip().endswith("FAKE_GENUI_RULES")
    assert "字段" in prompt


def test_service_prompt_without_plugins(monkeypatch):
    from clipwright.plugins.prompt_registry import PluginPromptRegistry
    from clipwright.services import requirements_service as rs

    monkeypatch.setattr(
        PluginPromptRegistry, "get_for_agent", classmethod(lambda cls, agent: []),
    )
    prompt = rs._brief_system_prompt("")
    assert "FAKE_GENUI_RULES" not in prompt
    assert prompt.strip()
