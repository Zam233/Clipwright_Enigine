"""generate_mg_batch：并发、逐项失败。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.animation.mg import generator as gen_mod
from clipwright.tool.animation_batch import GenerateMGBatchTool


class _FakeMG:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, description, text_content, **_kwargs):
        self.calls += 1
        if "bad" in description:
            raise RuntimeError("mg provider failed")
        return {"success": True, "mg_def": {"description": description}, "method": "test"}


def test_generate_mg_batch_partial_failure(monkeypatch) -> None:
    fake = _FakeMG()
    monkeypatch.setattr(gen_mod, "MGGenerator", lambda: fake)
    out = asyncio.run(GenerateMGBatchTool().execute(
        items=[{"description": "good", "text_content": "a"},
               {"description": "bad", "text_content": "b"}],
        concurrency=2,
    ))
    assert str(out.status) == "success"
    assert out.output["summary"] == {"total": 2, "ok": 1, "failed": 1}
    assert out.output["items"][0]["mg_def"]["description"] == "good"
    assert out.output["items"][1]["status"] == "failed"
    assert fake.calls == 2
