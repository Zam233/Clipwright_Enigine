"""llm_mg Fallback 单元测试。"""

from __future__ import annotations

import pytest

from clipwright.animation.mg.fallback import FallbackEngine
from clipwright.animation.mg.generator import MGGenerator


# 与 clipwright/animation/mg/templates/ 下 8 个真实模板库对齐
TEMPLATES = [
    {
        "animation_id": "mg_comparison_split",
        "name": "左右对比",
        "description": "A vs B 左右分屏对比",
        "duration_sec": 4.5,
        "params": {"left": {"type": "string"}, "right": {"type": "string"}},
    },
    {
        "animation_id": "mg_title_reveal",
        "name": "标题揭示",
        "description": "大标题从中心放大淡入",
        "duration_sec": 4.0,
        "params": {"text": {"type": "string"}, "accent": {"type": "string", "default": "#4f8cff"}},
    },
    {
        "animation_id": "mg_timeline_progress",
        "name": "时间线进度",
        "description": "时间线/进度百分比动画",
        "duration_sec": 4.5,
        "params": {"text": {"type": "string"}, "value": {"type": "string"}, "unit": {"type": "string", "default": "%"}},
    },
    {
        "animation_id": "mg_data_bars",
        "name": "柱状数据",
        "description": "柱状数据图表",
        "duration_sec": 4.5,
        "params": {"text": {"type": "string"}, "value1": {"type": "string"}},
    },
    {
        "animation_id": "mg_counter_up",
        "name": "数字滚动",
        "description": "数字递增计数动画",
        "duration_sec": 4.0,
        "params": {"text": {"type": "string"}, "value": {"type": "string"}},
    },
    {
        "animation_id": "mg_flow_arrows",
        "name": "流程箭头",
        "description": "流程步骤箭头动画",
        "duration_sec": 4.2,
        "params": {"text": {"type": "string"}, "step1": {"type": "string"}},
    },
    {
        "animation_id": "mg_quote_card",
        "name": "金句卡",
        "description": "金句强调卡片",
        "duration_sec": 4.5,
        "params": {"text": {"type": "string"}, "author": {"type": "string"}},
    },
    {
        "animation_id": "mg_mindmap",
        "name": "思维导图",
        "description": "思维导图结构",
        "duration_sec": 4.2,
        "params": {"text": {"type": "string"}, "node1": {"type": "string"}},
    },
]


class TestFindBestTemplate:
    """FallbackEngine.find_best_template 测试。"""

    def test_comparison_keyword(self) -> None:
        """'对比' 关键词匹配 comparison 模板。"""
        result = FallbackEngine.find_best_template("产品A和B的性能对比分析", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_comparison_split"

    def test_vs_keyword(self) -> None:
        """'vs' 关键词匹配。"""
        result = FallbackEngine.find_best_template("骁龙 vs 天玑", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_comparison_split"

    def test_title_keyword(self) -> None:
        """'标题' 关键词匹配 title_reveal。"""
        result = FallbackEngine.find_best_template("需要一个科技感的大标题开头", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_title_reveal"

    def test_progress_keyword(self) -> None:
        """'进度' 关键词匹配 timeline_progress。"""
        result = FallbackEngine.find_best_template("展示项目完成进度的动画", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_timeline_progress"

    def test_counter_keyword(self) -> None:
        """'数字增长' 匹配 counter_up。"""
        result = FallbackEngine.find_best_template("用户数量增长统计数字", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_counter_up"

    def test_data_keyword(self) -> None:
        """'数据' 关键词匹配 data_bars。"""
        result = FallbackEngine.find_best_template("展示各季度销售数据图表", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_data_bars"

    def test_flow_keyword(self) -> None:
        """'流程' 关键词匹配 flow_arrows。"""
        result = FallbackEngine.find_best_template("讲解产品开发流程步骤", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_flow_arrows"

    def test_quote_keyword(self) -> None:
        """'金句' 关键词匹配 quote_card。"""
        result = FallbackEngine.find_best_template("展示一句名人金句格言", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_quote_card"

    def test_mindmap_keyword(self) -> None:
        """'思维导图' 关键词匹配 mindmap。"""
        result = FallbackEngine.find_best_template("画一个知识体系思维导图", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_mindmap"

    def test_callout_keyword(self) -> None:
        """'标注' 匹配 quote_card（原 callout_badge 已由 quote_card 取代）。"""
        result = FallbackEngine.find_best_template("关键信息标注提示", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_quote_card"

    def test_no_match_returns_comparison(self) -> None:
        """无关键词匹配时返回最通用的 comparison_split。"""
        result = FallbackEngine.find_best_template("一个复杂的自定义动画需求", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_comparison_split"

    def test_empty_templates(self) -> None:
        """空模板列表返回 None。"""
        result = FallbackEngine.find_best_template("anything", [])
        assert result is None

    def test_empty_description(self) -> None:
        """空描述仍返回 comparison_split。"""
        result = FallbackEngine.find_best_template("", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_comparison_split"


class TestExtractKeywords:
    """FallbackEngine.extract_keywords 测试。"""

    def test_pipe_separated(self) -> None:
        """| 分隔的多段内容。"""
        parts = FallbackEngine.extract_keywords("骁龙8Gen3|天玑9300|骁龙胜出")
        assert len(parts) == 3
        assert parts[0] == "骁龙8Gen3"
        assert parts[2] == "骁龙胜出"

    def test_arrow_separated(self) -> None:
        """→ 分隔兼容。"""
        parts = FallbackEngine.extract_keywords("A → B → C")
        assert len(parts) == 3
        assert parts == ["A", "B", "C"]

    def test_single_value(self) -> None:
        """单个值。"""
        parts = FallbackEngine.extract_keywords("only_one")
        assert len(parts) == 1
        assert parts[0] == "only_one"

    def test_empty_string(self) -> None:
        """空字符串返回空列表。"""
        parts = FallbackEngine.extract_keywords("")
        assert parts == []

    def test_whitespace_trimmed(self) -> None:
        """前后空格被清理。"""
        parts = FallbackEngine.extract_keywords("  A  |  B  ")
        assert parts == ["A", "B"]


class TestFillTemplateParams:
    """FallbackEngine.fill_template_params 测试。"""

    def test_fill_all_params(self) -> None:
        """填充全部参数。"""
        template = {
            "params": {
                "text": {"type": "string", "default": ""},
                "value": {"type": "string", "default": ""},
                "unit": {"type": "string", "default": "%"},
            },
        }
        _, params = FallbackEngine.fill_template_params(
            template, "完成率|85|%",
        )
        assert params["text"] == "完成率"
        assert params["value"] == "85"
        assert params["unit"] == "%"

    def test_fill_partial_params(self) -> None:
        """部分参数使用默认值。"""
        template = {
            "params": {
                "text": {"type": "string", "default": "默认文本"},
                "accent": {"type": "string", "default": "#4f8cff"},
            },
        }
        _, params = FallbackEngine.fill_template_params(template, "only_text")
        assert params["text"] == "only_text"
        assert params["accent"] == "#4f8cff"  # 默认值

    def test_single_value_fallback(self) -> None:
        """只有一个值且无 params 定义时填到 text。"""
        template = {"params": {}}
        _, params = FallbackEngine.fill_template_params(template, "hello")
        assert params["text"] == "hello"

    def test_persona_accent_override(self) -> None:
        """Persona 风格覆盖 accent 颜色。"""
        template = {
            "params": {
                "text": {"type": "string", "default": ""},
                "accent": {"type": "string", "default": "#4f8cff"},
            },
        }
        _, params = FallbackEngine.fill_template_params(
            template, "标题",
            persona_style={"primary_color": "#ff0000"},
        )
        assert params["accent"] == "#ff0000"


# ── MGGenerator 生成方法记录 / 失败原因 trace 记录（Todo 6 / C3）───────────────
# 覆盖：
# - LLM 抛异常 → 降级命中模板 → method=fallback 且 fallback_template 非空
# - 经 services.trace.add_event 推送 method 追踪事件（agent="mg", event_type="method"）
# - LLM 成功路径 → method=llm
# - LLM 与模板均失败 → success False 且日志含明确原因
# - malformed_input: LLM 返回非法 JSON → 校验错误被记录，经修复重试恢复


def _valid_mg_def() -> dict:
    """最小合法 MG JSON。"""
    return {
        "animation_id": "mg_test",
        "duration_sec": 3.0,
        "width": 1920,
        "height": 1080,
        "elements": [
            {
                "type": "text",
                "content": "Hello",
                "keyframes": [
                    {"time": 0, "opacity": 0},
                    {"time": 1.0, "opacity": 1},
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_llm_raises_falls_back_to_template(monkeypatch) -> None:
    """LLM 调用抛异常 → 降级命中模板 → success 且 method=fallback / fallback_template 非空。"""
    gen = MGGenerator()

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM service down")

    monkeypatch.setattr(gen, "_call_llm", _boom)

    result = await gen.generate(
        description="一个视频标题展示",
        text_content="ClipWright",
        persona_style={},
    )

    assert result["success"] is True
    assert result["method"] == "fallback"
    assert result["fallback_template"]


@pytest.mark.asyncio
async def test_trace_event_emits_method(monkeypatch) -> None:
    """monkeypatch services.trace.add_event → 捕获到含 method 字段的事件（fallback）。"""
    import clipwright.services.trace as trace

    captured: list[tuple] = []

    def _fake_add_event(pipeline_id, agent, event_type, summary, detail=None):
        captured.append((pipeline_id, agent, event_type, summary, detail))

    monkeypatch.setattr(trace, "add_event", _fake_add_event)

    gen = MGGenerator()

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM service down")

    monkeypatch.setattr(gen, "_call_llm", _boom)

    result = await gen.generate(
        description="一个视频标题展示",
        text_content="ClipWright",
        persona_style={},
        pipeline_id="proj_demo_test",
    )

    assert result["success"] is True
    method_events = [
        ev for ev in captured if ev[1] == "mg" and ev[2] == "method"
    ]
    assert method_events, f"no method trace event captured: {captured}"
    detail = method_events[-1][4]
    assert detail["method"] == "fallback"
    assert detail["fallback_template"]


@pytest.mark.asyncio
async def test_llm_success_method_llm(monkeypatch) -> None:
    """LLM 成功路径 → method=llm。"""
    gen = MGGenerator()

    async def _ok(*args, **kwargs):
        return _valid_mg_def()

    monkeypatch.setattr(gen, "_call_llm", _ok)
    # 合并：批判闭环（本地线特性）需要 LLM——测试中打桩为不可用 → 静默跳过
    monkeypatch.setattr(gen, "_critique_quality", _ok)
    async def _no_repair(*args, **kwargs):
        return None

    monkeypatch.setattr(gen, "_call_llm_critique_repair", _no_repair)

    result = await gen.generate(
        description="一段成功生成的动画",
        text_content="Hello",
        persona_style={},
    )

    assert result["method"] == "llm"
    assert result["mg_def"] is not None


@pytest.mark.asyncio
async def test_all_fail_logs_clear_reason(monkeypatch, caplog) -> None:
    """LLM 与模板均失败 → success False 且日志含明确原因。"""
    gen = MGGenerator()

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM service down")

    monkeypatch.setattr(gen, "_call_llm", _boom)
    monkeypatch.setattr(gen, "_get_templates", lambda: [])

    import logging
    with caplog.at_level(logging.WARNING, logger="clipwright"):
        result = await gen.generate(
            description="无模板可用的动画",
            text_content="x",
            persona_style={},
        )

    assert result["success"] is False
    assert result["method"] == "fallback"
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "no fallback template available" in joined


@pytest.mark.asyncio
async def test_malformed_input_records_errors_then_repair_retry(monkeypatch) -> None:
    """LLM 返回非法 JSON → 校验错误被记录，经修复重试恢复为 llm_repair。"""
    import clipwright.services.trace as trace

    captured: list[tuple] = []
    monkeypatch.setattr(
        trace, "add_event",
        lambda *a, **kw: captured.append((a[0], a[1], a[2], a[3], a[4])),
    )

    gen = MGGenerator()

    async def _invalid(*args, **kwargs):
        # text 元素缺少 content → repair 无法修复 → 走带错误回传的修复重试
        return {
            "animation_id": "mg_bad",
            "duration_sec": 3.0,
            "width": 1920,
            "height": 1080,
            "elements": [{"type": "text", "keyframes": [{"time": 0, "opacity": 0}]}],
        }

    async def _repair_ok(*args, **kwargs):
        return _valid_mg_def()

    monkeypatch.setattr(gen, "_call_llm", _invalid)
    monkeypatch.setattr(gen, "_call_llm_repair", _repair_ok)

    result = await gen.generate(
        description="一个需要修复的动画",
        text_content="Hello",
        persona_style={},
        pipeline_id="proj_malformed",
    )

    assert result["method"] == "llm_repair"
    validation_events = [
        ev for ev in captured if ev[1] == "mg" and ev[2] == "validation_error"
    ]
    assert validation_events, f"no validation_error event captured: {captured}"
    assert any("content" in e for e in validation_events[0][4]["errors"])
    method_events = [ev for ev in captured if ev[1] == "mg" and ev[2] == "method"]
    assert method_events[-1][4]["method"] == "llm_repair"
