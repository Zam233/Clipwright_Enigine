"""FallbackEngine LLM 语义填充测试（计划 ux-polish A4）。

覆盖：
- `llm_fill_template_params` 调用 LLM 理解 params 键语义（step1=流程步骤名、
  text=主标题）后按含义填值，而非机械的 | 顺序位置填充；
- Prompt 携带参数键语义提示 + 可用内容 + 输出 schema 仅含模板键；
- LLM 未注入 / 未配置 API key / 无可用内容 / 抛异常 / 输出非法（非 dict、
  未知键、非字符串、空值）→ 一律回退现有 | 位置规则，输出与
  `fill_template_params` 完全一致（既有行为不回归，历史测试继续通过）；
- 部分合法输出与位置基线合并；Persona 主色覆盖始终优先于 LLM accent。
"""
from __future__ import annotations

from typing import Any

import pytest

from clipwright.animation.mg.fallback import FallbackEngine
from clipwright.config import settings

# 与 clipwright/animation/mg/templates/ 下真实模板的 params 结构对齐
FLOW_TEMPLATE = {
    "animation_id": "mg_flow_arrows",
    "params": {
        "text": {"type": "string", "default": "工作流程"},
        "step1": {"type": "string", "default": "分析"},
        "step2": {"type": "string", "default": "设计"},
        "step3": {"type": "string", "default": "开发"},
        "step4": {"type": "string", "default": "上线"},
        "accent": {"type": "string", "default": "#fbbf24"},
    },
}

TITLE_TEMPLATE = {
    "animation_id": "mg_title_reveal",
    "params": {
        "text": {"type": "string", "default": ""},
        "subtitle": {"type": "string", "default": ""},
        "accent": {"type": "string", "default": "#4f8cff"},
    },
}

# 典型流程内容：位置规则会把 text 填成第一步名、最后一步名错位（A4 痛点）
FLOW_CONTENT = "需求分析|设计|开发|上线|产品开发流程"

SEMANTIC_FLOW = {
    "text": "产品开发流程",
    "step1": "需求分析",
    "step2": "设计",
    "step3": "开发",
    "step4": "上线",
}


class FakeLLM:
    """可控 LLM：记录 structured_output 调用，可返回预设结果或抛错。"""

    def __init__(self, result: Any = None, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def structured_output(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("LLM down")
        return self.result or {}


def _enable_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")


def _positional(template: dict[str, Any], text: str, persona: dict | None = None) -> dict[str, str]:
    """当前位置规则的 params（作为回退对照基线）。"""
    _, params = FallbackEngine.fill_template_params(template, text, persona)
    return params


# ── LLM 语义填充：成功路径（断言值，而非调用次数）─────────────────

async def test_llm_fill_respects_key_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 理解 step1=流程步骤名：text 填标题、stepN 填步骤名（按语义非按 | 顺序）。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result=SEMANTIC_FLOW)
    _, params = await FallbackEngine.llm_fill_template_params(FLOW_TEMPLATE, FLOW_CONTENT, llm=fake)

    assert fake.calls, "LLM 应被调用"
    # 值语义正确：text 是总标题，step1 是第一步名
    assert params["text"] == "产品开发流程"
    assert params["step1"] == "需求分析"
    assert params["step2"] == "设计"
    assert params["step3"] == "开发"
    assert params["step4"] == "上线"
    assert params["accent"] == "#fbbf24"  # 颜色键保持默认值

    # 与机械位置填充结果不同（位置规则: text=需求分析, step4=产品开发流程）
    positional = _positional(FLOW_TEMPLATE, FLOW_CONTENT)
    assert params["text"] != positional["text"]
    assert params["step4"] != positional["step4"]


async def test_llm_prompt_contains_key_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompt 携带参数键语义提示（step1→流程第 1 步）+ 可用内容；schema 仅含模板键。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result={"text": "产品开发流程", "step1": "需求分析"})
    await FallbackEngine.llm_fill_template_params(FLOW_TEMPLATE, FLOW_CONTENT, llm=fake)

    assert len(fake.calls) == 1
    system_prompt = fake.calls[0]["system_prompt"]
    user_prompt = fake.calls[0]["user_prompt"]
    # 角色/规则在 system prompt，参数键语义提示在 user prompt
    assert "只输出 JSON" in system_prompt
    assert "语义" in system_prompt
    assert '"step1"' in user_prompt
    assert "流程第 1 步" in user_prompt
    assert "主标题" in user_prompt
    # 可用内容完整传入
    assert "需求分析" in user_prompt and "产品开发流程" in user_prompt
    # 输出 schema 只允许模板参数键（LLM 无法注入未知键）
    schema = fake.calls[0]["output_schema"]
    assert set(schema["properties"].keys()) == set(FLOW_TEMPLATE["params"].keys())


async def test_llm_title_subtitle_filled_by_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """title/subtitle 类键按语义填充（内容顺序与键顺序相反时仍正确映射）。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result={"text": "AI 视频创作工具介绍", "subtitle": "从脚本到成片"})
    _, params = await FallbackEngine.llm_fill_template_params(
        TITLE_TEMPLATE, "从脚本到成片|AI 视频创作工具介绍", llm=fake,
    )
    assert params["text"] == "AI 视频创作工具介绍"
    assert params["subtitle"] == "从脚本到成片"
    # 位置规则会把两者对调 — 语义填充纠正了错位
    positional = _positional(TITLE_TEMPLATE, "从脚本到成片|AI 视频创作工具介绍")
    assert positional["text"] == "从脚本到成片"
    assert params["text"] != positional["text"]


async def test_llm_partial_output_merges_with_positional_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 只填语义明确的键：其余键保持位置基线/默认值（部分合并）。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result={"step1": "需求分析"})
    _, params = await FallbackEngine.llm_fill_template_params(
        FLOW_TEMPLATE, FLOW_CONTENT, llm=fake,
    )
    assert params["step1"] == "需求分析"  # LLM 语义值生效
    assert params["text"] == "需求分析"   # 未覆盖键保持位置基线
    assert params["step2"] == "开发"
    assert params["step3"] == "上线"
    assert params["step4"] == "产品开发流程"
    assert params["accent"] == "#fbbf24"  # 默认值保留


async def test_persona_accent_override_survives_llm_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persona 主色覆盖始终优先于 LLM 输出的 accent。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result={"text": "产品开发流程", "step1": "需求分析", "accent": "#000000"})
    _, params = await FallbackEngine.llm_fill_template_params(
        FLOW_TEMPLATE, FLOW_CONTENT, persona_style={"primary_color": "#ff0000"}, llm=fake,
    )
    assert params["accent"] == "#ff0000"
    assert params["text"] == "产品开发流程"


# ── LLM 不可用 → | 位置规则回退（输出与当前位置行为完全一致）────────

async def test_llm_none_uses_positional_rule() -> None:
    """未注入 llm → 不调用 LLM，结果与 fill_template_params 完全一致。"""
    expected = _positional(FLOW_TEMPLATE, FLOW_CONTENT)
    _, params = await FallbackEngine.llm_fill_template_params(FLOW_TEMPLATE, FLOW_CONTENT)
    assert params == expected


async def test_no_api_key_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置 API key → 不调用 LLM，位置规则结果不变。"""
    monkeypatch.setattr(settings, "llm_api_key", "")
    fake = FakeLLM(result={"text": "hack", "step1": "x"})
    expected = _positional(FLOW_TEMPLATE, FLOW_CONTENT)
    _, params = await FallbackEngine.llm_fill_template_params(
        FLOW_TEMPLATE, FLOW_CONTENT, llm=fake,
    )
    assert fake.calls == [], "未配置 API key 时不得调用 LLM"
    assert params == expected


async def test_llm_exception_falls_back_to_positional(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 抛异常 → 回退位置规则，输出与无 LLM 完全一致。"""
    _enable_llm(monkeypatch)
    expected = _positional(FLOW_TEMPLATE, FLOW_CONTENT)
    _, params = await FallbackEngine.llm_fill_template_params(
        FLOW_TEMPLATE, FLOW_CONTENT, llm=FakeLLM(fail=True),
    )
    assert params == expected


@pytest.mark.parametrize("bad_result", [
    "not a dict",
    "```json\n这不是 JSON\n```",
    {},
    {"step1": 123},                       # 非字符串值
    {"step1": None},                      # 空值
    {"step1": ""},
    {"step1": "   "},
    {"unknown_key": "注入内容"},           # 未知键 → 忽略，无合法值 → 整体回退
    {"content": "JSON 解析失败的兜底形态"},
    {"step1": {"nested": 1}},
])
async def test_llm_malformed_output_falls_back_to_positional(
    monkeypatch: pytest.MonkeyPatch, bad_result: Any,
) -> None:
    """LLM 输出非法 → 整体回退位置规则（输出与无 LLM 完全一致）。"""
    _enable_llm(monkeypatch)
    expected = _positional(FLOW_TEMPLATE, FLOW_CONTENT)
    _, params = await FallbackEngine.llm_fill_template_params(
        FLOW_TEMPLATE, FLOW_CONTENT, llm=FakeLLM(result=bad_result),
    )
    assert params == expected


async def test_no_params_decl_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """模板无 params 声明 → 无键可映射，不调用 LLM，直接位置规则。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result={"text": "hack"})
    template: dict[str, Any] = {"animation_id": "mg_empty", "params": {}}
    expected = _positional(template, "hello")
    _, params = await FallbackEngine.llm_fill_template_params(template, "hello", llm=fake)
    assert fake.calls == []
    assert params == expected


async def test_no_content_pieces_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """可用内容为空（extract_keywords 无结果）→ 不调用 LLM，位置规则回退。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result={"text": "hack"})
    expected = _positional(FLOW_TEMPLATE, "|  | ")
    _, params = await FallbackEngine.llm_fill_template_params(FLOW_TEMPLATE, "|  | ", llm=fake)
    assert fake.calls == []
    assert params == expected


async def test_fallback_identical_to_positional_when_llm_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual-QA：LLM 失败路径与当前位置规则逐字段一致（含 Persona 覆盖）。"""
    _enable_llm(monkeypatch)
    persona = {"primary_color": "#ff0000"}
    expected = _positional(FLOW_TEMPLATE, FLOW_CONTENT, persona)
    _, params = await FallbackEngine.llm_fill_template_params(
        FLOW_TEMPLATE, FLOW_CONTENT, persona_style=persona, llm=FakeLLM(fail=True),
    )
    assert params == expected
    assert params["accent"] == "#ff0000"

# ── 生产接线：_fallback_generate 经 llm_fill_template_params 语义填充 ──

def _make_generator(monkeypatch: pytest.MonkeyPatch, fake_llm: FakeLLM):
    """构造不触网的最小 MGGenerator：替换 self._llm 为可控 FakeLLM。

    用 __new__ 跳过 __init__，避免构造真实 LLMService 客户端。
    """
    from clipwright.animation.mg.generator import MGGenerator
    gen = MGGenerator.__new__(MGGenerator)
    gen._llm = fake_llm
    gen._config = {}
    return gen


def _capture_fallback_params(monkeypatch: pytest.MonkeyPatch, gen, captured: dict) -> None:
    """拦截 _render_html_no_residuals 捕获 params（_build_success 不回传 params）。"""
    async def _fake_render(*args: Any, **kwargs: Any) -> str:
        captured["params"] = dict(args[1])
        return "<div>rendered</div>"
    monkeypatch.setattr(gen, "_render_html_no_residuals", _fake_render)


def _real_flow_template() -> dict[str, Any]:
    """读取 _fallback_generate 实际加载的 mg_flow_arrows.json（与生产同源）。"""
    import json as _json
    from pathlib import Path

    import clipwright.animation.mg.generator as _gen_mod
    path = Path(_gen_mod.__file__).resolve().parent / "templates" / "mg_flow_arrows.json"
    return _json.loads(path.read_text(encoding="utf-8"))


async def test_fallback_generate_overlays_llm_semantic_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产接线：_fallback_generate 把 LLM 语义填充结果送入渲染（断言真实值）。"""
    _enable_llm(monkeypatch)
    fake = FakeLLM(result=SEMANTIC_FLOW)
    gen = _make_generator(monkeypatch, fake)
    captured: dict[str, Any] = {}
    _capture_fallback_params(monkeypatch, gen, captured)

    result = await gen._fallback_generate("讲解产品开发流程步骤", FLOW_CONTENT, {})

    assert result["success"] is True
    assert result["method"] == "fallback"
    assert result["fallback_template"] == "mg_flow_arrows"
    # LLM 语义值叠加：text 是总标题、stepN 是步骤名（位置规则会错位）
    params = captured["params"]
    assert params["text"] == "产品开发流程"
    assert params["step1"] == "需求分析"
    assert params["step4"] == "上线"
    assert params["accent"] == "#fbbf24"
    # 与纯位置规则结果不同 → 接线确实走了语义填充
    positional = _positional(_real_flow_template(), FLOW_CONTENT)
    assert params["text"] != positional["text"]
    assert params["step4"] != positional["step4"]


async def test_fallback_generate_without_api_key_keeps_positional_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产接线回归：无 API key → _fallback_generate 与位置规则逐字段一致。"""
    monkeypatch.setattr(settings, "llm_api_key", "")
    fake = FakeLLM(result=SEMANTIC_FLOW)
    gen = _make_generator(monkeypatch, fake)
    captured: dict[str, Any] = {}
    _capture_fallback_params(monkeypatch, gen, captured)

    result = await gen._fallback_generate("讲解产品开发流程步骤", FLOW_CONTENT, {})

    assert fake.calls == [], "无 API key 时不得调用 LLM"
    assert result["success"] is True
    expected = _positional(_real_flow_template(), FLOW_CONTENT)
    assert captured["params"] == expected
