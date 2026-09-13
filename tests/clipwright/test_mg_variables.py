"""P1-5 变量参数化（上游 `data-composition-variables` / `--variables`）测试。

覆盖：
- 声明数组的形状与类型推断（string / number / color）；
- 文本绑定 `data-var-text`（保留真实文本作兜底，绝不把 `{key}` 留到画面上）；
- 标量 CSS 绑定 `var(--key, 兜底值)`（未传变量时兜底生效，行为不退化）；
- 图片 `data-var-src` 与兜底 src 同时存在（上游 lint `media_variable_src_no_fallback`）；
- 渲染命令在传入 variables 时追加 `--variables`，`CLIPWRIGHT_HF_STRICT_VARIABLES=1` 时追加 `--strict-variables`；
- 预置模板全部零残留占位符。
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from clipwright.animation.hyperframes_renderer import HyperframesRenderer
from clipwright.animation.mg_renderer import MGRenderer


def _solid_def() -> dict:
    """一个同时用到 {text}（文本绑定）、{accent}（颜色绑定）、{value}（数字）的 MG。"""
    return {
        "animation_id": "var_case",
        "duration_sec": 2.0,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "params": {
            "text": {"type": "string", "default": "默认标题"},
            "accent": {"type": "string", "default": "#fbbf24"},
            "value": {"type": "string", "default": "42"},
        },
        "elements": [
            {
                "type": "text", "content": "{text}", "x": "center", "y": "center",
                "font_size": 48, "font_color": "{accent}",
                "keyframes": [
                    {"time": 0.0, "opacity": 0, "color": "{accent}"},
                    {"time": 2.0, "opacity": 1},
                ],
            },
            {
                "type": "text", "content": "总量 {value}", "x": "center", "y": "bottom",
                "font_size": 24, "font_color": "#ffffff",
                "keyframes": [{"time": 0.0, "opacity": 0}, {"time": 2.0, "opacity": 1}],
            },
        ],
    }


class TestVariableDeclarations:
    def test_types_inferred(self) -> None:
        variables = MGRenderer.build_variables(_solid_def(), {"text": "T", "accent": "#ff0000", "value": "7"})
        by_id = {v["id"]: v for v in variables}
        assert by_id["text"]["type"] == "string"
        assert by_id["accent"]["type"] == "color"
        assert by_id["value"]["type"] == "number"
        for v in variables:
            assert set(v) == {"id", "type", "label", "default"}

    def test_defaults_prefer_runtime_params(self) -> None:
        variables = MGRenderer.build_variables(_solid_def(), {"text": "运行时"})
        by_id = {v["id"]: v for v in variables}
        assert by_id["text"]["default"] == "运行时"
        # 未传的键回退模板 default
        assert by_id["accent"]["default"] == "#fbbf24"

    def test_attribute_embedded_and_json_safe(self) -> None:
        html = MGRenderer.render(_solid_def(), {"text": '含"引号"的标题', "accent": "#fff", "value": "1"})
        m = re.search(r'data-composition-variables="([^"]*)"', html)
        assert m, "缺少 data-composition-variables 属性"
        payload = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))
        assert {v["id"] for v in payload} == {"text", "accent", "value"}


class TestBindings:
    def test_text_binding_with_fallback(self) -> None:
        html = MGRenderer.render(_solid_def(), {"text": "绑定标题", "accent": "#fbbf24", "value": "9"})
        assert 'data-var-text="text"' in html
        # 兜底文本是解析后的真实文案（不传 --variables 也能正确预览）
        assert "绑定标题" in html
        # 画面里绝不能出现字面占位符
        assert "{text}" not in html

    def test_color_binding_uses_var_with_fallback(self) -> None:
        html = MGRenderer.render(_solid_def(), {"text": "T", "accent": "#123456", "value": "1"})
        assert "var(--accent, #123456)" in html

    def test_partial_placeholder_stays_inline(self) -> None:
        html = MGRenderer.render(_solid_def(), {"text": "T", "accent": "#123456", "value": "11"})
        # 混合文案无法绑定（只能内联替换），但必须已被替换
        assert "总量 11" in html
        assert "{value}" not in html

    def test_empty_value_does_not_leak_placeholder(self) -> None:
        """值缺失时兜底文本为空串——绝不能让 `{text}` 出现在画面上。"""
        mg = _solid_def()
        html = MGRenderer.render(mg, {})
        assert "{text}" not in html
        assert 'data-var-text="text"' in html

    def test_image_var_src_keeps_fallback(self) -> None:
        """图片 src 整体是占位符 → 绑定 `data-var-src`，同时保留兜底 src。

        上游 lint 的 `media_variable_src_no_fallback` 要求兜底必须存在，
        否则渲染时读不到素材。这里用 data: URI 作兜底值（走白名单允许路径）。
        """
        data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
        mg = {
            "animation_id": "img_case", "duration_sec": 1.0,
            "style": {"background": "transparent", "font_family": "sans-serif"},
            "params": {"hero": {"type": "string", "default": data_uri}},
            "elements": [{
                "type": "image", "src": "{hero}", "x": 100, "y": 100,
                "width": 100, "height": 100,
                "keyframes": [{"time": 0.0, "opacity": 1}, {"time": 1.0, "opacity": 1}],
            }],
        }
        html = MGRenderer.render(mg, {"hero": data_uri})
        assert 'data-var-src="hero"' in html
        assert f'src="{data_uri}"' in html  # 兜底 src 必须存在


class TestAllTemplatesClean:
    def test_no_residual_placeholders(self) -> None:
        leftovers: dict[str, list[str]] = {}
        for a in MGRenderer.list_animations():
            mg = MGRenderer.load_animation(a["id"])
            if not mg:
                continue
            params = {
                k: (v.get("default", "") if isinstance(v, dict) else "")
                for k, v in (mg.get("params") or {}).items()
            }
            html = MGRenderer.render(mg, params, width=1280, height=720, fps=30)
            hits = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", html)
            if hits:
                leftovers[a["id"]] = sorted(set(hits))
        assert leftovers == {}, f"模板渲染后仍有未填充占位符: {leftovers}"


class TestRenderCommandVariables:
    def _cmd_for(self, variables, monkeypatch, tmp_path) -> list[str]:
        captured: list[list[str]] = []

        class _R:
            returncode = 0
            stdout = b""
            stderr = b""

        async def _run(cmd, **_kw):
            captured.append([str(c) for c in cmd])
            return _R()

        # out_dir 必须用临时目录：传 "." 会在仓库根留下 mg_<hex>/index.html 垃圾目录
        asyncio.run(HyperframesRenderer.render_mg_html_to_mov(
            "<html></html>", str(tmp_path), str(tmp_path / "out.mov"), fps=30, run_cmd=_run,
            validate=lambda _p: True, variables=variables,
        ))
        assert captured, "未执行渲染命令"
        return captured[0]

    def test_variables_flag_added(self, monkeypatch, tmp_path) -> None:
        cmd = self._cmd_for({"text": "标题", "accent": "#fff"}, monkeypatch, tmp_path)
        assert "--variables" in cmd
        payload = json.loads(cmd[cmd.index("--variables") + 1])
        assert payload == {"text": "标题", "accent": "#fff"}

    def test_no_variables_flag_when_empty(self, monkeypatch, tmp_path) -> None:
        cmd = self._cmd_for(None, monkeypatch, tmp_path)
        assert "--variables" not in cmd

    def test_strict_flag_opt_in(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("CLIPWRIGHT_HF_STRICT_VARIABLES", "1")
        cmd = self._cmd_for({"text": "T"}, monkeypatch, tmp_path)
        assert "--strict-variables" in cmd
        monkeypatch.delenv("CLIPWRIGHT_HF_STRICT_VARIABLES")
        cmd2 = self._cmd_for({"text": "T"}, monkeypatch, tmp_path)
        assert "--strict-variables" not in cmd2

    def test_none_values_become_empty_string(self, monkeypatch, tmp_path) -> None:
        cmd = self._cmd_for({"text": None, "accent": "#fff"}, monkeypatch, tmp_path)
        payload = json.loads(cmd[cmd.index("--variables") + 1])
        assert payload["text"] == ""
