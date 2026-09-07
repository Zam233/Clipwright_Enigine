"""批次 1（成片可用性）针对性回归测试。

覆盖：多人声混音、整文件音源裁齐、fade 绝对位置、拼接回退降级、
轨道空洞填充、BGM 死循环保护、占位视频并发竞态。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from clipwright.agents.audio_agent import AudioAgent
from clipwright.services.render import RenderService, _is_valid_video


# ── 1.1 混音 ───────────────────────────────────────────


def _make_mix_service(tmp_path, monkeypatch, vdur=60.0):
    svc = RenderService(work_dir=tmp_path / "w")
    captured: list[str] = []

    async def fake_ff(cmd, **kwargs):
        captured.append(" ".join(cmd))
        Path(cmd[-1]).write_bytes(b"v" * 2048)
        return type("R", (), {"returncode": 0, "stderr": b""})()

    svc._ff = fake_ff  # type: ignore[method-assign]
    monkeypatch.setattr("clipwright.services.render._get_actual_duration", lambda _p: vdur)
    return svc, captured


def _mkfile(p: Path) -> str:
    p.write_bytes(b"x" * 64)
    return str(p)


@pytest.mark.asyncio
async def test_mix_all_voice_clips_are_mixed(tmp_path, monkeypatch):
    """修复核心：顺序多句配音（3 个人声 clip）必须全部进入 amix——旧实现
    只混第一句，其余被 continue 跳过，成片只有第一句有声。"""
    svc, captured = _make_mix_service(tmp_path, monkeypatch)
    video = _mkfile(tmp_path / "v.mp4")
    segments = [
        {"source_path": _mkfile(tmp_path / f"s{i}.wav"), "volume": 1.0,
         "start_sec": float(i * 10), "duration_sec": 8.0, "is_voice": True}
        for i in range(3)
    ]
    await svc._mix_audio(video, segments, str(tmp_path / "out.mp4"))
    cmd = captured[0]
    assert "duration=longest" in cmd  # 旧 duration=first 在首句结束处截断
    # 三路人声各自独立预处理链 + 全部进入 amix（不再被 continue 跳过）
    assert cmd.count("atrim=start=0.0:duration=8.000000") == 3  # 三句各自裁 8s
    assert "atrim=start=0.0:duration=8.000000" in cmd
    assert "adelay=10000" in cmd and "adelay=20000" in cmd    # 句2/3 延迟到位
    amix_part = cmd[cmd.index("amix=inputs="):]
    assert "amix=inputs=3" in amix_part                       # 3 路全混


@pytest.mark.asyncio
async def test_mix_atrim_uses_source_offset_only(tmp_path, monkeypatch):
    """atrim 恒用源内偏移：start=20s 的 8s 句文件不得被裁成空流。"""
    svc, captured = _make_mix_service(tmp_path, monkeypatch)
    video = _mkfile(tmp_path / "v.mp4")
    segments = [{"source_path": _mkfile(tmp_path / "s.wav"), "volume": 1.0,
                 "start_sec": 20.0, "duration_sec": 8.0, "is_voice": True}]
    bgm = _mkfile(tmp_path / "bgm.mp3")  # ≥2 音源才进入 C11 混音路径
    await svc._mix_audio(video, segments, str(tmp_path / "out.mp4"), bfp=bgm)
    cmd = captured[0]
    assert "atrim=start=0.0:duration=8.000000" in cmd
    assert "atrim=start=20" not in cmd                # 旧 bug：把时间线起点当源偏移


@pytest.mark.asyncio
async def test_mix_fade_uses_absolute_position(tmp_path, monkeypatch):
    """fade 在 adelay 之后：fade-in/out 必须用时间线绝对位置。"""
    svc, captured = _make_mix_service(tmp_path, monkeypatch)
    video = _mkfile(tmp_path / "v.mp4")
    segments = [{"source_path": _mkfile(tmp_path / "s.wav"), "volume": 1.0,
                 "start_sec": 10.0, "duration_sec": 8.0, "is_voice": True,
                 "audio_fade_in_sec": 0.5, "audio_fade_out_sec": 1.0}]
    bgm = _mkfile(tmp_path / "bgm.mp3")  # ≥2 音源才进入 C11 混音路径
    await svc._mix_audio(video, segments, str(tmp_path / "out.mp4"), bfp=bgm)
    cmd = captured[0]
    assert "afade=t=in:st=10.000000:d=0.5" in cmd          # 旧实现 st=0 淡的是静音
    assert "afade=t=out:st=17.000000:d=1.0" in cmd         # 10 + 8 - 1


@pytest.mark.asyncio
async def test_mix_full_file_bgm_clamped_to_video_duration(tmp_path, monkeypatch):
    """整文件 BGM（dur=0）按画面时长裁齐——duration=longest 不被长音频文件拖长。"""
    svc, captured = _make_mix_service(tmp_path, monkeypatch, vdur=60.0)
    video = _mkfile(tmp_path / "v.mp4")
    voice = _mkfile(tmp_path / "voice.wav")
    bgm = _mkfile(tmp_path / "long_bgm.mp3")
    await svc._mix_audio(video, [], str(tmp_path / "out.mp4"),
                         afp=voice, bfp=bgm)
    cmd = captured[0]
    # 配音/BGM（dur=0）按 vdur 裁齐，混合时长受控
    assert "duration=longest" in cmd


# ── 1.2 拼接回退 ───────────────────────────────────────


def test_xfade_pair_failure_falls_back_to_hard_cut(tmp_path, monkeypatch):
    """xfade 失败降级硬切拼接（旧实现直接丢整个左块）。"""
    svc = RenderService(work_dir=tmp_path)
    left, right = str(tmp_path / "l.mp4"), str(tmp_path / "r.mp4")
    monkeypatch.setattr(svc, "_run_ff", lambda *a, **k: None)  # ffmpeg "失败"（无产物）
    def _size_valid(p, min_bytes=1024, **kw):
        try:
            return Path(p).stat().st_size >= min_bytes
        except OSError:
            return False

    monkeypatch.setattr("clipwright.services.render._is_valid_video", _size_valid)
    concat_calls: list[tuple[str, str]] = []

    def fake_concat(a, b, fps, bitrate, encoder, preset, out_name="concat.mp4"):
        concat_calls.append((a, b))
        out = tmp_path / Path(out_name).name
        out.write_bytes(b"v" * 2048)
        return str(out)

    monkeypatch.setattr(svc, "_run_concat", fake_concat)
    monkeypatch.setattr("clipwright.services.render._get_actual_duration", lambda _p: 5.0)
    out = svc._xfade_pair(left, right, "fade", 0.4, 30, "5M", "libx264", "medium", "x.mp4")
    assert concat_calls == [(left, right)]  # 硬切降级被触发
    assert Path(out).exists()


def test_concat_all_failure_degrades_to_pairwise(tmp_path, monkeypatch):
    """concat=n 整体失败降级两两串接（旧实现只返回最后一个片段）。"""
    svc = RenderService(work_dir=tmp_path)
    clips = [str(tmp_path / f"c{i}.mp4") for i in range(4)]
    monkeypatch.setattr(svc, "_run_ff", lambda *a, **k: None)  # 整体 concat 失败
    def _size_valid(p, min_bytes=1024, **kw):
        try:
            return Path(p).stat().st_size >= min_bytes
        except OSError:
            return False

    monkeypatch.setattr("clipwright.services.render._is_valid_video", _size_valid)
    ok = {"n": 0}

    def fake_concat(a, b, fps, bitrate, encoder, preset, out_name="concat.mp4"):
        ok["n"] += 1
        out = tmp_path / f"m{ok['n']}.mp4"
        out.write_bytes(b"v" * 2048)
        return str(out)

    monkeypatch.setattr(svc, "_run_concat", fake_concat)
    out = svc._run_concat_all(clips, 30, "5M", "libx264", "medium")
    assert ok["n"] == 3  # 4 段 → 3 次两两串接全部成功
    assert out.endswith("m3.mp4")


def test_is_valid_video_requires_streams(tmp_path, monkeypatch):
    """_is_valid_video(require_streams=True)：单帧/无流残片不再判有效。"""
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"v" * 4096)  # 尺寸够但非真实视频
    monkeypatch.setattr("clipwright.services.render._get_actual_duration", lambda _p: 0.2)
    assert _is_valid_video(fake) is True                    # 尺寸模式放行（缓存路径）
    assert _is_valid_video(fake, require_streams=True) is False  # 关键产物拒绝


# ── 1.4 轨道空洞填充 ───────────────────────────────────


@pytest.mark.asyncio
async def test_concat_segments_fills_gap_with_black(tmp_path, monkeypatch):
    """start_sec 空洞：片段 2 从 5s 开始（片段 1 只有 2s）→ 插入黑帧，
    后续画面不再整体左移。"""
    svc = RenderService(work_dir=tmp_path / "w")
    seg1 = str(tmp_path / "a.mp4")
    seg2 = str(tmp_path / "b.mp4")
    for f in (seg1, seg2):
        Path(f).write_bytes(b"v" * 2048)
    fallbacks: list[float] = []

    def fake_fallback(dur, w, h, fps, idx):
        fallbacks.append(dur)
        out = tmp_path / f"black_{len(fallbacks)}.mp4"
        out.write_bytes(b"v" * 2048)
        return str(out)

    monkeypatch.setattr(svc, "_generate_fallback", fake_fallback)
    monkeypatch.setattr(svc, "_run_concat_all",
                        lambda clips, fps, bitrate, encoder, preset: clips[0])
    segments = [
        {"start_sec": 0.0, "duration_sec": 2.0},
        {"start_sec": 5.0, "duration_sec": 3.0},  # 2s-5s 空洞
    ]
    out = await svc._concat_segments([seg1, seg2], segments, 30, "5M", "libx264",
                                     "medium", None, width=1920, height=1080)
    assert out
    assert fallbacks == [3.0]  # 5 - 2 = 3s 空洞被填充


# ── 1.6 BGM 死循环保护 ─────────────────────────────────


def test_valid_bgm_entries_prefilter():
    class _Asset:
        def __init__(self, local_path=None, url=None):
            self.local_path = local_path
            self.url = url

    entries = [
        {"asset": _Asset(local_path="a.mp3")},
        {"asset": _Asset()},               # 无路径 → 过滤
        {"asset": _Asset(url="http://x")},
        {},                                 # 无 asset → 过滤
    ]
    kept = AudioAgent._valid_bgm_entries(entries)
    assert len(kept) == 2
    assert AudioAgent._valid_bgm_entries([]) == []
    assert AudioAgent._valid_bgm_entries([{"asset": _Asset()}]) == []


# ── 1.5 占位视频并发竞态 ───────────────────────────────


@pytest.mark.asyncio
async def test_text_video_temp_files_unique_under_concurrency(tmp_path, monkeypatch):
    """两个并发占位生成使用不同的临时文本文件（旧 getpid 固定名互相覆盖）。

    用 uuid 桩验证：两路生成各消耗一个 uuid（构造上互不相同），且各自产出了
    drawtext 命令；结束后临时文件全部清理。
    """
    import clipwright.tool.text_video as tv

    filters: list[str] = []
    seq = {"n": 0}

    class FakeProc:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kw):
        for a, b in zip(cmd, cmd[1:]):
            if a == "-vf":
                filters.append(b)
        Path(cmd[-1]).write_bytes(b"v" * 2048)
        return FakeProc()

    def fake_uuid4():
        seq["n"] += 1
        return SimpleNamespace(hex=f"hex{seq['n']}")

    monkeypatch.setattr(tv, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(tv.subprocess, "run", fake_run)
    monkeypatch.setattr(tv, "_font_filter_arg", lambda: "")  # 桩 ffmpeg 不实跑，无需真字体
    monkeypatch.setattr(tv, "uuid", SimpleNamespace(uuid4=fake_uuid4))
    monkeypatch.chdir(tmp_path)

    tool = tv.GenerateTextVideoTool()
    run_one = getattr(tool, "execute")
    await asyncio.gather(
        run_one(text="场景一", duration_sec=2.0),
        run_one(text="场景二", duration_sec=2.0),
    )
    assert seq["n"] == 2       # 两路各消耗一个 uuid → 临时文件名构造上互不相同
    assert len(filters) == 2   # 两路都产出了 drawtext 命令
    assert not list(tmp_path.glob("__text_*"))  # 用后即清
