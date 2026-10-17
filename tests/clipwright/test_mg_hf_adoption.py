"""HFR 采纳项测试：Persona 字体令牌 + 生成期 lint 门禁。

对应审计 HFR-3（字体恒 sans-serif）与 HFR-4（无静态门禁）：
- ``MGGenerator._apply_persona_font`` 把 Persona 字体落进 MG 的 style.font_family，
  且只接受纯字体族名（拒绝路径/URL/注入），LLM/模板显式指定字体时不覆盖。
- ``MGGenerator._lint_gated`` 在生成期调用 ``hyperframes lint``：
  error 级 → 返回 ""（触发降级链）；warning 级或 CLI 不可用 → 原样放行。
"""

from __future__ import annotations

import json

import pytest

from clipwright.animation.mg.generator import MGGenerator


def _base_mg_def(font_family: str | None = "sans-serif") -> dict:
    style: dict = {"background": "transparent"}
    if font_family is not None:
        style["font_family"] = font_family
    return {"animation_id": "mg_t", "duration_sec": 3.0, "elements": [], "style": style}


class TestApplyPersonaFont:
    def test_overrides_default_sans_serif(self) -> None:
        out = MGGenerator._apply_persona_font(
            _base_mg_def(), {"font": "Source Han Sans SC"}
        )
        assert out["style"]["font_family"] == "Source Han Sans SC"

    def test_accepts_font_stack(self) -> None:
        out = MGGenerator._apply_persona_font(
            _base_mg_def(), {"font": "Inter, PingFang SC"}
        )
        assert out["style"]["font_family"] == "Inter, PingFang SC"

    def test_overrides_missing_family(self) -> None:
        out = MGGenerator._apply_persona_font(_base_mg_def(None), {"font": "Noto Sans"})
        assert out["style"]["font_family"] == "Noto Sans"

    @pytest.mark.parametrize(
        "font",
        [
            'x"; url(evil.js)',
            "../../etc/passwd",
            "url(http://evil/x.woff2)",
            "C:\\Windows\\Fonts\\msyh.ttc",
            "",
            "   ",
        ],
    )
    def test_rejects_unsafe_or_empty(self, font: str) -> None:
        out = MGGenerator._apply_persona_font(_base_mg_def(), {"font": font})
        assert out["style"]["font_family"] == "sans-serif"

    def test_preserves_explicit_font(self) -> None:
        out = MGGenerator._apply_persona_font(
            _base_mg_def("Fira Code"), {"font": "Inter"}
        )
        assert out["style"]["font_family"] == "Fira Code"

    def test_no_persona_font_is_noop(self) -> None:
        defn = _base_mg_def()
        out = MGGenerator._apply_persona_font(defn, {})
        assert out["style"]["font_family"] == "sans-serif"
        # 不修改入参（避免影响调用方已缓存的 mg_def）
        assert defn["style"]["font_family"] == "sans-serif"


class TestLintGate:
    @pytest.fixture()
    def gen(self) -> MGGenerator:
        return MGGenerator()

    @pytest.fixture(autouse=True)
    def _cli_available(self, monkeypatch) -> None:
        """测试环境没有 hyperframes CLI → 强制探针返回 True 才能走到 lint 调用。"""
        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.is_available",
            staticmethod(lambda: True),
        )

    def test_error_blocks(self, gen: MGGenerator, monkeypatch) -> None:
        async def _fake_lint(_html, **_kw):
            return False, ["[error] missing_timeline_registry: Missing registration."]

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.lint_html",
            _fake_lint,
        )
        import asyncio

        assert asyncio.run(gen._lint_gated("<html></html>")) == ""

    def test_cli_unavailable_short_circuits(self, gen: MGGenerator, monkeypatch) -> None:
        """CLI 不在时不做无谓的 npx 调用（离线/测试环境的默认路径）。"""
        import asyncio

        calls: list[int] = []

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.is_available",
            staticmethod(lambda: False),
        )

        async def _fake_lint(_html, **_kw):
            calls.append(1)
            return False, ["[error] x"]

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.lint_html",
            _fake_lint,
        )
        assert asyncio.run(gen._lint_gated("<html>x</html>")) == "<html>x</html>"
        assert calls == []

    def test_env_escape_hatch(self, gen: MGGenerator, monkeypatch) -> None:
        import asyncio

        monkeypatch.setenv("CLIPWRIGHT_HF_LINT_GATE", "0")
        calls: list[int] = []

        async def _fake_lint(_html, **_kw):
            calls.append(1)
            return False, ["[error] x"]

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.lint_html",
            _fake_lint,
        )
        assert asyncio.run(gen._lint_gated("<html>x</html>")) == "<html>x</html>"
        assert calls == []

    def test_warning_passes(self, gen: MGGenerator, monkeypatch) -> None:
        async def _fake_lint(_html, **_kw):
            return True, ["[warning] timeline_track_too_dense: too dense"]

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.lint_html",
            _fake_lint,
        )
        import asyncio

        assert asyncio.run(gen._lint_gated("<html>x</html>")) == "<html>x</html>"

    def test_gate_can_be_disabled(self, gen: MGGenerator, monkeypatch) -> None:
        called: list[int] = []

        async def _fake_lint(_html, **_kw):
            called.append(1)
            return False, ["[error] boom"]

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.lint_html",
            _fake_lint,
        )
        monkeypatch.setitem(gen._config["generation"], "lint_gate", False)
        import asyncio

        assert asyncio.run(gen._lint_gated("<html>x</html>")) == "<html>x</html>"
        assert called == []

    def test_lint_exception_passes(self, gen: MGGenerator, monkeypatch) -> None:
        async def _boom(_html, **_kw):
            raise RuntimeError("cli missing")

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.lint_html",
            _boom,
        )
        import asyncio

        assert asyncio.run(gen._lint_gated("<html>x</html>")) == "<html>x</html>"

    def test_lint_json_parsing(self) -> None:
        """lint_html 对上游 JSON 的解析（error/warning 分级）。"""
        import asyncio

        from clipwright.animation.hyperframes_renderer import HyperframesRenderer

        class _R:
            returncode = 1

            def __init__(self, payload: str) -> None:
                self.stdout = payload.encode("utf-8")

        async def _run(payload: str):
            async def _cmd(_c):
                return _R(payload)
            return await HyperframesRenderer.lint_html("<html></html>", run_cmd=_cmd)

        ok, findings = asyncio.run(_run(json.dumps({
            "ok": False,
            "findings": [{"code": "x", "severity": "error", "message": "m"}],
        })))
        assert ok is False and findings[0].startswith("[error]")

        ok2, _ = asyncio.run(_run(json.dumps({"ok": True, "findings": []})))
        assert ok2 is True

    def test_check_html_parsing(self) -> None:
        """check 门禁：按 stage 汇总 findings，error 级 → ok=False。"""
        import asyncio

        from clipwright.animation.hyperframes_renderer import HyperframesRenderer

        payload = {
            "ok": False,
            "lint": {"findings": [{"code": "w1", "severity": "warning", "message": "mw"}]},
            "runtime": {"findings": []},
            "layout": {"findings": [{"code": "l1", "severity": "error", "message": "ml"}]},
            "motion": {"findings": []},
            "contrast": {"findings": [
                {"code": "c1", "severity": "error", "message": "#mg-e5 1.6:1 (need 3:1)"}
            ]},
        }

        class _R:
            returncode = 1
            stdout = json.dumps(payload).encode("utf-8")

        async def _run(_cmd):
            return _R()

        ok, findings = asyncio.run(
            HyperframesRenderer.check_html("<html></html>", run_cmd=_run)
        )
        assert ok is False
        assert any("layout/error" in f for f in findings)
        assert any("contrast/error" in f for f in findings)
        assert any("lint/warning" in f for f in findings)


class TestCheckGate:
    @pytest.fixture()
    def gen(self) -> MGGenerator:
        return MGGenerator()

    def _patch(self, monkeypatch, ok: bool, findings: list[str]) -> None:
        async def _fake_check(_html, **_kw):
            return ok, findings

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.check_html",
            _fake_check,
        )

    def test_off_by_default(self, gen: MGGenerator, monkeypatch) -> None:
        called: list[int] = []

        async def _fake_check(_html, **_kw):
            called.append(1)
            return False, ["[contrast/error] c: bad"]

        monkeypatch.setattr(
            "clipwright.animation.hyperframes_renderer.HyperframesRenderer.check_html",
            _fake_check,
        )
        import asyncio

        gen._config["generation"]["check_gate"] = "off"
        assert asyncio.run(gen._check_gated("<html>x</html>")) == "<html>x</html>"
        assert called == []

    def test_warn_mode_keeps_html(self, gen: MGGenerator, monkeypatch) -> None:
        import asyncio

        gen._config["generation"]["check_gate"] = "warn"
        self._patch(monkeypatch, False, ["[contrast/error] c: 1.6:1"])
        assert asyncio.run(gen._check_gated("<html>x</html>")) == "<html>x</html>"

    def test_strict_mode_rejects_on_error(self, gen: MGGenerator, monkeypatch) -> None:
        import asyncio

        gen._config["generation"]["check_gate"] = "strict"
        self._patch(monkeypatch, False, ["[contrast/error] c: 1.6:1"])
        assert asyncio.run(gen._check_gated("<html>x</html>")) == ""

    def test_strict_mode_passes_when_ok(self, gen: MGGenerator, monkeypatch) -> None:
        import asyncio

        gen._config["generation"]["check_gate"] = "strict"
        self._patch(monkeypatch, True, [])
        assert asyncio.run(gen._check_gated("<html>x</html>")) == "<html>x</html>"


class TestVisualEvaluator:
    def test_default_sample_times(self) -> None:
        from clipwright.animation.mg.visual_eval import _default_sample_times

        assert _default_sample_times(4.0) == [1.0, 2.0, 3.7]
        # 极短镜头不产生负值，且首点 > 0
        short = _default_sample_times(0.6)
        assert all(t >= 0 for t in short) and short[0] > 0

    def test_parse_json_tolerates_fences_and_prose(self) -> None:
        from clipwright.animation.mg.visual_eval import MGVisualEvaluator

        assert MGVisualEvaluator._parse_json('```json\n{"score": 80}\n```') == {"score": 80}
        assert MGVisualEvaluator._parse_json('结论如下 {"score": 70} 完毕') == {"score": 70}
        assert MGVisualEvaluator._parse_json("这不是 JSON") is None

    def test_normalize_clamps_and_coerces(self) -> None:
        from clipwright.animation.mg.visual_eval import MGVisualEvaluator

        out = MGVisualEvaluator._normalize(
            {"score": "120", "issues": ["压字", {"severity": "high", "detail": "越界"}],
             "suggestions": ["缩小字号", 3]},
            ["/tmp/a.png"],
        )
        assert out["score"] == 100
        assert len(out["issues"]) == 2 and out["issues"][1]["severity"] == "high"
        assert out["suggestions"] == ["缩小字号", "3"]

    def test_evaluate_requires_visual_llm_flag(self, monkeypatch) -> None:
        import asyncio

        from clipwright.animation.mg.visual_eval import MGVisualEvaluator
        from clipwright.config import settings

        monkeypatch.setattr(settings, "enable_visual_llm", False, raising=False)
        assert asyncio.run(MGVisualEvaluator().evaluate(["/tmp/x.png"])) is None

    def test_evaluate_html_returns_none_without_frames(self, monkeypatch) -> None:
        import asyncio

        from clipwright.animation.mg.visual_eval import MGVisualEvaluator

        async def _no_frames(_html, **_kw):
            return []

        ev = MGVisualEvaluator()
        monkeypatch.setattr(ev, "snapshot_html", _no_frames)
        assert asyncio.run(ev.evaluate_html("<html></html>")) is None

    def test_visual_stage_disabled_returns_none(self) -> None:
        import asyncio

        gen = MGGenerator()
        gen._config["critique"]["visual_eval"] = {"enabled": False}
        assert asyncio.run(gen._visual_eval_stage({"elements": []}, "d", "t", {})) is None


class TestVisualRepairLoop:
    """视觉自评接进批判闭环：低分 → 视觉修复一次；跳过/高分 → 正常出口。"""

    _MG = {
        "animation_id": "mg_v",
        "duration_sec": 3.0,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "params": {"text": {"type": "string", "default": "T"}},
        "elements": [
            {"type": "text", "content": "{text}", "x": "center", "y": "center",
             "keyframes": [{"time": 0.0, "properties": {"opacity": 0}},
                           {"time": 3.0, "properties": {"opacity": 1}}]},
        ],
    }

    def _prep(self, monkeypatch, gen: MGGenerator, visual: dict | None) -> list[str]:
        """搭好批判闭环的桩：批判高分 + 视觉结果可控 + 修复成功。"""
        gen._config["critique"]["visual_eval"] = {"enabled": True, "min_score": 60}

        async def _critique(_mg_def, *_a, **_kw):
            return {"score": 90, "issues": [], "suggestions": []}

        async def _evaluate_html(_self, *_a, **_kw):
            return visual

        async def _repair(_mg_def, _critique, *_a, **_kw):
            return dict(_mg_def)

        monkeypatch.setattr(gen, "_critique_quality", _critique)
        monkeypatch.setattr(
            "clipwright.animation.mg.visual_eval.MGVisualEvaluator.evaluate_html",
            _evaluate_html,
        )
        monkeypatch.setattr(gen, "_call_llm_critique_repair", _repair)
        return []

    def _run(self, gen: MGGenerator) -> dict:
        import asyncio

        return asyncio.run(gen._finalize_with_critique(
            dict(self._MG), "llm", "描述", "T", {}, {}, {}, "vp",
        )) or {}

    def test_low_visual_score_triggers_visual_repair(self, monkeypatch) -> None:
        gen = MGGenerator()
        self._prep(monkeypatch, gen, {
            "score": 30,
            "issues": [{"severity": "high", "detail": "文字被压住"}],
            "suggestions": ["缩小标题字号"],
        })
        out = self._run(gen)
        assert out.get("success") is True
        assert out.get("method") == "visual_repair"

    def test_high_visual_score_keeps_original(self, monkeypatch) -> None:
        gen = MGGenerator()
        self._prep(monkeypatch, gen, {"score": 88, "issues": [], "suggestions": []})
        out = self._run(gen)
        assert out.get("success") is True
        assert out.get("method") == "llm"

    def test_visual_eval_skipped_keeps_original(self, monkeypatch) -> None:
        gen = MGGenerator()
        self._prep(monkeypatch, gen, None)  # 未配置视觉模型 / CLI 不可用
        out = self._run(gen)
        assert out.get("success") is True
        assert out.get("method") == "llm"
