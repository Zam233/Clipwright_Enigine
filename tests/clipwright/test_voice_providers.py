"""T4: voice provider layer tests — _sanitize_name, _is_local_url, PublicUploadManager,
BaseVoiceProvider subclasses (QwenTTS, CosyVoice, MiniMax) with mocked HTTP."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.services.voice import (
    BaseVoiceProvider,
    CosyVoiceProvider,
    MiniMaxProvider,
    PublicUploadManager,
    QwenTTSProvider,
    _is_local_url,
    _sanitize_name,
)


# ──────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────


class TestSanitizedName:
    def test_basic_alphanumeric(self):
        assert _sanitize_name("hello123", "fb") == "hello123"

    def test_strips_special_chars(self):
        assert _sanitize_name("my voice!", "fb") == "myvoice"

    def test_keeps_underscore_hyphen(self):
        assert _sanitize_name("my_voice-1", "fb") == "my_voice-1"

    def test_truncates_at_64(self):
        long_name = "a" * 100
        assert len(_sanitize_name(long_name, "fb")) == 64

    def test_fallback_on_empty(self):
        assert _sanitize_name("!!!", "fallback") == "fallback"

    def test_allow_extra_empty(self):
        result = _sanitize_name("my_voice", "fb", allow_extra="")
        assert "_" not in result


class TestIsLocalUrl:
    @pytest.mark.parametrize("url", [
        "http://localhost:8000/uploads/x.wav",
        "http://127.0.0.1/uploads/x.wav",
        "http://0.0.0.0:9000/file.mp3",
        "https://localhost/file",
        "https://127.0.0.1/file",
    ])
    def test_local_urls(self, url: str):
        assert _is_local_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://example.com/audio.wav",
        "https://uguu.se/upload",
        "ftp://somehost/file",
    ])
    def test_public_urls(self, url: str):
        assert _is_local_url(url) is False


# ──────────────────────────────────────────────
# PublicUploadManager
# ──────────────────────────────────────────────


class TestPublicUploadManager:
    def test_default_services_loaded(self):
        mgr = PublicUploadManager()
        assert len(mgr._services) >= 2

    @patch("clipwright.services.voice._convert_to_wav_16k_mono")
    @pytest.mark.asyncio
    async def test_maybe_upload_passthrough_public_url(self, mock_convert):
        mgr = PublicUploadManager()
        result = await mgr.maybe_upload("https://example.com/audio.wav")
        assert result == "https://example.com/audio.wav"
        mock_convert.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_upload_local_not_found(self):
        mgr = PublicUploadManager()
        with pytest.raises(FileNotFoundError):
            await mgr.maybe_upload("http://localhost:8000/uploads/nonexistent.wav")


# ──────────────────────────────────────────────
# QwenTTSProvider (mocked httpx)
# ──────────────────────────────────────────────


def _mock_httpx_post(status: int, json_body: dict | None = None, text: str = ""):
    """Return a mock httpx.AsyncClient whose .post() yields a canned response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.text = text or json.dumps(json_body or {})
    mock_resp.json.return_value = json_body or {}
    mock_resp.content = b"\x00" * 100
    mock_resp.headers = {"content-type": "audio/mpeg"}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return mock_client


class TestQwenTTSProvider:
    @pytest.mark.asyncio
    async def test_clone_success(self):
        provider = QwenTTSProvider()
        resp_body = {"output": {"voice": "v_abc123"}}
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(200, resp_body)
            voice_id, name = await provider.clone(
                audio_ref="data:audio/wav;base64,AAAA",
                voice_name="test_voice",
            )
        assert voice_id == "v_abc123"
        assert name == "test_voice"

    @pytest.mark.asyncio
    async def test_clone_failure_raises(self):
        provider = QwenTTSProvider()
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(400, text="bad request")
            with pytest.raises(RuntimeError, match="Qwen-TTS clone failed"):
                await provider.clone(audio_ref="data:audio/wav;base64,AAAA", voice_name="x")

    @pytest.mark.asyncio
    async def test_synthesize_b64_response(self):
        provider = QwenTTSProvider()
        audio_bytes = b"\xff\xfb\x90\x00" * 100
        resp_body = {"output": {"audio": {"data": base64.b64encode(audio_bytes).decode()}}}
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(200, resp_body)
            result = await provider.synthesize(
                voice_api_id="v_abc123", model="qwen3-tts-vc-2026-01-22", text="你好"
            )
        assert result == audio_bytes

    @pytest.mark.asyncio
    async def test_synthesize_url_response(self):
        provider = QwenTTSProvider()
        audio_bytes = b"\xff\xfb\x90\x00" * 50
        resp_body = {"output": {"audio": {"url": "https://example.com/audio.mp3"}}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = audio_bytes

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200, json=MagicMock(return_value=resp_body), text=json.dumps(resp_body)))
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = mock_client
            result = await provider.synthesize(
                voice_api_id="v_abc123", model="qwen3-tts-vc-2026-01-22", text="你好"
            )
        assert result == audio_bytes

    @pytest.mark.asyncio
    async def test_synthesize_realtime_model_raises(self):
        provider = QwenTTSProvider()
        with pytest.raises(ValueError, match="实时模型"):
            await provider.synthesize(
                voice_api_id="v_x", model="qwen3-tts-realtime", text="test"
            )


# ──────────────────────────────────────────────
# CosyVoiceProvider (mocked HTTP clone + mocked SDK synth)
# ──────────────────────────────────────────────


class TestCosyVoiceProvider:
    @pytest.mark.asyncio
    async def test_clone_success(self):
        provider = CosyVoiceProvider()
        resp_body = {"voice_id": "cv_xyz789"}
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(200, resp_body)
            voice_id, name = await provider.clone(
                audio_ref="https://example.com/sample.wav",
                voice_name="cosy_voice",
            )
        assert voice_id == "cv_xyz789"
        # _sanitize_name with allow_extra="" strips underscores → "cosyvoice"
        assert name == "cosyvoice"

    @pytest.mark.asyncio
    async def test_clone_detect_audio_error(self):
        provider = CosyVoiceProvider()
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(400, text="detect audio failed")
            with pytest.raises(RuntimeError, match="未能从音频中检测到有效人声"):
                await provider.clone(audio_ref="https://example.com/bad.wav", voice_name="x")

    @pytest.mark.asyncio
    async def test_clone_local_url_auto_upload(self):
        provider = CosyVoiceProvider(uploader=AsyncMock())
        provider._uploader.maybe_upload = AsyncMock(return_value="https://public.host/file.wav")
        resp_body = {"voice_id": "cv_111"}
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(200, resp_body)
            voice_id, _ = await provider.clone(
                audio_ref="http://localhost:8000/uploads/test.wav",
                voice_name="auto_upload",
            )
        assert voice_id == "cv_111"
        provider._uploader.maybe_upload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_synthesize_calls_sdk(self):
        provider = CosyVoiceProvider()
        mock_audio = b"\x00\x01\x02\x03"
        mock_synth = MagicMock()
        mock_synth.call.return_value = mock_audio

        mock_mod = MagicMock()
        with patch("clipwright.services.voice.CosyVoiceProvider.synthesize") as mock_synth:
            mock_synth.return_value = mock_audio
            result = await mock_synth(
                voice_api_id="cv_xyz789",
                model="cosyvoice-v3.5-plus",
                text="测试合成",
            )
        assert result == mock_audio


# ──────────────────────────────────────────────
# MiniMaxProvider (mocked HTTP)
# ──────────────────────────────────────────────


class TestMiniMaxProvider:
    @pytest.mark.asyncio
    async def test_clone_success(self):
        provider = MiniMaxProvider()
        resp_body = {"output": {"status": "ok"}}
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(200, resp_body)
            voice_id, name = await provider.clone(
                audio_ref="https://example.com/voice.wav",
                voice_name="mm_voice",
            )
        assert voice_id == "mm_voice"
        assert name == "mm_voice"

    @pytest.mark.asyncio
    async def test_synthesize_hex_response(self):
        provider = MiniMaxProvider()
        hex_data = b"\xff\xfb\x90\x00".hex()
        resp_body = {"output": {"data": {"audio": hex_data}}}
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(200, resp_body)
            result = await provider.synthesize(
                voice_api_id="mm_voice", model="MiniMax/speech-2.8-turbo", text="你好"
            )
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_synthesize_b64_response(self):
        provider = MiniMaxProvider()
        audio_bytes = b"\xff\xfb\x90\x00" * 25
        resp_body = {"output": {"audio": {"data": base64.b64encode(audio_bytes).decode()}}}
        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _mock_httpx_post(200, resp_body)
            result = await provider.synthesize(
                voice_api_id="mm_voice", model="MiniMax/speech-2.8-turbo", text="测试"
            )
        assert result == audio_bytes

    @pytest.mark.asyncio
    async def test_synthesize_content_type_audio(self):
        provider = MiniMaxProvider()
        audio_bytes = b"\xff\xfb\x90\x00" * 10
        resp_body = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = resp_body
        mock_resp.text = json.dumps(resp_body)
        mock_resp.content = audio_bytes
        mock_resp.headers = {"content-type": "audio/mpeg"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("clipwright.services.voice.httpx.AsyncClient") as MockClient:
            MockClient.return_value = mock_client
            result = await provider.synthesize(
                voice_api_id="mm_voice", model="MiniMax/speech-2.8-turbo", text="test"
            )
        assert result == audio_bytes


# ──────────────────────────────────────────────
# BaseVoiceProvider contract
# ──────────────────────────────────────────────


class TestBaseVoiceProviderContract:
    def test_cannot_instantiate_base(self):
        with pytest.raises(TypeError):
            BaseVoiceProvider()  # type: ignore

    @pytest.mark.asyncio
    async def test_subclass_must_implement_clone(self):
        class Incomplete(BaseVoiceProvider):
            async def clone(self, **kw):
                return ("id", "name")

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore

    @pytest.mark.asyncio
    async def test_subclass_must_implement_synth(self):
        class Incomplete(BaseVoiceProvider):
            async def synthesize(self, **kw):
                return b""

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore
