"""artifact_search / artifact_summary。"""

from __future__ import annotations

import asyncio
import json
import pytest

from clipwright.tool.artifacts import ArtifactSearchTool, ArtifactSummaryTool


@pytest.fixture
def ws(tmp_path, monkeypatch):
    import clipwright.security as security

    monkeypatch.setattr(security, "allowed_media_roots", lambda: [tmp_path])
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "art_a.json").write_text(
        json.dumps({"scenes": [{"title": "PCL 公告"}]}, ensure_ascii=False), encoding="utf-8",
    )
    (tmp_path / "artifacts" / "art_b.json").write_text(
        json.dumps({"tracks": []}, ensure_ascii=False), encoding="utf-8",
    )
    return tmp_path


def test_artifact_search(ws) -> None:
    out = asyncio.run(ArtifactSearchTool().execute(
        query="PCL", workspace_root=str(ws),
    ))
    assert str(out.status) == "success"
    assert out.output["count"] == 1
    assert out.output["items"][0]["artifact"] == "art_a"


def test_artifact_summary(ws) -> None:
    out = asyncio.run(ArtifactSummaryTool().execute(
        artifact="art_a", workspace_root=str(ws),
    ))
    assert str(out.status) == "success"
    assert out.output["shape"]["type"] == "dict"
    assert "scenes" in out.output["shape"]["keys"]
    assert out.output["bytes"] > 0
