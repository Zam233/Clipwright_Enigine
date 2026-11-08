"""素材源 media_type 默认值（E2E 2026-10-28：一个空串毁掉整条素材链路）。

现象：43 个场景全部返回 `suggested_assets: []`、`score: 0.0`，剪辑只能退化成
"纯背景 + 文字"——用户看到的成片没有一帧真实画面。

根因：`MaterialRegistry.search(media_type="")` 把**空串**透传给各素材源，而所有源
都写 `if media_type in ("video", "all")` / `("photo", "all")` / `("audio", "all")`。
空串两个分支都不进 → 每个源静默返回 []（连一条日志都没有），
"候选素材 43 条"其实是 43 个空信封。

修复：registry 侧 `media_type or "all"`；源侧再把 `""` 归一成 `"all"`。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.material.registry import MaterialRegistry
from clipwright.schema.material import MaterialAsset, MaterialSearchResult, MaterialType


class _RecordingSource:
    """记录收到的 media_type，并按"只认 video/all"的源惯例过滤。"""

    #: registry 用这两个属性做日志与结果归因
    source_id = "fake_src"
    source_name = "Fake 素材源"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def search(self, query: str, top_k: int = 10, media_type: str = "all", **kw):  # noqa: ANN001, ANN003, ANN201
        self.seen.append(media_type)
        if media_type not in ("video", "all"):
            return []
        asset = MaterialAsset(id="a1", title="t", type=MaterialType.VIDEO, url="https://x/y.mp4")
        return [(asset, 0.8)]


@pytest.fixture()
def fake_source(monkeypatch):
    src = _RecordingSource()
    monkeypatch.setitem(MaterialRegistry._sources, "fake_src", src)  # noqa: SLF001
    try:
        yield src
    finally:
        MaterialRegistry._sources.pop("fake_src", None)  # noqa: SLF001


def test_empty_media_type_is_passed_as_all(fake_source):
    res = asyncio.run(MaterialRegistry.search("query", top_k_per_source=3, source_ids=["fake_src"]))

    assert fake_source.seen == ["all"], "空串必须归一成 all，否则源侧两个分支都不进"
    assert len(res) == 1
    assert res[0].asset.id == "a1"


def test_explicit_media_type_is_respected(fake_source):
    res = asyncio.run(MaterialRegistry.search(
        "query", top_k_per_source=3, source_ids=["fake_src"], media_type="video"))

    assert fake_source.seen == ["video"]
    assert len(res) == 1


def test_search_result_wraps_asset_and_score(fake_source):
    res = asyncio.run(MaterialRegistry.search("q", top_k_per_source=1, source_ids=["fake_src"]))

    assert isinstance(res[0], MaterialSearchResult)
    assert res[0].score == pytest.approx(0.8)
    assert res[0].source_name
