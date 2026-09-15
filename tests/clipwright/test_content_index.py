"""R1：素材内容索引（镜头级）测试。

分三组：
1. **纯函数**（无 ffmpeg、无 IO）：内容寻址、模型指纹、镜头区间合并/上限、场景时间解析、
   廉价统计与运动分类；
2. **存储与缓存**：原子写、损坏容忍、版本不符重建、指纹不符重建、缓存命中；
3. **端到端**（需要 ffmpeg）：真实合成视频的切分 → 镜头 → 统计 → 落盘 → 缓存命中 → 增量。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.schema.content import (
    INDEX_VERSION,
    ContentIndex,
    ShotRecord,
    ShotStats,
    cache_key,
    format_shot_ref,
    model_fingerprint,
    parse_shot_ref,
)
from clipwright.services import content_index as ci

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="需要本机 ffmpeg/ffprobe")


# ── 1. 纯函数 ─────────────────────────────────────────────


class TestContentAddressing:
    def test_format_and_parse_roundtrip(self) -> None:
        ref = format_shot_ref("ab" * 32, 7)
        assert ref == "asset:abababababab#s7"
        assert parse_shot_ref(ref) == ("abababababab", 7)

    def test_parse_accepts_long_and_short_sha(self) -> None:
        assert parse_shot_ref("asset:0123456789abcdef#s0") == ("0123456789abcdef", 0)
        assert parse_shot_ref("asset:01234567#s12") == ("01234567", 12)

    @pytest.mark.parametrize("bad", [
        "", "asset:zzzz#s1", "asset:ab#s1", "abab#s1", "asset:abababababab",
        "asset:abababababab#s-1", "asset:abababababab#x1", "asset:0123456#s1", None,
    ])
    def test_parse_rejects_garbage(self, bad) -> None:
        assert parse_shot_ref(bad) is None  # type: ignore[arg-type]

    def test_format_requires_enough_entropy(self) -> None:
        with pytest.raises(ValueError):
            format_shot_ref("abc", 0)

    def test_case_normalized(self) -> None:
        assert format_shot_ref("AB" * 32, 3) == "asset:abababababab#s3"
        assert parse_shot_ref("asset:ABABABABABAB#s3") == ("abababababab", 3)


class TestFingerprint:
    def test_stable_for_same_models(self) -> None:
        a = model_fingerprint(vision="vit-base", asr="whisper-small")
        b = model_fingerprint(asr="whisper-small", vision="vit-base")  # 顺序无关
        assert a == b

    def test_changes_when_model_changes(self) -> None:
        assert model_fingerprint(vision="vit-base") != model_fingerprint(vision="clip-vit")

    def test_empty_is_stable(self) -> None:
        assert model_fingerprint(vision="", asr="") == model_fingerprint()

    def test_cache_key_changes_on_each_component(self) -> None:
        base = cache_key("sha", INDEX_VERSION, "fp")
        assert base != cache_key("sha2", INDEX_VERSION, "fp")
        assert base != cache_key("sha", INDEX_VERSION + 1, "fp")
        assert base != cache_key("sha", INDEX_VERSION, "fp2")


class TestShotRanges:
    def test_no_cuts_is_one_shot(self) -> None:
        assert ci.build_shot_ranges([], 10.0) == [(0.0, 10.0)]

    def test_cuts_split_evenly(self) -> None:
        assert ci.build_shot_ranges([2.0, 4.0], 6.0) == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]

    def test_cuts_sorted_and_deduped(self) -> None:
        assert ci.build_shot_ranges([4.0, 2.0, 4.0, 2.0], 6.0) == [
            (0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]

    def test_out_of_range_cuts_dropped(self) -> None:
        assert ci.build_shot_ranges([-1.0, 0.0, 10.0, 20.0], 10.0) == [(0.0, 10.0)]

    def test_zero_duration_is_empty(self) -> None:
        assert ci.build_shot_ranges([1.0], 0.0) == []

    def test_short_shot_merged_into_previous(self) -> None:
        """0.2s 的碎片（转场/闪帧）应并入前一个镜头，而不是单独成镜。"""
        rng = ci.build_shot_ranges([1.0, 1.2], 4.0, min_shot_sec=0.3)
        assert rng == [(0.0, 1.2), (1.2, 4.0)]

    def test_leading_short_shot_merged_into_next(self) -> None:
        rng = ci.build_shot_ranges([0.1], 4.0, min_shot_sec=0.3)
        assert rng == [(0.0, 4.0)]

    def test_all_short_collapses_to_one(self) -> None:
        rng = ci.build_shot_ranges([0.1, 0.2, 0.3], 0.4, min_shot_sec=0.3)
        assert rng == [(0.0, 0.4)]

    def test_max_shots_keeps_longest_deterministically(self) -> None:
        # 10 个 1s 镜头 + 1 个 5s 镜头 → 上限 3 时保留最长的三个
        cuts = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        rng = ci.build_shot_ranges(cuts, 15.0, min_shot_sec=0.1, max_shots=3)
        assert len(rng) == 3
        assert rng[-1] == (10.0, 15.0)          # 5s 的最长镜头
        assert rng == sorted(rng)               # 仍按时间排序

    def test_ranges_are_contiguous(self) -> None:
        rng = ci.build_shot_ranges([1.0, 2.0, 5.0], 8.0)
        for a, b in zip(rng, rng[1:]):
            assert a[1] == b[0]

    def test_float_dedup_tolerates_precision_noise(self) -> None:
        rng = ci.build_shot_ranges([2.0, 2.0004], 6.0, min_shot_sec=0.1)
        assert len(rng) == 2  # 2.0004 与 2.0 同为一位小数 → 去重


class TestParseSceneTimes:
    def test_extracts_pts_time(self) -> None:
        stderr = ("[Parsed_showinfo_1 @ 0x1] n:0 pts:100 pts_time:1.25 duration:1\n"
                  "[Parsed_showinfo_1 @ 0x1] n:1 pts:200 pts_time:2.5 duration:1\n")
        assert ci.parse_scene_times(stderr) == [1.25, 2.5]

    def test_no_match_is_empty(self) -> None:
        assert ci.parse_scene_times("no timestamps here") == []
        assert ci.parse_scene_times("") == []


def _png(path: Path, color: tuple[int, int, int], size=(32, 32)) -> str:
    from PIL import Image

    Image.new("RGB", size, color).save(path)
    return str(path)


def _noisy_png(path: Path, base: int, size=(32, 32)) -> str:
    import numpy as np
    from PIL import Image

    arr = np.full((size[1], size[0], 3), base, dtype="uint8")
    rng = np.random.default_rng(7)
    arr = np.clip(arr.astype("int16") + rng.integers(-40, 40, arr.shape), 0, 255).astype("uint8")
    Image.fromarray(arr).save(path)
    return str(path)


class TestCheapStats:
    def test_empty_input(self) -> None:
        stats = ci.compute_shot_stats([])
        assert stats.frame_count == 0 and stats.brightness == 0.0

    def test_black_frame_detected(self, tmp_path: Path) -> None:
        stats = ci.compute_shot_stats([_png(tmp_path / "b.png", (0, 0, 0))])
        assert stats.is_black and not stats.is_white

    def test_white_frame_detected(self, tmp_path: Path) -> None:
        stats = ci.compute_shot_stats([_png(tmp_path / "w.png", (255, 255, 255))])
        assert stats.is_white and not stats.is_black

    def test_gray_is_neither(self, tmp_path: Path) -> None:
        stats = ci.compute_shot_stats([_png(tmp_path / "g.png", (128, 128, 128))])
        assert not stats.is_black and not stats.is_white
        assert stats.brightness == pytest.approx(128, abs=1)

    def test_noise_has_more_sharpness_than_flat(self, tmp_path: Path) -> None:
        flat = ci.compute_shot_stats([_png(tmp_path / "f.png", (120, 120, 120))])
        noisy = ci.compute_shot_stats([_noisy_png(tmp_path / "n.png", 120)])
        assert noisy.sharpness > flat.sharpness
        assert flat.sharpness == pytest.approx(0.0, abs=1e-6)

    def test_motion_from_frame_difference(self, tmp_path: Path) -> None:
        a = _png(tmp_path / "1.png", (0, 0, 0))
        b = _png(tmp_path / "2.png", (255, 255, 255))
        stats = ci.compute_shot_stats([a, b])
        assert stats.motion == pytest.approx(255.0, abs=1.0)

    def test_single_frame_motion_is_zero_and_unknown(self, tmp_path: Path) -> None:
        """单帧无法测运动 → 记 0 且分类 unknown（诚实缺省，不假装静止）。"""
        stats = ci.compute_shot_stats([_noisy_png(tmp_path / "n.png", 100)])
        assert stats.motion == 0.0 and stats.frame_count == 1
        assert ci.classify_motion(stats) == "unknown"

    def test_saturation_measured(self, tmp_path: Path) -> None:
        gray = ci.compute_shot_stats([_png(tmp_path / "g.png", (128, 128, 128))])
        red = ci.compute_shot_stats([_png(tmp_path / "r.png", (255, 0, 0))])
        assert red.saturation > gray.saturation

    @pytest.mark.parametrize("motion,expect", [
        (0.0, "static"), (1.9, "static"), (2.0, "low"), (7.9, "low"),
        (8.0, "medium"), (19.9, "medium"), (20.0, "high"), (99.0, "high"),
    ])
    def test_motion_classification_boundaries(self, motion: float, expect: str) -> None:
        stats = ShotStats(motion=motion, frame_count=2)
        assert ci.classify_motion(stats) == expect

    def test_black_shot_motion_unknown(self) -> None:
        stats = ShotStats(motion=50.0, frame_count=2, is_black=True)
        assert ci.classify_motion(stats) == "unknown"


class TestIndexModel:
    def _index(self, n: int = 3) -> ContentIndex:
        shots = [
            ShotRecord(index=i, t0=float(i), t1=float(i + 1),
                       visual={"description": f"镜头{i}", "tags": ["a"] if i == 1 else []},
                       speech={"text": "hello" if i == 2 else ""})
            for i in range(n)
        ]
        return ContentIndex(sha256="ab" * 32, filename="a.mp4", duration_sec=float(n),
                            width=1920, height=1080, shots=shots)

    def test_shot_record_computes_duration(self) -> None:
        s = ShotRecord(index=0, t0=1.0, t1=2.5)
        assert s.duration_sec == pytest.approx(1.5)

    def test_shot_at_binary_search(self) -> None:
        idx = self._index(3)
        assert idx.shot_at(0.5).index == 0
        assert idx.shot_at(1.0).index == 1     # 边界属于后一个镜头
        assert idx.shot_at(2.99).index == 2
        assert idx.shot_at(3.0) is None
        assert idx.shot_at(-1.0) is None

    def test_summary_counts(self) -> None:
        s = self._index(3).summary()
        assert s["shot_count"] == 3
        assert s["shots_with_speech"] == 1
        assert s["shots_with_description"] == 3
        assert s["avg_shot_sec"] == pytest.approx(1.0)

    def test_summary_text_is_compact(self) -> None:
        text = self._index(3).summary_text()
        assert "3 个镜头" in text and "镜头样本" in text
        assert len(text.splitlines()) <= 3 + 3

    def test_summary_sample_limited(self) -> None:
        s = self._index(9).summary(max_shots=2)
        assert len(s["sample_shots"]) == 2

    def test_coverage_ratios(self) -> None:
        cov = self._index(4).coverage()
        assert cov["shots"] == 4
        assert cov["speech"] == pytest.approx(0.25)
        assert cov["visual_tags"] == pytest.approx(0.25)

    def test_text_for_embedding_merges_fields(self) -> None:
        text = self._index(3).shots[1].text_for_embedding()
        assert "镜头1" in text and "a" in text and "hello" not in text

    def test_served_from_cache_not_serialized(self) -> None:
        idx = self._index(1)
        idx.served_from_cache = True
        assert "served_from_cache" not in idx.model_dump()

    def test_mongo_doc_has_search_text_and_id(self) -> None:
        doc = self._index(3).to_mongo_doc()
        assert doc["_id"] == "ab" * 32
        assert "镜头0" in doc["search_text"] and "hello" in doc["search_text"]

    def test_brief_marks_black_and_motion(self) -> None:
        s = ShotRecord(index=0, t0=0.0, t1=2.0,
                       visual={"camera_motion": "high"}, stats={"is_black": True})
        brief = s.brief()
        assert "黑场" in brief and "motion:high" in brief


# ── 2. 存储与缓存 ─────────────────────────────────────────


@pytest.fixture()
def isolated_library(tmp_path, monkeypatch):
    """把素材库/项目目录指到 tmp，避免污染真实数据。"""
    lib = tmp_path / "library"
    proj = tmp_path / "projects"
    lib.mkdir()
    proj.mkdir()
    monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
    monkeypatch.setattr(ci.settings, "project_dir", proj, raising=False)
    return lib, proj


class TestStore:
    def test_save_then_load_roundtrip(self, isolated_library) -> None:
        idx = ContentIndex(sha256="cd" * 32, filename="x.mp4",
                           shots=[ShotRecord(index=0, t0=0.0, t1=1.0)])
        path = ci.save_index(idx)
        assert path.exists() and path.name == "cd" * 32 + ".json"
        back = ci.load_index("cd" * 32)
        assert back is not None and back.filename == "x.mp4"
        assert back.created_at and back.updated_at

    def test_index_is_content_addressed_across_projects(self, isolated_library) -> None:
        """同 sha 在不同项目目录下仍是各自的索引文件，但路径可预测（内容寻址）。"""
        lib, proj = isolated_library
        p1 = ci.index_path("ef" * 32)
        p2 = ci.index_path("ef" * 32, project_id="proj_1")
        assert p1.parent != p2.parent
        assert p1.name == p2.name == "ef" * 32 + ".json"

    def test_load_missing_is_none(self, isolated_library) -> None:
        assert ci.load_index("00" * 32) is None
        assert ci.load_index("") is None

    def test_load_corrupt_is_none_not_raise(self, isolated_library) -> None:
        path = ci.index_path("aa" * 32)
        path.write_bytes(b"\xff\xfe{not json")
        assert ci.load_index("aa" * 32) is None

    def test_version_mismatch_invalidates(self, isolated_library) -> None:
        idx = ContentIndex(sha256="bb" * 32, index_version=INDEX_VERSION - 1,
                           shots=[ShotRecord(index=0, t0=0.0, t1=1.0)])
        ci.save_index(idx)
        assert ci.load_index("bb" * 32) is None

    def test_save_is_atomic_no_tmp_left(self, isolated_library) -> None:
        ci.save_index(ContentIndex(sha256="cc" * 32))
        d = ci.index_dir()
        assert not list(d.glob("*.tmp"))

    def test_freshness_rules(self, isolated_library) -> None:
        idx = ContentIndex(sha256="dd" * 32, model_fingerprint="fp",
                           shots=[ShotRecord(index=0, t0=0.0, t1=1.0)])
        assert ci.index_is_fresh(idx, "dd" * 32, "fp") is True
        assert ci.index_is_fresh(idx, "dd" * 32, "other") is False   # 换模型 → 重建
        assert ci.index_is_fresh(idx, "ee" * 32, "fp") is False      # 换内容 → 重建
        assert ci.index_is_fresh(None, "dd" * 32, "fp") is False
        assert ci.index_is_fresh(ContentIndex(sha256="dd" * 32, model_fingerprint="fp"),
                                 "dd" * 32, "fp") is False           # 无镜头 → 重建

    def test_summarize_index_files(self, isolated_library) -> None:
        ci.save_index(ContentIndex(sha256="11" * 32))
        ci.save_index(ContentIndex(sha256="22" * 32))
        info = ci.summarize_index_files()
        assert info["count"] == 2 and info["total_bytes"] > 0

    def test_sha256_matches_asset_manager_algorithm(self, tmp_path: Path) -> None:
        """与 `asset_manager._sha256_of` 同算法——不然复用它算出的 sha 会错位。"""
        from clipwright.services.asset_manager import _sha256_of

        f = tmp_path / "blob.bin"
        f.write_bytes(b"clipwright" * 1000)
        assert ci.sha256_of(f) == _sha256_of(f)

    def test_sha256_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert ci.sha256_of(tmp_path / "nope.bin") == ""


class TestMongoMirror:
    def test_mirror_skipped_without_connection(self, monkeypatch) -> None:
        import clipwright.context as ctx

        monkeypatch.setattr(ctx.mongo, "_client", None, raising=False)
        monkeypatch.setattr(ctx.mongo, "_db", None, raising=False)
        idx = ContentIndex(sha256="33" * 32, shots=[ShotRecord(index=0, t0=0.0, t1=1.0)])
        assert asyncio.run(ci.mirror_index(idx)) is False
        assert asyncio.run(ci.find_indexes(keyword="x")) == []
        assert asyncio.run(ci.count_indexes()) == 0

    def test_mirror_upserts_and_find_projects(self, monkeypatch) -> None:
        """用一个假集合验证 upsert/检索语义（不依赖真实 Mongo）。"""
        stored: dict[str, dict] = {}

        class _FakeCursor:
            """pymongo Cursor 的最小替身：支持链式 .limit()。"""

            def __init__(self, docs):
                self._docs = docs

            def limit(self, n):
                self._docs = self._docs[:n]
                return self

            def __iter__(self):
                return iter(self._docs)

        class _FakeColl:
            def create_index(self, *a, **k):
                return "idx"

            def replace_one(self, key, doc, upsert=False):
                stored[key["_id"]] = doc

            def find(self, query, projection=None):
                out = []
                for doc in stored.values():
                    if query.get("sha256") and doc.get("sha256") != query["sha256"]:
                        continue
                    if "$or" in query:
                        pat = query["$or"][0]["search_text"]["$regex"]
                        hay = (doc.get("search_text", "") + doc.get("filename", "")).lower()
                        if pat.lower() not in hay:
                            continue
                    out.append(dict(doc))
                if projection:
                    for key, keep in projection.items():
                        if not keep:  # 排除式投影（pymongo 语义）
                            for d in out:
                                d.pop(key, None)
                return _FakeCursor(out)

            def estimated_document_count(self):
                # pymongo 上这是方法；count_indexes 必须按方法调用
                return len(stored)

        class _FakeDb:
            def __getitem__(self, name):
                return _FakeColl()

        import clipwright.context as ctx

        monkeypatch.setattr(ctx.mongo, "_client", object(), raising=False)
        monkeypatch.setattr(ctx.mongo, "_db", _FakeDb(), raising=False)

        idx = ContentIndex(sha256="44" * 32, filename="街拍.mp4", shots=[
            ShotRecord(index=0, t0=0.0, t1=1.0, visual={"description": "咖啡杯特写"}),
        ])
        assert asyncio.run(ci.mirror_index(idx)) is True
        assert asyncio.run(ci.count_indexes()) == 1
        hits = asyncio.run(ci.find_indexes(keyword="咖啡"))
        assert len(hits) == 1 and hits[0]["filename"] == "街拍.mp4"
        assert "shots" not in hits[0] and "_id" not in hits[0]
        assert asyncio.run(ci.find_indexes(sha256="44" * 32))
        assert asyncio.run(ci.find_indexes(keyword="不存在")) == []


# ── 3. 端到端（需要 ffmpeg） ──────────────────────────────


def _make_cut_video(path: Path, colors: list[str], seg_sec: float = 1.0) -> None:
    """合成一段"硬切"视频：每段一种纯色（颜色差异大 → scene 检测必命中）。"""
    inputs: list[str] = []
    for c in colors:
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=160x120:r=10:d={seg_sec}"]
    filters = "".join(f"[{i}:v]" for i in range(len(colors)))
    filters += f"concat=n={len(colors)}:v=1:a=0[out]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", filters, "-map", "[out]",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]


@needs_ffmpeg
class TestEndToEnd:
    def test_probe_media(self, tmp_path: Path) -> None:
        v = tmp_path / "v.mp4"
        _make_cut_video(v, ["red", "blue"])
        meta = asyncio.run(ci.probe_media(v))
        assert meta["width"] == 160 and meta["height"] == 120
        assert meta["duration"] == pytest.approx(2.0, abs=0.3)
        assert meta["fps"] == pytest.approx(10.0, abs=0.1)

    def test_detect_cut_times(self, tmp_path: Path) -> None:
        v = tmp_path / "v.mp4"
        _make_cut_video(v, ["red", "blue", "green", "white"])
        cuts = asyncio.run(ci.detect_cut_times(v, 0.3))
        assert 2 <= len(cuts) <= 3
        assert cuts[0] == pytest.approx(1.0, abs=0.25)

    def test_detect_cut_times_missing_file_is_empty(self) -> None:
        assert asyncio.run(ci.detect_cut_times("no_such_file.mp4", 0.3)) == []

    def test_build_index_produces_shots_and_stats(self, tmp_path: Path, isolated_library) -> None:
        v = tmp_path / "clip.mp4"
        _make_cut_video(v, ["black", "white", "red"], seg_sec=1.0)
        idx = asyncio.run(ci.build_index(v, force=True, mirror=False))
        assert len(idx.shots) >= 3
        assert idx.sha256 and idx.index_version == INDEX_VERSION
        assert idx.model_fingerprint
        # 每个镜头都有抽帧与统计
        assert all(s.stats.frame_count >= 1 for s in idx.shots)
        # 第一段是纯黑 → 黑场判定必须命中（廉价统计的有效性证据）
        assert idx.shots[0].stats.is_black is True
        # 相邻镜头连续覆盖整段
        assert idx.shots[0].t0 == 0.0
        assert idx.shots[-1].t1 == pytest.approx(idx.duration_sec, abs=0.05)
        # 索引落盘
        assert ci.index_path(idx.sha256).exists()

    def test_second_build_hits_cache(self, tmp_path: Path, isolated_library) -> None:
        v = tmp_path / "clip.mp4"
        _make_cut_video(v, ["red", "blue"], seg_sec=1.0)
        first = asyncio.run(ci.build_index(v, force=True, mirror=False))
        assert first.served_from_cache is False
        second = asyncio.run(ci.build_index(v, mirror=False))
        assert second.served_from_cache is True
        assert len(second.shots) == len(first.shots)
        assert second.build_ms == first.build_ms  # 缓存命中即原样返回

    def test_force_rebuild_bypasses_cache(self, tmp_path: Path, isolated_library) -> None:
        v = tmp_path / "clip.mp4"
        _make_cut_video(v, ["red", "blue"], seg_sec=1.0)
        asyncio.run(ci.build_index(v, force=True, mirror=False))
        again = asyncio.run(ci.build_index(v, force=True, mirror=False))
        assert again.served_from_cache is False

    def test_progress_callback_reports_monotonic(self, tmp_path: Path, isolated_library) -> None:
        v = tmp_path / "clip.mp4"
        _make_cut_video(v, ["red", "blue", "green"], seg_sec=1.0)
        seen: list[tuple[float, str]] = []
        asyncio.run(ci.build_index(v, force=True, mirror=False,
                                   progress=lambda p, m: seen.append((p, m))))
        assert seen and seen[-1][0] == 1.0
        assert all(b[0] >= a[0] for a, b in zip(seen, seen[1:])), seen

    def test_keep_frames_false_cleans_frames_dir(self, tmp_path: Path, isolated_library) -> None:
        v = tmp_path / "clip.mp4"
        _make_cut_video(v, ["red", "blue"], seg_sec=1.0)
        idx = asyncio.run(ci.build_index(v, force=True, mirror=False, keep_frames=False))
        assert all(not s.keyframes for s in idx.shots)
        assert not (ci.index_dir() / "frames" / idx.sha256[:12]).exists()

    def test_min_shot_merges_flash_cut(self, tmp_path: Path, isolated_library) -> None:
        """0.2s 的闪白段应被合并（不产生 2 个镜头）。"""
        v = tmp_path / "flash.mp4"
        _make_cut_video(v, ["red", "white", "red"], seg_sec=0.2)
        idx = asyncio.run(ci.build_index(v, force=True, mirror=False,
                                         min_shot_sec=0.5))
        assert len(idx.shots) == 1

    def test_ensure_index_from_asset_like_object(self, tmp_path: Path, isolated_library) -> None:
        v = tmp_path / "clip.mp4"
        _make_cut_video(v, ["red", "blue"], seg_sec=1.0)

        class _Asset:
            asset_id = "asset_test"
            filename = "clip.mp4"
            file_path = str(v)
            media_type = "video"
            sha256 = ""

        idx = asyncio.run(ci.ensure_index(_Asset(), force=True, mirror=False))
        assert idx is not None and idx.asset_id == "asset_test"

    def test_ensure_index_skips_non_video(self, tmp_path: Path) -> None:
        class _Asset:
            file_path = "/tmp/a.png"
            media_type = "image"

        assert asyncio.run(ci.ensure_index(_Asset())) is None

    def test_ensure_index_skips_missing_file(self, tmp_path: Path) -> None:
        class _Asset:
            file_path = str(tmp_path / "ghost.mp4")
            media_type = "video"

        assert asyncio.run(ci.ensure_index(_Asset())) is None

    def test_build_index_missing_file_raises(self, tmp_path: Path, isolated_library) -> None:
        with pytest.raises(FileNotFoundError):
            asyncio.run(ci.build_index(tmp_path / "ghost.mp4", force=True, mirror=False))

    def test_index_survives_json_roundtrip(self, tmp_path: Path, isolated_library) -> None:
        v = tmp_path / "clip.mp4"
        _make_cut_video(v, ["red", "blue"], seg_sec=1.0)
        idx = asyncio.run(ci.build_index(v, force=True, mirror=False))
        raw = json.loads(ci.index_path(idx.sha256).read_text(encoding="utf-8"))
        assert raw["served_from_cache"] is False if "served_from_cache" in raw else True
        back = ci.load_index(idx.sha256)
        assert back is not None
        assert back.shots[0].stats.brightness == idx.shots[0].stats.brightness
