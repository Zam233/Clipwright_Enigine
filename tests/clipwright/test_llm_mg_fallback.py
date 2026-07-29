"""llm_mg Fallback 单元测试。"""

from __future__ import annotations

from plugins.llm_mg.fallback import FallbackEngine


TEMPLATES = [
    {
        "animation_id": "mg_comparison_split",
        "name": "左右对比",
        "description": "A vs B 左右分屏对比",
        "duration_sec": 3.5,
        "params": {"text": {"type": "string", "default": ""}},
    },
    {
        "animation_id": "mg_title_reveal",
        "name": "标题揭示",
        "description": "大标题从中心放大淡入",
        "duration_sec": 3.0,
        "params": {"text": {"type": "string"}, "accent": {"type": "string", "default": "#4f8cff"}},
    },
    {
        "animation_id": "mg_progress_bar",
        "name": "进度条",
        "description": "百分比进度条动画",
        "duration_sec": 2.5,
        "params": {"text": {"type": "string"}, "value": {"type": "string"}, "unit": {"type": "string", "default": "%"}},
    },
    {
        "animation_id": "mg_counter_up",
        "name": "数字滚动",
        "description": "数字递增计数动画",
        "duration_sec": 2.0,
        "params": {"text": {"type": "string"}, "value": {"type": "string"}},
    },
    {
        "animation_id": "mg_callout_badge",
        "name": "标签徽章",
        "description": "关键信息标注徽章",
        "duration_sec": 2.0,
        "params": {"text": {"type": "string"}, "subtitle": {"type": "string"}},
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
        """'进度' 关键词匹配 progress_bar。"""
        result = FallbackEngine.find_best_template("展示项目完成进度的动画", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_progress_bar"

    def test_counter_keyword(self) -> None:
        """'数字增长' 匹配 counter_up。"""
        result = FallbackEngine.find_best_template("用户数量增长统计数字", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_counter_up"

    def test_callout_keyword(self) -> None:
        """'标注' 匹配 callout_badge。"""
        result = FallbackEngine.find_best_template("关键信息标注提示", TEMPLATES)
        assert result is not None
        assert result["animation_id"] == "mg_callout_badge"

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
