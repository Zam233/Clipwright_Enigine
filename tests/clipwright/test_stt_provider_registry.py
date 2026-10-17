"""M5 测试：STT provider 注册表化（B-2 后半，全部打桩不触真实推理）。"""
from __future__ import annotations

import asyncio

import pytest

from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
from clipwright.providers.stt import (
    SttBackendEntry,
    ensure_stt_providers_registered,
    resolve_stt_backends,
)


class _FakeSTTResult:
    def __init__(self, success: bool, text: str = "") -> None:
        self.success = success
        self.text = text


class _FakeService:
    """替身 STTService：后端调用全部可控。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _transcribe_whisper(self, audio_file, language, model_size, word_timestamps):
        self.calls.append("whisper")
        return _FakeSTTResult(False)

    def _transcribe_faster_whisper(self, audio_file, language, model_size, word_timestamps):
        self.calls.append("faster-whisper")
        return _FakeSTTResult(True, text="ok")

    def _get_duration(self, path):
        return 1.0


@pytest.fixture(autouse=True)
def _clean():
    yield
    ContributionRegistry.clear_all()


class TestSttRegistry:
    def test_builtin_registered_and_order(self):
        ensure_stt_providers_registered()
        names = [c.id for _, c in ContributionRegistry.list(ContributionKind.STT_PROVIDER)]
        assert set(names) == {"whisper", "faster-whisper"}
        # 尝试顺序与旧硬编码一致：whisper → faster-whisper
        ordered = [n for n, _ in resolve_stt_backends(_FakeService())]
        assert ordered[:2] == ["whisper", "faster-whisper"]

    def test_fallback_order_when_first_fails(self):
        """第一个后端失败 → 走第二个（与旧行为的回落链一致）。"""
        from clipwright.services.stt import STTService

        svc = _FakeService()
        backends = resolve_stt_backends(svc)
        assert backends, "注册表解析失败"

        result = asyncio.run(_run_chain(svc, backends))
        assert svc.calls == ["whisper", "faster-whisper"]
        assert result.success is True

    def test_plugin_provider_appended_after_builtins(self):
        """插件贡献的 STT provider 排在两个内置后端之后（确定性）。"""
        ensure_stt_providers_registered()
        ContributionRegistry.register("stt_plug", [
            ContributionWrapper("zzz-custom-stt").contribution(),
        ], dispatch=False)
        ordered = [n for n, _ in resolve_stt_backends(_FakeService())]
        assert ordered == ["whisper", "faster-whisper", "zzz-custom-stt"]

    def test_transcribe_any_uses_registry(self, monkeypatch):
        """_transcribe_any 遍历注册表；两个内置都不成功 → 保底失败结果。"""
        from clipwright.services.stt import STTService

        class _AllFailService(STTService):
            def __init__(self) -> None:
                self.calls = []

            def _transcribe_whisper(self, *a, **k):
                self.calls.append("whisper")
                return _FakeSTTResult(False)

            def _transcribe_faster_whisper(self, *a, **k):
                self.calls.append("faster-whisper")
                return _FakeSTTResult(False)

            def _get_duration(self, path):
                return 2.5

        svc = _AllFailService()
        result = asyncio.run(svc._transcribe_any("x.wav", "zh", "small", False))
        assert svc.calls == ["whisper", "faster-whisper"]
        assert result.success is False
        assert result.duration_sec == 2.5


async def _run_chain(svc, backends):
    """复刻 _transcribe_any 的遍历语义（不触真实实现）。"""
    for name, fn in backends:
        try:
            result = await asyncio.to_thread(fn, "x.wav", "zh", "small", False)
            if result.success:
                return result
        except Exception:
            continue
    return _FakeSTTResult(False)


class ContributionWrapper:
    """构造一个插件 STT 贡献（协议形态：有 transcribe 可调用）。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def contribution(self):
        from clipwright.plugins.contribution import Contribution

        return Contribution(ContributionKind.STT_PROVIDER, self.name, self)

    async def transcribe(self, audio_path, **kwargs):
        return _FakeSTTResult(True, text=f"{self.name}:ok")

    async def align(self, audio_path, text):
        return None

    def is_available(self) -> bool:
        return True
