"""STT 服务缺陷回归测试 — B8/B9/B10。

- B8: whisper 模型按 model_size 分键缓存，避免复用错误尺寸的模型
- B9: faster-whisper 模型按 model_size 缓存，避免每次调用重新加载
- B10: FFmpeg 音频提取失败时以 warning 级别记录日志

通过向 sys.modules 注入假 whisper / faster_whisper 模块、mock subprocess.run
来隔离重依赖，不加载任何真实模型。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipwright.services.stt import STTService


class _FakeWhisperModel:
    """假 whisper 模型：每次 load 记录 model_size，transcribe 返回固定结果。"""

    loaded_sizes: list[str] = []
    instances: list["_FakeWhisperModel"] = []
    used_instances: list["_FakeWhisperModel"] = []

    def __init__(self, model_size: str) -> None:
        _FakeWhisperModel.loaded_sizes.append(model_size)
        _FakeWhisperModel.instances.append(self)
        self.model_size = model_size

    def transcribe(self, audio_path: str, **opts) -> dict:
        _FakeWhisperModel.used_instances.append(self)
        return {"segments": [], "text": "", "language": "en", "duration": 1.0}


class _FakeFasterWhisperModel:
    """假 faster-whisper 模型：每次构造记录 model_size。"""

    constructed_sizes: list[str] = []

    def __init__(self, model_size: str, device: str = "cpu", compute_type: str = "int8") -> None:
        _FakeFasterWhisperModel.constructed_sizes.append(model_size)
        self.model_size = model_size

    def transcribe(self, audio_path: str, language=None, word_timestamps: bool = False):
        seg = SimpleNamespace(start=0.0, end=1.0, text="hi", avg_logprob=-0.1)
        info = SimpleNamespace(language="en", duration=1.0)
        return [seg], info


def _install_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("whisper")
    mod.load_model = _FakeWhisperModel
    monkeypatch.setitem(sys.modules, "whisper", mod)


def _install_faster_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = _FakeFasterWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


class TestB8WhisperCachePerSize:
    def test_different_sizes_load_distinct_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tiny 与 large 应加载两个不同模型对象。"""
        _install_whisper(monkeypatch)
        _FakeWhisperModel.loaded_sizes.clear()
        _FakeWhisperModel.instances.clear()

        svc = STTService()
        r1 = svc._transcribe_whisper("a.wav", "", "tiny", True)
        r2 = svc._transcribe_whisper("a.wav", "", "large", True)

        assert r1.success and r2.success
        assert _FakeWhisperModel.loaded_sizes == ["tiny", "large"]
        assert _FakeWhisperModel.instances[0] is not _FakeWhisperModel.instances[1]

    def test_same_size_reuses_cached_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """两个尺寸各加载一次后，重复 tiny 应复用首个缓存对象（load 数仍为 2）。"""
        _install_whisper(monkeypatch)
        _FakeWhisperModel.loaded_sizes.clear()
        _FakeWhisperModel.used_instances.clear()

        svc = STTService()
        svc._transcribe_whisper("a.wav", "", "tiny", True)
        svc._transcribe_whisper("a.wav", "", "large", True)
        svc._transcribe_whisper("a.wav", "", "tiny", True)

        assert len(_FakeWhisperModel.loaded_sizes) == 2
        assert _FakeWhisperModel.used_instances[0] is _FakeWhisperModel.used_instances[2]

    def test_cache_keyed_by_model_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_models 缓存字典应按 model_size 分键。"""
        _install_whisper(monkeypatch)
        _FakeWhisperModel.loaded_sizes.clear()

        svc = STTService()
        svc._transcribe_whisper("a.wav", "", "tiny", True)
        svc._transcribe_whisper("a.wav", "", "large", True)

        assert len(svc._models) == 2
        assert set(svc._models) == {"tiny", "large"}


class TestB9FasterWhisperCache:
    def test_same_size_loads_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """相同 model_size 连续两次调用应只构造一次模型并复用。"""
        _install_faster_whisper(monkeypatch)
        _FakeFasterWhisperModel.constructed_sizes.clear()

        svc = STTService()
        r1 = svc._transcribe_faster_whisper("a.wav", "", "base", True)
        r2 = svc._transcribe_faster_whisper("a.wav", "", "base", True)

        assert r1.success and r2.success
        assert _FakeFasterWhisperModel.constructed_sizes == ["base"]

    def test_different_sizes_load_once_each(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不同 model_size 各构造一次并分别缓存。"""
        _install_faster_whisper(monkeypatch)
        _FakeFasterWhisperModel.constructed_sizes.clear()

        svc = STTService()
        svc._transcribe_faster_whisper("a.wav", "", "small", True)
        svc._transcribe_faster_whisper("a.wav", "", "medium", True)

        assert _FakeFasterWhisperModel.constructed_sizes == ["small", "medium"]
        assert len(svc._faster_models) == 2


class TestB10EnsureAudioWarning:
    def _video(self, tmp_path) -> Path:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00" * 10)
        return video

    def test_ffmpeg_failure_logs_warning(
        self, tmp_path, caplog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ffmpeg 返回非零退出码时，应记录包含 stderr 片段的 warning。"""
        video = self._video(tmp_path)

        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stderr="Invalid data found when processing input",
                stdout="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        svc = STTService()
        with caplog.at_level(logging.WARNING):
            path = svc._ensure_audio(video)

        assert path.endswith(".wav")
        assert any("FFmpeg 音频提取失败" in r.message for r in caplog.records)
        assert any("Invalid data" in r.message for r in caplog.records)

    def test_ffmpeg_success_no_warning(
        self, tmp_path, caplog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ffmpeg 成功时不记录 warning。"""
        video = self._video(tmp_path)

        def fake_run(cmd, **kwargs):
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        svc = STTService()
        with caplog.at_level(logging.WARNING):
            path = svc._ensure_audio(video)

        assert path.endswith(".wav")
        assert not any("FFmpeg 音频提取失败" in r.message for r in caplog.records)
