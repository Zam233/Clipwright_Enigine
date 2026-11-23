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
        return {"success": True, "mg_def": {
            "animation_id": "a", "duration_sec": 3, "width": 1920, "height": 1080,
            "elements": [{"type": "text", "content": "A", "x": "center", "y": "center",
                          "font_size": 80, "font_color": "#fff",
                          "keyframes": [{"time": 0, "opacity": 0},
                                        {"time": 0.5, "opacity": 1}]}],
        }, "method": "test"}


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
    assert out.output["items"][0]["mg_def"]["animation_id"] == "a"
    assert out.output["items"][1]["status"] == "failed"
    assert fake.calls == 2


def test_generate_mg_batch_prevalidates_supplied_mg(tmp_path) -> None:
    tool = GenerateMGBatchTool()
    mg = {"animation_id": "a", "duration_sec": 2, "width": 1920, "height": 1080,
          "elements": [{"type": "text", "content": "A", "x": "center", "y": "center",
                        "font_size": 80, "font_color": "#fff",
                        "keyframes": [{"time": 0, "opacity": 0},
                                      {"time": 0.5, "opacity": 1}]}]}
    out = asyncio.run(tool.execute(
        items=[{"mg_def": mg}], workspace_root=str(tmp_path),
    ))
    assert out.output["items"][0]["status"] == "ok"
    assert out.output["items"][0]["preview_path"]
    bad = dict(mg); bad["elements"] = [{"type": "text", "content": "A",
                                        "font_color": ""}]
    out2 = asyncio.run(tool.execute(items=[{"mg_def": bad}]))
    assert out2.output["items"][0]["status"] == "failed"
