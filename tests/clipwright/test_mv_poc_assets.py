"""MV POC 资产单测（R-04 / C2）：只验资产与合成正确，不做真机渲染。

真机渲染与三道闸在 ``scripts/render_mv_poc.py`` 里跑（门 1 证据见
``docs/spike-results-2026-10-18.md``）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from clipwright.animation.scene_renderer import build_composition_html
from tools.lint_determinism import scan_text

ROOT = Path(__file__).resolve().parents[2]
CHAT_DIR = ROOT / "examples" / "mv_poc" / "chat"


def _load_script() -> dict:
    return json.loads((CHAT_DIR / "chat_script.json").read_text(encoding="utf-8"))


def _render_module():
    spec = importlib.util.spec_from_file_location(
        "render_mv_poc", ROOT / "scripts" / "render_mv_poc.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestPlateShotAssets:
    """R-11…R-13 的集成资产：版画镜头场景 + 歌词行。"""

    PLATE_DIR = ROOT / "examples" / "mv_poc" / "plate_shot"

    def test_scene_is_lint_clean(self) -> None:
        source = (self.PLATE_DIR / "scene.js").read_text(encoding="utf-8")
        assert scan_text(source, "scene.js") == []

    def test_lines_json_schema(self) -> None:
        payload = json.loads((self.PLATE_DIR / "lines.json").read_text(encoding="utf-8"))
        assert payload["duration_sec"] > 0
        assert payload["keyword"]
        times = [line["t0"] for line in payload["lines"]]
        assert times == sorted(times)
        for line in payload["lines"]:
            assert line["t1"] > line["t0"]
            assert line["text"]

    def test_scene_uses_data_uri_assets_only(self) -> None:
        source = (self.PLATE_DIR / "scene.js").read_text(encoding="utf-8")
        assert "window.__plate" in source
        # 不许从网络/相对路径取素材（渲染期禁止网络）
        assert "http://" not in source and "https://" not in source
        assert "fetch(" not in source

    def test_scene_builder_injects_assets(self) -> None:
        module = _load_script_module()
        assets = {
            "ground": "data:image/png;base64,AAA",
            "clean": "data:image/png;base64,BBB",
            "misreg": "data:image/png;base64,CCC",
            "glyph": "data:image/png;base64,DDD",
        }
        lines = json.loads((self.PLATE_DIR / "lines.json").read_text(encoding="utf-8"))
        js = module.build_scene_js(assets, lines)
        assert js.startswith("window.__plate = ")
        assert "data:image/png;base64,AAA" in js
        assert scan_text(js, "plate_shot") == []


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "render_plate_shot", ROOT / "scripts" / "render_plate_shot.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


    def test_lyrics_text_not_embedded_in_lines(self) -> None:
        """V31：场景数据里的 lines 只带时间，歌词文本单独放 texts（按索引取）。"""
        module = _load_script_module()
        lines = json.loads((self.PLATE_DIR / "lines.json").read_text(encoding="utf-8"))
        assets = {k: "data:image/png;base64,AAA" for k in ("ground", "clean", "misreg", "glyph")}
        js = module.build_scene_js(assets, lines, 2.5)
        payload = json.loads(js.split("window.__plate = ", 1)[1].split(";\n", 1)[0])
        for meta in payload["lines"]:
            assert set(meta) == {"i", "t0", "t1"}, meta
        assert payload["texts"]["0"]

    def test_scene_reads_texts_by_index(self) -> None:
        source = (self.PLATE_DIR / "scene.js").read_text(encoding="utf-8")
        assert "data.texts" in source
        assert "line.text" not in source

    def test_cut_time_is_cue_driven(self) -> None:
        """R-13 接入 R-14：腐坏切入时间来自 cues，而不是代码常量。"""
        module = _load_script_module()
        lines = json.loads((self.PLATE_DIR / "lines.json").read_text(encoding="utf-8"))
        _, cut_130 = module.build_cues_for_shot(lines, bpm=130.0, beat_zero_sec=0.344)
        _, cut_90 = module.build_cues_for_shot(lines, bpm=90.0, beat_zero_sec=0.344)
        assert cut_130 != cut_90, "换 BPM 后切入拍点必须变化，否则仍是常量"

    def test_cut_lands_on_grid(self) -> None:
        module = _load_script_module()
        lines = json.loads((self.PLATE_DIR / "lines.json").read_text(encoding="utf-8"))
        items, cut_at = module.build_cues_for_shot(lines, bpm=130.0, beat_zero_sec=0.344)
        assert items, "应生成 cues"
        # 半拍网格：cut_at 应落在 0.344 + k * (60/130/2)
        beat = 60.0 / 130.0
        steps = (cut_at - 0.344) / (beat / 2)
        assert abs(steps - round(steps)) < 1e-6, f"cut_at={cut_at} 不在半拍网格上"

    def test_scene_js_uses_given_cut_time(self) -> None:
        module = _load_script_module()
        lines = json.loads((self.PLATE_DIR / "lines.json").read_text(encoding="utf-8"))
        assets = {k: "data:image/png;base64,AAA" for k in ("ground", "clean", "misreg", "glyph")}
        js = module.build_scene_js(assets, lines, 3.25)
        assert '"cut_at": 3.25' in js


class TestChatAssets:
    def test_scene_js_is_lint_clean(self) -> None:
        source = (CHAT_DIR / "scene.js").read_text(encoding="utf-8")
        assert scan_text(source, "scene.js") == []

    def test_scene_exports_render_frame_contract(self) -> None:
        source = (CHAT_DIR / "scene.js").read_text(encoding="utf-8")
        assert "window.__scene" in source
        assert "renderFrame" in source
        assert "duration" in source

    def test_chat_script_schema(self) -> None:
        script = _load_script()
        assert script["duration_sec"] > 0
        assert script["messages"], "至少要有消息"
        for message in script["messages"]:
            assert set(message) >= {"t", "side", "text"}
            assert message["side"] in {"user", "ai"}
            assert 0 <= message["t"] <= script["duration_sec"]
        times = [m["t"] for m in script["messages"]]
        assert times == sorted(times), "消息时间必须单调"

    def test_status_times_within_duration(self) -> None:
        script = _load_script()
        for entry in script["status"]:
            assert 0 <= entry["t"] <= script["duration_sec"]
            assert entry["state"] in {"online", "away", "offline"}

    def test_scene_builder_injects_data_and_wraps_author(self) -> None:
        module = _render_module()
        js = module._scene_js(_load_script())
        assert js.startswith("window.__chat = ")
        assert "window.__scene" in js
        html = build_composition_html(
            js, duration_sec=12.0, width=960, height=540, fps=30, background="#0a0a0c"
        )
        assert '"world.execute(me);"' in html
        assert "(function (Math, Date, setTimeout, setInterval, requestAnimationFrame)" in html
        assert "--width" not in html  # 防呆：不把 CLI 参数写进 HTML

    def test_background_is_opaque_for_full_frame(self) -> None:
        module = _render_module()
        html = build_composition_html(
            module._scene_js(_load_script()),
            duration_sec=12.0,
            width=960,
            height=540,
            background="#0a0a0c",
        )
        assert "background: #0a0a0c" in html
