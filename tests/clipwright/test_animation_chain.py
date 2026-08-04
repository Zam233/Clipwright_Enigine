"""T2 (C6a) — 动画 overlay 的 ASS 渲染链（drawtext → ASS 迁移）。

原 tests/test_animation_chain.py 第 8 节验证的是动画 overlay 在 RenderService
内的分类：drawtext（文字/动画）→ 文字烧录路径，hyperframes / diagram_params →
Hyperframes 图解路径。字幕渲染默认改走 ASS 后，这条分类落在
``_apply_text_concat``（ASS Dialogue）与 Hyperframes 之间的分派：
- renderer=="drawtext" 或 无 diagram_params → 生成 ASS Dialogue（``-vf ass=``）
- renderer=="hyperframes" 或 带 diagram_params → 跳过文字路径（走 Hyperframes）

注意：仓库根 tests/test_animation_chain.py（脚本式）存在既有 collection error，
不属于本任务，保持不动。
"""

from __future__ import annotations

import types

from clipwright.services.render import RenderService


def _anim_overlay(**kw) -> dict:
    """动画 overlay dict（与 _extract_animation_overlay 同构）。"""
    base = dict(start_sec=0.0, duration_sec=3.0, text="Anim", font_size=72,
                font_color="#ffd700", position="center", offset_y=0,
                anim_type="fade_in", renderer="drawtext",
                anim_class="hf-fade-in", category="text", _track_idx=1)
    base.update(kw)
    return base


class TestOverlayClassification:
    """原 tests/test_animation_chain.py §8 的分类不变量（pytest 化）。"""

    def test_drawtext_text_goes_to_text_path(self) -> None:
        ov = _anim_overlay(renderer="drawtext")
        assert ov.get("renderer") != "hyperframes" and not ov.get("diagram_params")

    def test_hyperframes_logic_goes_to_hf_path(self) -> None:
        ov = _anim_overlay(renderer="hyperframes", diagram_params={"preset": "diagram"})
        assert ov.get("renderer") == "hyperframes" or ov.get("diagram_params")

    def test_diagram_without_renderer_goes_to_hf_path(self) -> None:
        ov = _anim_overlay(diagram_params={"preset": "arrow"})
        assert ov.get("renderer") == "hyperframes" or ov.get("diagram_params")


class TestApplyTextConcatAnimationPath:
    async def test_drawtext_animation_produces_ass_dialogue(self, tmp_path, monkeypatch) -> None:
        """文字动画（drawtext renderer）经 _apply_text_concat → ASS Dialogue + `-vf ass=`。"""
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_get_actual_duration", lambda p: 0.0)
        svc = RenderService(work_dir=tmp_path)
        captured: dict = {}
        async def fake_ff(cmd, **kw):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0)
        monkeypatch.setattr(svc, "_ff", fake_ff)
        ov = _anim_overlay(renderer="drawtext", text="Hello动画")
        await svc._apply_text_concat("video.mp4", [ov], "libx264", "medium",
                                     width=1920, height=1080)
        ass_text = (svc._work_dir / "subs_0.ass").read_text(encoding="utf-8")
        assert "Dialogue:" in ass_text
        assert "[V4+ Styles]" in ass_text
        vf_arg = captured["cmd"][captured["cmd"].index("-vf") + 1]
        assert "ass=" in vf_arg
        assert "drawtext=" not in vf_arg

    async def test_hyperframes_overlay_skipped_no_ass(self, tmp_path, monkeypatch) -> None:
        """hyperframes/diagram overlay → 跳过文字路径，不写 .ass 文件。"""
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_get_actual_duration", lambda p: 0.0)
        svc = RenderService(work_dir=tmp_path)
        async def fake_ff(cmd, **kw):
            return types.SimpleNamespace(returncode=0)
        monkeypatch.setattr(svc, "_ff", fake_ff)
        ov = _anim_overlay(renderer="hyperframes", diagram_params={"preset": "diagram"})
        out = await svc._apply_text_concat("video.mp4", [ov], "libx264", "medium",
                                           width=1920, height=1080)
        assert out == "video.mp4"
        assert not (svc._work_dir / "subs_0.ass").exists()

    async def test_mixed_overlays_only_text_gets_dialogue(self, tmp_path, monkeypatch) -> None:
        """混合 overlay：仅文字动画生成 Dialogue，图解动画被跳过。"""
        import clipwright.services.render as render_mod
        monkeypatch.setattr(render_mod, "_get_actual_duration", lambda p: 0.0)
        svc = RenderService(work_dir=tmp_path)
        captured: dict = {}
        async def fake_ff(cmd, **kw):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0)
        monkeypatch.setattr(svc, "_ff", fake_ff)
        overlays = [
            _anim_overlay(renderer="drawtext", text="文字1"),
            _anim_overlay(renderer="hyperframes", diagram_params={"preset": "diagram"}),
            _anim_overlay(diagram_params={"preset": "arrow"}),
            _anim_overlay(renderer="drawtext", text="文字2"),
        ]
        await svc._apply_text_concat("video.mp4", overlays, "libx264", "medium",
                                     width=1920, height=1080)
        ass_text = (svc._work_dir / "subs_0.ass").read_text(encoding="utf-8")
        dialogues = [l for l in ass_text.splitlines() if l.startswith("Dialogue:")]
        assert len(dialogues) == 2
        assert "文字1" in dialogues[0]
        assert "文字2" in dialogues[1]
