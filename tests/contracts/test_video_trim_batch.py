"""video_trim_batch：并发、逐项失败。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.tool import video_batch as mod


class _FakeTrim:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *, input_path, start_sec=0, duration_sec=None,
                      end_sec=None, output_path=None):
        self.calls += 1
        if "bad" in str(input_path):
            return SimpleNamespace(status="error", error="source corrupt", output_path="")
        return SimpleNamespace(
            status="success", error="", output_path=f"/tmp/{input_path}.mp4",
        )


def test_video_trim_batch_partial_failure(monkeypatch) -> None:
    fake = _FakeTrim()
    monkeypatch.setattr(mod, "resolve", lambda _cap: fake)
    out = asyncio.run(mod.VideoTrimBatchTool().execute(
        items=[{"input_path": "a.mp4", "duration_sec": 2},
               {"input_path": "bad.mp4", "duration_sec": 2}],
        concurrency=2,
    ))
    assert str(out.status) == "success"
    assert out.output["summary"] == {"total": 2, "ok": 1, "failed": 1}
    assert out.output["items"][0]["output_path"] == "/tmp/a.mp4.mp4"
    assert out.output["items"][1]["status"] == "failed"
    assert fake.calls == 2


def test_video_trim_batch_requires_trim(monkeypatch) -> None:
    monkeypatch.setattr(mod, "resolve", lambda _cap: None)
    out = asyncio.run(mod.VideoTrimBatchTool().execute(items=[{"input_path": "a.mp4"}]))
    assert str(out.status) == "not_found"
