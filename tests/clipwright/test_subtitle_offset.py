"""Bug1 回归测试 — 字幕累积偏移修复（ASS 语义）。

Baseline：钉住当前可观察行为——同轨字幕 ASS Dialogue 完全一致、不再逐条上移。
Failing-first：同轨 3 条字幕的 Dialogue override tags（\\an 对齐）必须完全一致。
若修复缺失（旧的 ``y_off = min(len(...) * 35, 500)`` 累积逻辑），
ASS 路径会输出带逐条累积垂直偏移的 Dialogue，该测试会失败。

字幕渲染默认走 ASS（libass，14 字段全生效）；drawtext 位置表达式基线保留在
TextStyle.drawtext_position()（fallback 构建器仍用）。
"""

from __future__ import annotations

import types

from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services.render import RenderService
from clipwright.tool.design import TextStyle


def _caption(cid: str, start: float, dur: float, text: str,
             position: str = "bottom") -> Clip:
    """构造一个字幕 clip（真实 schema 对象）。"""
    return Clip(
        id=cid, kind="caption", asset_id="", track_id="t1",
        start_sec=start, duration_sec=dur, text=text,
        metadata={"position": position},
    )


def _dialogue_body(dialogue: str) -> str:
    """提取 Dialogue 行第 9 个字段之后的内容（override tags + 转义文本）。"""
    return dialogue.split(",", 9)[9]


async def _ass_dialogues(svc: RenderService, overlays: list[dict], monkeypatch,
                         actual_dur: float = 0.0) -> list[str]:
    """走 ASS 路径（mock ffprobe/ffmpeg），返回生成的 Dialogue 行列表。"""
    import clipwright.services.render as render_mod
    monkeypatch.setattr(render_mod, "_get_actual_duration", lambda p: actual_dur)
    async def fake_ff(cmd, **kw):
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(svc, "_ff", fake_ff)
    await svc._apply_text_concat("video.mp4", overlays, "libx264", "medium",
                                 width=1920, height=1080)
    ass_path = svc._work_dir / "subs_0.ass"
    if not ass_path.exists():
        return []
    return [l for l in ass_path.read_text(encoding="utf-8").splitlines()
            if l.startswith("Dialogue:")]


class TestBaselinePinned:
    """基线特征化测试：钉住当前（已修复）行为，在未改动代码上必须通过。"""

    def test_drawtext_position_bottom_offset_zero(self) -> None:
        """bottom + offset_y=0 的 y 表达式基线（drawtext fallback 构建器）。"""
        ts = TextStyle(position="bottom", offset_y=0)
        assert ts.drawtext_position() == ("(w-text_w)/2", "h-text_h-20-0")

    def test_drawtext_position_top_offset_zero(self) -> None:
        """top + offset_y=0 的 y 表达式基线（drawtext fallback 构建器）。"""
        ts = TextStyle(position="top", offset_y=0)
        assert ts.drawtext_position() == ("(w-text_w)/2", "20+0")

    def test_drawtext_position_center(self) -> None:
        """center 位置基线（无 offset）。"""
        ts = TextStyle(position="center")
        assert ts.drawtext_position() == ("(w-text_w)/2", "(h-text_h)/2")

    def test_extract_text_overlay_offset_zero(self) -> None:
        """_extract_text_overlay 当前产出的 offset_y 固定为 0。"""
        ov = RenderService._extract_text_overlay(_caption("c1", 0, 2, "字幕"), 1, [])
        assert ov["offset_y"] == 0
        assert ov["style"]["offset_y"] == 0


class TestSubtitleNoCumulativeOffset:
    """Failing-first：同轨多字幕不得累积上移（ASS 语义）。"""

    async def test_three_same_track_subtitles_identical_dialogue(self, tmp_path, monkeypatch) -> None:
        """同一 track 的 3 条字幕 → ASS Dialogue override tags 完全一致（不逐条上移）。"""
        svc = RenderService(work_dir=tmp_path)
        clips = [_caption(f"c{i}", i * 3.0, 2.0, f"第{i}条字幕") for i in range(3)]
        overlays = []
        for clip in clips:
            overlays.append(svc._extract_text_overlay(clip, 1, overlays))
        dialogues = await _ass_dialogues(svc, overlays, monkeypatch)
        assert len(dialogues) == 3
        bodies = [_dialogue_body(d) for d in dialogues]
        # 全部底部对齐（\an2 + 默认描边 \bord2），无逐条累积垂直偏移
        assert all(b.startswith(r"{\an2\bord2}") for b in bodies)
        # 唯一差异只有转义文本本身 → override tags 完全一致
        assert [b[len(r"{\an2\bord2}"):] for b in bodies] == ["第0条字幕", "第1条字幕", "第2条字幕"]
        # 最后一条字幕不得被推出屏幕：offset_y 为 0，即贴底部安全区
        assert all(ov["offset_y"] == 0 for ov in overlays)

    async def test_single_subtitle(self, tmp_path, monkeypatch) -> None:
        """单条字幕：正常生成，底部对齐（\an2）。"""
        svc = RenderService(work_dir=tmp_path)
        ov = svc._extract_text_overlay(_caption("c1", 0, 2, "唯一字幕"), 1, [])
        dialogues = await _ass_dialogues(svc, [ov], monkeypatch)
        assert len(dialogues) == 1
        assert _dialogue_body(dialogues[0]) == r"{\an2\bord2}唯一字幕"

    async def test_top_position_variant(self, tmp_path, monkeypatch) -> None:
        """top 位置字幕：顶部对齐（\an8），同样不累积。"""
        svc = RenderService(work_dir=tmp_path)
        ovs = [svc._extract_text_overlay(_caption(f"c{i}", i * 3.0, 2.0, f"顶{i}", "top"), 2, [])
               for i in range(2)]
        dialogues = await _ass_dialogues(svc, ovs, monkeypatch)
        assert len(dialogues) == 2
        bodies = [_dialogue_body(d) for d in dialogues]
        assert bodies == [r"{\an8\bord2}顶0", r"{\an8\bord2}顶1"]

    async def test_empty_text_no_dialogue(self, tmp_path, monkeypatch) -> None:
        """空文本 clip：不生成 Dialogue，不崩溃（malformed input 防护）。"""
        svc = RenderService(work_dir=tmp_path)
        ov = svc._extract_text_overlay(_caption("c1", 0, 2, ""), 1, [])
        dialogues = await _ass_dialogues(svc, [ov], monkeypatch)
        assert dialogues == []

    def test_empty_subtitle_list(self, tmp_path) -> None:
        """空字幕列表：_extract_segments 不报错、产出空 overlay 列表。"""
        svc = RenderService(work_dir=tmp_path)
        tl = Timeline(id="t", tracks=[Track(id="t1", kind="caption", index=1, clips=[])])
        _, _, text_overlays, _, _ = svc._extract_segments(tl)
        assert text_overlays == []
