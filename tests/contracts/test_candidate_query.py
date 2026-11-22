"""R5：candidate_query / rerank / dedupe。"""

from __future__ import annotations

import asyncio

from clipwright.tool.material_query import (
    CandidateQueryTool, CandidateRerankTool, MaterialDedupeTool,
)


def test_candidate_query_filters() -> None:
    rows = [
        {"scene_index": 0, "asset_id": "a", "title": "城市夜景", "type": "video"},
        {"scene_index": 1, "asset_id": "b", "title": "芯片", "type": "image"},
    ]
    out = asyncio.run(CandidateQueryTool().execute(
        candidates=rows, scene_index=0, kind="video",
    ))
    assert out.output["total"] == 1
    assert out.output["items"][0]["asset_id"] == "a"


def test_candidate_rerank_and_dedupe() -> None:
    rows = [
        {"asset_id": "a", "title": "芯片", "score": 0.2},
        {"asset_id": "b", "title": "城市", "score": 0.9},
        {"asset_id": "a", "title": "芯片重复", "score": 0.8},
    ]
    ranked = asyncio.run(CandidateRerankTool().execute(
        candidates=rows, scene_context={"title": "芯片", "keywords": ["芯片"]}, top_k=2,
    ))
    assert ranked.output["items"][0]["asset_id"] == "a"
    deduped = asyncio.run(MaterialDedupeTool().execute(candidates=rows))
    assert deduped.output["total"] == 2
    assert len(deduped.output["removed"]) == 1
