"""轮70（D7）：远程渲染健壮性——轮询瞬态容忍 + 下载大小上限。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from clipwright.services.remote_render import RemoteRenderError, RemoteRenderService


class _Resp:
    def __init__(self, status_code: int = 200, json_data: dict | None = None,
                 headers: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}
        self.text = ""

    def json(self) -> dict:
        return self._json


class _Stream:
    def __init__(self, status_code: int, chunks: list[bytes],
                 headers: dict | None = None) -> None:
        self.status_code = status_code
        self._chunks = chunks
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _Client:
    """伪造 httpx.AsyncClient：get 按序列返回/抛错，stream 返回固定块。"""

    def __init__(self, get_seq: list | None = None, stream_obj: _Stream | None = None) -> None:
        self.get_seq = list(get_seq or [])
        self.stream_obj = stream_obj
        self.get_calls = 0

    async def get(self, url, headers=None):
        self.get_calls += 1
        item = self.get_seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stream(self, method, url, headers=None):
        assert self.stream_obj is not None
        return self.stream_obj


def _patch_settings(monkeypatch, **kw) -> None:
    from clipwright.config import settings
    for k, v in kw.items():
        monkeypatch.setattr(settings, k, v)


class TestPollTransientTolerance:
    @pytest.mark.asyncio
    async def test_transient_errors_tolerated_then_success(self, monkeypatch) -> None:
        _patch_settings(monkeypatch, remote_render_poll_interval=0.01,
                        remote_render_timeout=10, remote_render_poll_max_failures=3)
        client = _Client(get_seq=[
            httpx.ConnectError("reset"),
            httpx.ReadTimeout("slow"),
            _Resp(200, {"status": "completed", "progress": 100}),
        ])
        job = await RemoteRenderService()._poll_job(
            client, "http://w", {}, "job1", None, cancel_id=None,
        )
        assert job["status"] == "completed"
        assert client.get_calls == 3

    @pytest.mark.asyncio
    async def test_persistent_errors_eventually_raise(self, monkeypatch) -> None:
        _patch_settings(monkeypatch, remote_render_poll_interval=0.01,
                        remote_render_timeout=10, remote_render_poll_max_failures=3)
        client = _Client(get_seq=[
            httpx.ConnectError("reset"),
            httpx.ConnectError("reset"),
            httpx.ConnectError("reset"),
        ])
        with pytest.raises(RemoteRenderError) as ei:
            await RemoteRenderService()._poll_job(
                client, "http://w", {}, "job1", None, cancel_id=None,
            )
        assert "连续 3 次" in str(ei.value)

    @pytest.mark.asyncio
    async def test_failure_counter_resets_after_success(self, monkeypatch) -> None:
        _patch_settings(monkeypatch, remote_render_poll_interval=0.01,
                        remote_render_timeout=10, remote_render_poll_max_failures=3)
        client = _Client(get_seq=[
            httpx.ConnectError("x"),
            _Resp(200, {"status": "running", "progress": 10}),
            httpx.ConnectError("x"),
            httpx.ConnectError("x"),
            _Resp(200, {"status": "completed", "progress": 100}),
        ])
        job = await RemoteRenderService()._poll_job(
            client, "http://w", {}, "job1", None, cancel_id=None,
        )
        assert job["status"] == "completed"


class TestDownloadCap:
    @pytest.mark.asyncio
    async def test_content_length_over_cap_rejected(self, monkeypatch, tmp_path: Path) -> None:
        _patch_settings(monkeypatch, remote_render_max_download_mb=1)
        client = _Client(stream_obj=_Stream(
            200, [b"x"], headers={"content-length": str(2 * 1024 * 1024)},
        ))
        out = tmp_path / "out.mp4"
        with pytest.raises(RemoteRenderError) as ei:
            await RemoteRenderService()._download_output(client, "http://w", {}, "j1", out)
        assert "过大" in str(ei.value)
        assert not out.exists()
        assert not list(tmp_path.glob("*.part-*"))

    @pytest.mark.asyncio
    async def test_stream_exceeds_cap_aborted(self, monkeypatch, tmp_path: Path) -> None:
        _patch_settings(monkeypatch, remote_render_max_download_mb=1)
        # 无 content-length：每块 600KB，第二块即超 1MB
        client = _Client(stream_obj=_Stream(200, [b"a" * (600 * 1024)] * 3))
        out = tmp_path / "out.mp4"
        with pytest.raises(RemoteRenderError) as ei:
            await RemoteRenderService()._download_output(client, "http://w", {}, "j1", out)
        assert "上限" in str(ei.value)
        assert not out.exists()
        assert not list(tmp_path.glob("*.part-*"))

    @pytest.mark.asyncio
    async def test_success_writes_file(self, monkeypatch, tmp_path: Path) -> None:
        _patch_settings(monkeypatch, remote_render_max_download_mb=1)
        client = _Client(stream_obj=_Stream(200, [b"hello", b" world"]))
        out = tmp_path / "out.mp4"
        final = await RemoteRenderService()._download_output(client, "http://w", {}, "j1", out)
        assert final.read_bytes() == b"hello world"
        assert not list(tmp_path.glob("*.part-*"))
