"""V49 第 7 类形态「纯图形歌词」的场景纪律单测。

渲染本身慢（一次 20 秒起），不在单测里跑；这里锁住**源码级纪律**：

- `scene.js` 不得包含歌词文本（V31 的纪律：文本走数据文件）；
- 音频/歌词数据放 `lyric_lines.json`，且带 t0/t1（逐词高亮的依据）；
- 场景源码要过确定性 lint（不出现 Math.random / 定时器 / 读墙钟）。
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.lint_determinism import scan_text

SCENE_DIR = Path(__file__).resolve().parents[2] / "examples" / "mv_poc" / "archetypes"


def _scene_js() -> str:
    return (SCENE_DIR / "scene.js").read_text(encoding="utf-8")


def _lyric_lines() -> dict:
    return json.loads((SCENE_DIR / "lyric_lines.json").read_text(encoding="utf-8"))


class TestLyricGraphicDiscipline:
    def test_scene_js_passes_determinism_lint(self) -> None:
        hits = scan_text(_scene_js(), "archetypes/scene.js")
        assert hits == [], [h.render() for h in hits]

    def test_lyric_text_lives_in_data_file_not_scene_js(self) -> None:
        source = _scene_js()
        for line in _lyric_lines()["lines"]:
            raw = str(line["text"]).replace(" ", "")
            assert raw not in source.replace(" ", ""), f"歌词混进了 scene.js: {raw[:12]}"

    def test_has_lyric_graphic_branch(self) -> None:
        assert "lyric_graphic" in _scene_js()

    def test_lyric_lines_have_timing(self) -> None:
        lines = _lyric_lines()["lines"]
        assert len(lines) >= 4
        for line in lines:
            assert float(line["t1"]) > float(line["t0"]) >= 0
        # 段落首尾相接，不留空洞（逐词高亮要连续推进）
        ordered = sorted(lines, key=lambda item: float(item["t0"]))
        for prev, cur in zip(ordered, ordered[1:]):
            assert abs(float(prev["t1"]) - float(cur["t0"])) < 1e-6

    def test_render_script_registers_kind_and_data(self) -> None:
        source = (Path(__file__).resolve().parents[2] / "scripts" / "render_archetypes.py").read_text(
            encoding="utf-8"
        )
        assert '"lyric_graphic"' in source
        assert "lyric_lines.json" in source
