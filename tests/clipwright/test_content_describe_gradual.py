"""真实素材补测引入的两项能力：本地描述合成 + 渐变转场补检。

为什么单独一个文件：

- 这两项都是**真实素材实测暴露的缺口**（`visual_description` 覆盖率 0、溶接测不到），
  合成基准测不出来；
- 渐变检测需要**真实像素**才有意义（纯色块之间没有"渐变"可言），
  所以这里用 ffmpeg 现搭溶接/硬切素材，真值精确已知；
- 没有 ffmpeg 时优雅跳过（不假装测过）。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.schema.content import ShotRecord, ShotSpeech, ShotStats, ShotVisual
from clipwright.services import content_describe as cd
from clipwright.services import content_index as ci

FFMPEG = shutil.which("ffmpeg")


# ── 本地描述合成 ──────────────────────────────────────────


class TestComposeDescription:
    def _shot(self, **kw) -> ShotRecord:
        stats = kw.pop("stats", {})
        visual = kw.pop("visual", {})
        speech = kw.pop("speech", {})
        if "frame_count" not in stats and not stats.get("is_black") and not stats.get("is_white"):
            # 表示"统计确实跑过"——否则默认值 0 会被当成"测到 0"，凭空写出"偏暗"
            stats["frame_count"] = 3
        return ShotRecord(index=0, t0=0.0, t1=2.0, duration_sec=2.0,
                          stats=ShotStats(**stats), visual=ShotVisual(**visual),
                          speech=ShotSpeech(**speech),
                          audio_events=kw.pop("audio_events", []))

    def test_unmeasured_stats_produce_no_words(self):
        """**未测量 vs 测到 0** 必须分开：默认值是 0，直接当"偏暗"就是捏造。

        实测踩到过：对一条裸 `ShotRecord` 合成描述得到"偏暗的画面。"。
        """
        bare = ShotRecord(index=0, t0=0.0, t1=1.0, duration_sec=1.0)
        assert cd.compose_description(bare) == ""

    def test_includes_measured_fields(self):
        text = cd.compose_description(self._shot(
            stats={"brightness": 30.0}, visual={"tags": ["夜景"], "camera_motion": "low"}))
        assert "偏暗" in text and "夜景" in text and "缓慢摇动" in text

    def test_does_not_invent_content(self):
        """只有统计、没有任何语义标签时，**不许编内容**（只说明画面属性）。"""
        text = cd.compose_description(self._shot(stats={"brightness": 200.0}))
        assert "非常亮" in text
        for word in ("足球", "人", "城市", "车"):
            assert word not in text

    def test_black_and_white_frames(self):
        assert "全黑" in cd.compose_description(self._shot(stats={"is_black": True}))
        assert "全白" in cd.compose_description(self._shot(stats={"is_white": True}))

    def test_quality_flags(self):
        text = cd.compose_description(self._shot(stats={"sharpness": 10.0}))
        assert "模糊" in text
        text = cd.compose_description(self._shot(stats={"sharpness": 5000.0}))
        assert "清晰锐利" in text

    def test_speech_keywords_are_used(self):
        text = cd.compose_description(self._shot(speech={"keywords": ["进球", "射门"]}))
        assert "进球" in text and "射门" in text

    def test_speech_without_keywords_notes_narration(self):
        text = cd.compose_description(self._shot(speech={"text": "大家好"}))
        assert "旁白或对话" in text

    def test_audio_events_are_translated(self):
        text = cd.compose_description(self._shot(audio_events=[{"kind": "loud"}]))
        assert "较大声响" in text

    def test_face_negative_is_phrased_as_not_detected(self):
        """刻意写"没有检出人脸"而不是"没有人"——弱检测器的假阴性不能变成断言。"""
        text = cd.compose_description(self._shot(visual={"has_face": False}))
        assert "没有检出人脸" in text

    def test_unknown_values_are_skipped(self):
        text = cd.compose_description(self._shot(visual={"shot_size": "unknown",
                                                        "camera_motion": "unknown"}))
        assert "镜头" not in text and "unknown" not in text

    def test_empty_shot_yields_empty_string(self):
        assert cd.compose_description(ShotRecord(index=0, t0=0, t1=1, duration_sec=1.0)) == ""

    def test_should_compose_respects_existing_description(self):
        shot = self._shot(visual={"description": "云端给的描述", "tags": ["水面"]})
        assert cd.should_compose(shot) is False
        assert cd.compose_description(shot)      # 但函数本身照样能合成（不改对象）

    def test_fill_descriptions_marks_evidence_and_confidence(self):
        shot = self._shot(visual={"tags": ["水面"]})
        assert cd.fill_descriptions([shot]) == 1
        assert shot.visual.description
        assert shot.evidence["visual.description"] == "composed:stats+clip+audio"
        # 置信度刻意压低：拼接出来的句子读起来像"理解"，但它不是模型级理解
        assert shot.confidence["visual.description"] == 0.5

    def test_fill_descriptions_does_not_overwrite(self):
        shot = self._shot(visual={"description": "已有描述", "tags": ["水面"]})
        assert cd.fill_descriptions([shot]) == 0
        assert shot.visual.description == "已有描述"

    def test_fill_descriptions_skips_empty_compositions(self):
        shot = ShotRecord(index=0, t0=0.0, t1=1.0, duration_sec=1.0)
        assert cd.fill_descriptions([shot]) == 0
        assert not shot.visual.description


# ── 渐变转场补检（真实像素）───────────────────────────────


pytestmark_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="需要 ffmpeg 生成真实像素素材")


@pytestmark_ffmpeg
class TestGradualTransitions:
    @staticmethod
    def _rich(path: Path, kind: str = "testsrc2", seconds: float = 4.0, fps: int = 25) -> None:
        """**直方图丰富**的素材（纯色块不适合测渐变：溶接时会变成第三种纯色，
        单帧跳变很大，看着像硬切——真实拍摄素材的溶接是"混合中间态"）。"""
        src = {"testsrc2": f"testsrc2=s=320x180:r={fps}:d={seconds}",
               "smptebars": f"smptebars=s=320x180:r={fps}:d={seconds}",
               "rgbtestsrc": f"rgbtestsrc=s=320x180:r={fps}:d={seconds}"}[kind]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src,
                        "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)

    @staticmethod
    def _solid(path: Path, color: str, seconds: float = 4.0, fps: int = 25) -> None:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c={color}:s=320x180:r={fps}:d={seconds}",
                        "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", str(path)], check=True,
                       capture_output=True)

    @staticmethod
    def _noise(path: Path, seconds: float = 4.0, fps: int = 25, seed: int = 7) -> None:
        """移动噪声：**持续变化**但没有转场——用来验证"持续性"判据不会把运动当转场。"""
        src = (f"nullsrc=s=320x180:r={fps}:d={seconds},"
               f"geq=random(1)*255:128:128,format=yuv420p")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src,
                        "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)

    @staticmethod
    def _xfade(a: Path, b: Path, out: Path, offset: float, dur: float = 0.6) -> None:
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-i", str(b),
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={dur}:offset={offset}[o]",
            "-map", "[o]", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", str(out)], check=True, capture_output=True)

    @staticmethod
    def _hardcut(a: Path, b: Path, out: Path) -> None:
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-i", str(b),
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[o]", "-map", "[o]",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)],
            check=True, capture_output=True)

    def test_crossfade_is_detected(self, tmp_path):
        """**ffmpeg 老路径测不到溶接**（R2 记录的方法学盲区）——补检必须能测到。"""
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        out = tmp_path / "xfade.mp4"
        self._rich(a, "testsrc2")
        self._rich(b, "smptebars")
        self._xfade(a, b, out, offset=3.4)

        hard_only = ci.detect_cut_times(out, 0.3, detect_gradual=False)
        graduals = ci.detect_gradual_transitions(out)
        assert graduals, "溶接未被检出（这正是要修的方法学盲区）"
        # 真值：offset=3.4 + dur/2=0.3 → 中心约 3.70s
        assert any(abs(t - 3.70) <= 0.9 for t in graduals), (graduals, hard_only)

    def test_soft_crossfade_between_similar_scenes(self, tmp_path):
        """相似画面之间的溶接（更难）也要能检出。"""
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        out = tmp_path / "soft.mp4"
        self._rich(a, "testsrc2")
        self._rich(b, "rgbtestsrc")
        self._xfade(a, b, out, offset=3.4)
        graduals = ci.detect_gradual_transitions(out)
        assert any(abs(t - 3.70) <= 0.9 for t in graduals), graduals

    def test_hardcut_is_not_reported_as_gradual(self, tmp_path):
        """硬切窗口内单步差异超阈值 → 不许当成"渐变"重复报（否则一处切变两处）。"""
        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        out = tmp_path / "hard.mp4"
        self._rich(a, "testsrc2")
        self._rich(b, "smptebars")
        self._hardcut(a, b, out)
        assert ci.detect_gradual_transitions(out) == []

    def test_continuous_motion_is_not_a_transition(self, tmp_path):
        """持续运动（噪声）没有"起始稳定 → 渐变 → 结束稳定"的结构。

        注意：这是**弱保证**。真实素材实测里，海浪的连续涌动仍会误报 1 处
        （见 `scripts/benchmark_real_material.py` 与基准文档），所以这里只断言
        "噪声片段不会被报成多处转场"。
        """
        n = tmp_path / "noise.mp4"
        self._noise(n)
        assert len(ci.detect_gradual_transitions(n)) <= 1

    def test_detect_cut_times_merges_both_paths(self, tmp_path):
        """硬切 + 溶接混在一条素材里，两条路的结果都要在（且不互相吞掉）。"""
        a, b, c = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"
        hc, out = tmp_path / "hc.mp4", tmp_path / "mixed.mp4"
        self._rich(a, "testsrc2")
        self._rich(b, "smptebars")
        self._rich(c, "rgbtestsrc")
        # 第一步：硬切（前段截到 2.0s）→ 分两步做，避免一条 filtergraph 里
        # concat 出来的流没有正确时间基、xfade 报 -22
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-i", str(b),
            "-filter_complex",
            "[0:v]trim=0:2,setpts=PTS-STARTPTS[v0];"
            "[1:v]trim=0:2,setpts=PTS-STARTPTS[v1];"
            "[v0][v1]concat=n=2:v=1:a=0[hc]",
            "-map", "[hc]", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", str(hc)], check=True, capture_output=True)
        # 第二步：在硬切结果后接一段溶接（硬切在 2.0s；溶接起点 4.0、中心 4.3）
        self._xfade(hc, c, out, offset=4.0)

        cuts = asyncio.run(ci.detect_cut_times(out, 0.3))
        assert any(abs(t - 2.0) <= 0.3 for t in cuts), ("硬切漏检", cuts)
        assert any(abs(t - 4.3) <= 0.9 for t in cuts), ("溶接漏检", cuts)

    def test_setting_can_disable_gradual(self, tmp_path, monkeypatch):
        from clipwright.config import settings

        a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
        out = tmp_path / "xfade.mp4"
        self._rich(a, "testsrc2")
        self._rich(b, "smptebars")
        self._xfade(a, b, out, offset=3.4)
        monkeypatch.setattr(settings, "content_index_gradual_detect", False)
        assert asyncio.run(ci.detect_cut_times(out, 0.3)) == []

    def test_hist_intersection_requires_l1(self):
        """踩坑回归：直方图必须 **L1** 归一化，否则"交集"能 >1、跨度出现负数。

        第一版用 `cv2.normalize` 的默认 L2 归一化，实测一处溶接的跨度是 **-0.92**
        （看起来像"完全负相关"，实际是口径错了），溶接因此检不出来。
        """
        import numpy as np

        a = np.array([0.5, 0.5], dtype="float32")
        b = np.array([0.5, 0.5], dtype="float32")
        assert ci._hist_intersection(a, b) == pytest.approx(1.0)
        c = ci._l1(np.array([2.0, 2.0], dtype="float32"))
        assert float(c.sum()) == pytest.approx(1.0)
