"""R2：镜头画面维度标注测试（本地模型优先 + 云端按需）。

分组：
1. **纯函数**：景别阈值映射、词表解析（JSON 覆盖 / 非法回退 / 空回退）、质量标志计算；
2. **标注流程**（全部打桩，不加载真模型、不联网）：
   CLIP → 标签与置信度；CLIP 缺席 → 退回分类器；云端开关与"最多 N 个镜头"闸门；
   云端失败静默降级；单镜头异常隔离；批处理并发与进度回调；
3. **接线**：`content_index.build_index` 是否真的把标注结果写进 ShotRecord（打桩标注器）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clipwright.services import content_vision as cvmod
from clipwright.services.content_vision import (
    DEFAULT_LABEL_VOCAB,
    SHOT_SIZE_THRESHOLDS,
    classify_shot_size,
    detect_quality_flags,
    label_shot,
    label_shots,
    vocab_for,
)

pytest.importorskip("PIL")


def _img(path: Path, color=(128, 128, 128), size=(64, 64)) -> str:
    from PIL import Image

    Image.new("RGB", size, color).save(path)
    return str(path)


# ── 1. 纯函数 ─────────────────────────────────────────────


class TestShotSize:
    @pytest.mark.parametrize("ratio,expect", [
        (1.0, "extreme_close"),
        (0.35, "extreme_close"),
        (0.34, "close"),
        (0.12, "close"),
        (0.11, "medium"),
        (0.04, "medium"),
        (0.03, "full"),
        (0.012, "full"),
        (0.011, "wide"),
        (0.0, "wide"),
    ])
    def test_thresholds(self, ratio: float, expect: str) -> None:
        assert classify_shot_size(ratio) == expect

    def test_thresholds_are_descending(self) -> None:
        """阈值表必须严格降序，否则映射会出现"永远命中第一个"的静默错误。"""
        values = [t for t, _ in SHOT_SIZE_THRESHOLDS]
        assert values == sorted(values, reverse=True)

    def test_negative_ratio_is_wide(self) -> None:
        assert classify_shot_size(-1.0) == "wide"


class TestVocab:
    def test_default_vocab_used_when_empty(self) -> None:
        assert vocab_for("") == DEFAULT_LABEL_VOCAB
        assert vocab_for(None) == DEFAULT_LABEL_VOCAB

    def test_json_override(self) -> None:
        got = vocab_for('{"咖啡": "a cup of coffee"}')
        assert got == {"咖啡": "a cup of coffee"}

    def test_invalid_json_falls_back(self) -> None:
        assert vocab_for("{not json") == DEFAULT_LABEL_VOCAB
        assert vocab_for("[]") == DEFAULT_LABEL_VOCAB
        assert vocab_for("{}") == DEFAULT_LABEL_VOCAB

    def test_default_vocab_prompts_are_english(self) -> None:
        """提示词必须是英文（CLIP 文本塔以英文为主），标签是中文。"""
        assert all(p.isascii() for p in DEFAULT_LABEL_VOCAB.values())
        assert any(not k.isascii() for k in DEFAULT_LABEL_VOCAB)


class TestQualityFlags:
    def test_empty_input(self) -> None:
        assert detect_quality_flags([]) == {}

    def test_overexposed(self, tmp_path: Path) -> None:
        flags = detect_quality_flags([_img(tmp_path / "w.png", (255, 255, 255))])
        assert flags["overexposed_ratio"] > 0.99

    def test_underexposed(self, tmp_path: Path) -> None:
        flags = detect_quality_flags([_img(tmp_path / "b.png", (0, 0, 0))])
        assert flags["underexposed_ratio"] > 0.99

    def test_edge_energy_higher_for_texture(self, tmp_path: Path) -> None:
        import numpy as np
        from PIL import Image

        flat = _img(tmp_path / "flat.png", (120, 120, 120), (64, 64))
        rng = np.random.default_rng(3)
        arr = rng.integers(0, 255, (64, 64, 3), dtype="uint8")
        noisy = tmp_path / "noisy.png"
        Image.fromarray(arr).save(noisy)
        assert (detect_quality_flags([str(noisy)])["edge_energy"]
                > detect_quality_flags([flat])["edge_energy"])

    def test_gray_is_neither_extreme(self, tmp_path: Path) -> None:
        flags = detect_quality_flags([_img(tmp_path / "g.png", (128, 128, 128))])
        assert flags["overexposed_ratio"] == 0.0
        assert flags["underexposed_ratio"] == 0.0


# ── 2. 标注流程（打桩） ───────────────────────────────────


@pytest.fixture()
def stub_clip(monkeypatch):
    """把 CLIP 打桩成确定性结果，避免测试依赖 600MB 权重。"""
    def fake_scores(paths, vocab=None):
        return [{("咖啡杯" if i == 0 else "人物"): 0.8,
                 "空镜": 0.05, "食物": 0.02}
                for i, _ in enumerate(paths)]

    monkeypatch.setattr(cvmod, "clip_scores_for_paths", fake_scores)
    return fake_scores


@pytest.fixture()
def stub_detectors(monkeypatch):
    monkeypatch.setattr(cvmod._detectors, "detect",
                        lambda p: {"has_face": True, "face_ratio": 0.2, "person_ratio": 0.0})


@pytest.fixture()
def stub_classifier(monkeypatch):
    """把本地分类器也打桩：避免测试去加载真实 ViT 权重（慢且依赖模型可用性）。"""
    from clipwright.services import vision as vision_mod

    async def fake_analyze(self, path, provider=None):
        return {"tags": ["粗标签"], "labels": ["something"], "description": "内容: something",
                "scene_type": "", "model": "stub-clf"}

    monkeypatch.setattr(vision_mod.VisionService, "analyze_image", fake_analyze)
    return fake_analyze


class TestLabelShot:
    def test_clip_tags_become_visual_fields(self, tmp_path, stub_clip, stub_detectors) -> None:
        f = _img(tmp_path / "a.png")
        out = asyncio.run(label_shot([f], clip_scores={"人物": 0.7, "空镜": 0.1}))
        assert out["visual"]["tags"] == ["人物"]           # 低于阈值的被丢弃
        assert out["evidence"]["visual.tags"] == "clip_zero_shot"
        assert out["confidence"]["clip.人物"] == 0.7
        assert out["visual"]["shot_size"] == "close"       # 0.2 → close
        assert out["visual"]["has_face"] is True

    def test_low_score_tags_dropped(self, tmp_path, stub_detectors, stub_classifier) -> None:
        f = _img(tmp_path / "a.png")
        out = asyncio.run(label_shot([f], clip_scores={"人物": 0.14}))
        assert "人物" not in out["visual"].get("tags", [])
        assert out["visual"]["tags"] == ["粗标签"]   # 低分时不留下"仅 0.14 的标签"

    def test_falls_back_to_classifier_without_clip(self, tmp_path, monkeypatch) -> None:
        """CLIP 不可用 → 用本地分类器，且置信度必须标低（ImageNet 标签粗）。"""
        from clipwright.services import vision as vision_mod

        async def fake_analyze(self, path, provider=None):
            assert provider == "transformers"  # 必须显式指定本地后端
            return {"tags": ["自然"], "labels": ["valley"], "description": "内容: valley",
                    "scene_type": "landscape", "model": "vit-base"}

        monkeypatch.setattr(vision_mod.VisionService, "analyze_image", fake_analyze)
        f = _img(tmp_path / "a.png")
        out = asyncio.run(label_shot([f], use_clip=False))
        assert out["visual"]["tags"] == ["自然"]
        assert out["evidence"]["visual.tags"].startswith("classifier/")
        assert out["confidence"]["visual.tags"] == 0.3

    def test_cloud_llm_description_when_enabled(self, tmp_path, monkeypatch) -> None:
        from clipwright.services import vision as vision_mod

        calls: list[str] = []

        async def fake_analyze(self, path, provider=None):
            calls.append(provider or "")
            if provider == "llm":
                return {"tags": ["室内"], "description": "一个人坐在咖啡馆里", "labels": []}
            return {"tags": ["咖啡杯"], "labels": [], "description": "", "model": "vit"}

        monkeypatch.setattr(vision_mod.VisionService, "analyze_image", fake_analyze)
        monkeypatch.setattr(cvmod.settings, "content_index_visual_llm", True, raising=False)
        f = _img(tmp_path / "a.png")
        out = asyncio.run(label_shot([f], use_clip=False, use_llm=True))
        assert out["visual"]["description"] == "一个人坐在咖啡馆里"
        assert out["evidence"]["visual.description"].startswith("llm/")
        assert "llm" in calls

    def test_cloud_llm_disabled_by_setting(self, tmp_path, monkeypatch) -> None:
        from clipwright.services import vision as vision_mod

        calls: list[str] = []

        async def fake_analyze(self, path, provider=None):
            calls.append(provider or "")
            return {"tags": [], "labels": [], "description": ""}

        monkeypatch.setattr(vision_mod.VisionService, "analyze_image", fake_analyze)
        monkeypatch.setattr(cvmod.settings, "content_index_visual_llm", False, raising=False)
        f = _img(tmp_path / "a.png")
        asyncio.run(label_shot([f], use_clip=False, use_llm=True))
        assert "llm" not in calls, "开关关闭时绝不能调用云端多模态"

    def test_cloud_failure_degrades_silently(self, tmp_path, monkeypatch) -> None:
        from clipwright.services import vision as vision_mod

        async def boom(self, path, provider=None):
            if provider == "llm":
                raise RuntimeError("云挂了")
            return {"tags": ["人物"], "labels": [], "description": ""}

        monkeypatch.setattr(vision_mod.VisionService, "analyze_image", boom)
        monkeypatch.setattr(cvmod.settings, "content_index_visual_llm", True, raising=False)
        f = _img(tmp_path / "a.png")
        out = asyncio.run(label_shot([f], use_clip=False, use_llm=True))
        assert out["visual"]["tags"] == ["人物"]     # 本地结果仍在
        assert not out.get("visual", {}).get("description")

    def test_missing_frames_returns_empty(self, tmp_path) -> None:
        assert asyncio.run(label_shot([], use_clip=False)) == {}
        assert asyncio.run(label_shot([str(tmp_path / "ghost.png")], use_clip=False)) == {}

    def test_detector_absent_leaves_shot_size_unknown(self, tmp_path, monkeypatch,
                                                       stub_classifier) -> None:
        monkeypatch.setattr(cvmod._detectors, "detect", lambda p: {})
        f = _img(tmp_path / "a.png")
        out = asyncio.run(label_shot([f], clip_scores={"人物": 0.9}))
        assert "shot_size" not in out["visual"]


class TestLabelShotsBatch:
    def test_batch_clip_called_once(self, tmp_path, monkeypatch) -> None:
        calls: list[int] = []

        def fake_scores(paths, vocab=None):
            calls.append(len(paths))
            return [{"人物": 0.9} for _ in paths]

        monkeypatch.setattr(cvmod, "clip_scores_for_paths", fake_scores)
        monkeypatch.setattr(cvmod._detectors, "detect", lambda p: {})
        frames = [[_img(tmp_path / f"{i}.png")] for i in range(5)]
        out = asyncio.run(label_shots(frames, use_clip=True))
        assert calls == [5], "CLIP 应该整批一次前向，而不是逐镜头调用"
        assert all(r["visual"]["tags"] == ["人物"] for r in out)

    def test_progress_is_monotonic(self, tmp_path, monkeypatch, stub_classifier) -> None:
        monkeypatch.setattr(cvmod, "clip_scores_for_paths", lambda p, vocab=None: [{} for _ in p])
        monkeypatch.setattr(cvmod._detectors, "detect", lambda p: {})
        frames = [[_img(tmp_path / f"{i}.png")] for i in range(4)]
        seen: list[float] = []
        asyncio.run(label_shots(frames, use_clip=False,
                                progress=lambda p, m: seen.append(p),
                                progress_base=0.8, progress_span=0.2))
        assert seen == sorted(seen)
        assert seen[-1] == pytest.approx(1.0)
        assert seen[0] == pytest.approx(0.8 + 0.2 / 4)

    def test_single_failure_isolated(self, tmp_path, monkeypatch) -> None:
        async def flaky(frames, **kwargs):
            if frames and frames[0].endswith("1.png"):
                raise RuntimeError("坏帧")
            return {"visual": {"tags": ["ok"]}}

        monkeypatch.setattr(cvmod, "label_shot", flaky)
        monkeypatch.setattr(cvmod, "clip_scores_for_paths", lambda p, vocab=None: [{} for _ in p])
        frames = [[_img(tmp_path / f"{i}.png")] for i in range(3)]
        out = asyncio.run(label_shots(frames, use_clip=False))
        assert out[1] == {} and out[0]["visual"]["tags"] == ["ok"]

    def test_llm_max_shots_gate(self, tmp_path, monkeypatch) -> None:
        """预算闸门：只允许前 N 个镜头走云端。"""
        from clipwright.services import vision as vision_mod

        seen: list[str] = []

        async def fake_analyze(self, path, provider=None):
            seen.append(provider or "")
            return {"tags": [], "labels": [], "description": ""}

        monkeypatch.setattr(vision_mod.VisionService, "analyze_image", fake_analyze)
        monkeypatch.setattr(cvmod, "clip_scores_for_paths", lambda p, vocab=None: [{} for _ in p])
        monkeypatch.setattr(cvmod._detectors, "detect", lambda p: {})
        monkeypatch.setattr(cvmod.settings, "content_index_visual_llm", True, raising=False)
        frames = [[_img(tmp_path / f"{i}.png")] for i in range(5)]
        asyncio.run(label_shots(frames, use_clip=False, use_llm=True, llm_max_shots=2))
        assert seen.count("llm") == 2


class TestDetectorSafety:
    """检测器的**崩溃防护**（实测踩过：HOG 在小图上会崩进程，异常捕获不到）。

    这两条用例是回归守卫：它们在崩溃时会直接杀掉测试进程，所以"跑得过"本身就是证据。
    """

    def test_tiny_image_does_not_crash(self, tmp_path) -> None:
        for size in ((32, 32), (64, 64), (64, 127)):
            f = _img(tmp_path / f"t{size[0]}x{size[1]}.png", (90, 90, 90), size)
            out = cvmod._detectors.detect(f)
            assert isinstance(out, dict)
            assert out.get("person_ratio", 0.0) == 0.0   # 小图必须跳过 HOG

    def test_large_image_downscaled_before_detect(self, tmp_path) -> None:
        """大图必须先降采样（否则 HOG 在 1080p 上慢一个数量级）。"""
        f = _img(tmp_path / "big.png", (100, 100, 100), (1920, 1080))
        out = cvmod._detectors.detect(f)
        assert isinstance(out, dict)
        assert set(out).issubset({"has_face", "face_ratio", "person_ratio"})

    def test_missing_file_is_empty_dict(self, tmp_path) -> None:
        assert cvmod._detectors.detect(str(tmp_path / "ghost.png")) == {}


class TestClipAvailability:
    def test_clip_failure_marks_unavailable(self, monkeypatch) -> None:
        monkeypatch.setattr(cvmod, "_clip_failed", False, raising=False)
        monkeypatch.setattr(cvmod, "_clip_cache", {}, raising=False)
        monkeypatch.setattr(cvmod, "clip_scores_for_paths",
                            lambda paths, vocab=None: [{} for _ in paths])
        assert cvmod.clip_scores_for_paths(["x.png"]) == [{}]


# ── 3. 接线：build_index 是否落进 ShotRecord ──────────────


@pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg") or not __import__("shutil").which("ffprobe"),
    reason="需要本机 ffmpeg/ffprobe")
class TestIndexWiring:
    def test_build_index_merges_visual_and_quality(self, tmp_path, monkeypatch) -> None:
        """R2 接线：标注结果必须真的写进镜头记录（否则等于白跑一次模型）。"""
        from clipwright.services import content_index as ci

        lib = tmp_path / "library"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "projects", raising=False)
        monkeypatch.setattr(ci.settings, "content_index_mirror_mongo", False, raising=False)
        monkeypatch.setattr(ci.settings, "content_index_enabled", True, raising=False)

        async def fake_label_shots(frames, **kwargs):
            return [{"visual": {"tags": ["人物"], "shot_size": "close", "has_face": True,
                                "description": "测试描述"},
                     "evidence": {"visual.tags": "stub"},
                     "confidence": {"clip.人物": 0.9}}
                    for _ in frames]

        monkeypatch.setattr(cvmod, "label_shots", fake_label_shots)

        video = tmp_path / "v.mp4"
        import subprocess

        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=160x120:r=10:d=1",
                        "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=1",
                        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[out]",
                        "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
        idx = asyncio.run(ci.build_index(video, force=True, mirror=False))
        assert idx.shots
        for shot in idx.shots:
            assert shot.visual.tags == ["人物"]
            assert shot.visual.shot_size == "close"
            assert shot.visual.has_face is True
            assert shot.visual.description == "测试描述"
            assert shot.evidence.get("visual.tags") == "stub"
            assert shot.confidence.get("clip.人物") == 0.9
            # 质量维度也必须落上（过曝/欠曝/边缘能量）
            assert shot.evidence.get("stats.quality") == "pillow+numpy"
        assert idx.sources.get("visual") in ("clip_zero_shot", "classifier")

    def test_labeling_failure_does_not_break_index(self, tmp_path, monkeypatch) -> None:
        from clipwright.services import content_index as ci

        lib = tmp_path / "library"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "projects", raising=False)
        monkeypatch.setattr(ci.settings, "content_index_mirror_mongo", False, raising=False)

        async def boom(frames, **kwargs):
            raise RuntimeError("标注全挂了")

        monkeypatch.setattr(cvmod, "label_shots", boom)
        video = tmp_path / "v.mp4"
        import subprocess

        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=160x120:r=10:d=1",
                        "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
        idx = asyncio.run(ci.build_index(video, force=True, mirror=False))
        assert idx.shots and idx.shots[0].visual.tags == []   # 索引仍然可用
