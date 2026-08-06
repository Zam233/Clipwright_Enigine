"""B11: maybe_upload local-path validation — media extension + file existence.

Local inputs (localhost URLs / bare filesystem paths) are validated before
upload; public http(s) URLs pass through unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clipwright.services.voice import PublicUploadManager


class TestMaybeUploadLocalValidation:
    @pytest.mark.asyncio
    async def test_api_url_raises_clear_error(self):
        """localhost API URL without a media extension must not be silently accepted."""
        mgr = PublicUploadManager()
        with pytest.raises(ValueError, match="音频"):
            await mgr.maybe_upload("http://127.0.0.1:8000/api/anything")

    @pytest.mark.asyncio
    async def test_nonexistent_local_mp3_raises_file_not_found(self):
        mgr = PublicUploadManager()
        with pytest.raises(FileNotFoundError, match="不存在"):
            await mgr.maybe_upload("renders/nonexistent.mp3")

    @pytest.mark.asyncio
    async def test_wrong_extension_raises(self, tmp_path: Path):
        notes = tmp_path / "notes.txt"
        notes.write_text("not audio", encoding="utf-8")
        mgr = PublicUploadManager()
        with pytest.raises(ValueError, match="音频"):
            await mgr.maybe_upload(str(notes))

    @pytest.mark.asyncio
    async def test_valid_local_wav_uploads(self, tmp_path: Path, monkeypatch):
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        mgr = PublicUploadManager()
        monkeypatch.setattr(
            "clipwright.services.voice._resolve_local_file", lambda p: audio
        )
        mgr.upload = AsyncMock(return_value="https://public.example/sample.wav")
        result = await mgr.maybe_upload("http://localhost:8000/uploads/sample.wav")
        assert result == "https://public.example/sample.wav"
        mgr.upload.assert_awaited_once_with(audio)

    @pytest.mark.asyncio
    async def test_public_url_passes_through(self):
        mgr = PublicUploadManager()
        public = "https://example.com/audio.wav"
        assert await mgr.maybe_upload(public) == public
        assert (
            await mgr.maybe_upload("http://example.com/voice.mp3")
            == "http://example.com/voice.mp3"
        )
