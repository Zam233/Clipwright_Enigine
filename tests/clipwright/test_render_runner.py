"""Worker 渲染执行器（render_runner）单测 — 纯函数 + run_job 状态机。

RenderService.render 用 AsyncMock 替换，不触真实 ffmpeg；
work_dir 通过 CLIPWRIGHT_WORKER_WORK_DIR 指向 tmp_path，避免污染仓库。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clipwright.services.render import RenderResult
from clipwright.worker import render_runner as rr
from clipwright.worker.store import JobStore


@pytest.fixture(autouse=True)
def _work_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPWRIGHT_WORKER_WORK_DIR", str(tmp_path / "worker"))
    return tmp_path / "worker"


def _tl(asset_uri: str | None = None) -> dict:
    clips = [
        {
            "id": "c1",
            "kind": "video",
            "asset_id": asset_uri or "",
            "track_id": "t1",
            "start_sec": 0.0,
            "duration_sec": 1.0,
        }
    ]
    return {
        "id": "tl_test",
        "width": 64,
        "height": 64,
        "fps": 30,
        "duration_sec": 1.0,
        "tracks": [{"id": "t1", "name": "视频轨", "kind": "video", "index": 0, "clips": clips}],
    }


def _make_asset(assets_dir: Path, sha1: str = "0123456789abcdef0123") -> Path:
    assets_dir.mkdir(parents=True, exist_ok=True)
    p = assets_dir / f"{sha1[:16]}.mp4"
    p.write_bytes(b"fake")
    return p


class TestPureFunctions:
    def test_filter_render_params_drops_unknown_and_fills_size(self) -> None:
        tl = {"width": 640, "height": 360, "fps": 24, "duration_sec": 5}
        out = rr._filter_render_params(
            {"width": 1280, "bitrate": "1M", "evil_key": "x"}, tl
        )
        assert out["width"] == 1280  # 显式传参优先
        assert out["height"] == 360  # 缺省回退 timeline
        assert out["fps"] == 24
        assert "evil_key" not in out

    def test_filter_render_params_skips_zero_size_in_timeline(self) -> None:
        out = rr._filter_render_params({}, {"width": 0, "height": "", "fps": None})
        assert "width" not in out and "height" not in out and "fps" not in out

    def test_resolve_asset_uri_prefix_match(self, tmp_path) -> None:
        sha1 = "0123456789abcdef0123"
        p = _make_asset(tmp_path / "assets", sha1)
        got = rr._resolve_asset_uri(f"asset://{sha1}", tmp_path / "assets")
        assert got == str(p)

    def test_resolve_asset_uri_missing_raises(self, tmp_path) -> None:
        with pytest.raises(rr.AssetNotFoundError):
            rr._resolve_asset_uri("asset://ffffffffffffffffffff", tmp_path / "assets")

    def test_resolve_asset_uri_empty_raises(self, tmp_path) -> None:
        with pytest.raises(rr.AssetNotFoundError):
            rr._resolve_asset_uri("asset://", tmp_path / "assets")

    def test_resolve_asset_refs_rewrites_nested_strings(self, tmp_path) -> None:
        assets = tmp_path / "assets"
        sha1 = "0123456789abcdef0123"
        _make_asset(assets, sha1)
        obj = {"a": [f"asset://{sha1}", {"b": f"asset://{sha1}"}], "c": "keep"}
        out = rr._resolve_asset_refs(obj, assets)
        assert out["a"][0] == str(assets / f"{sha1[:16]}.mp4")
        assert out["a"][1]["b"] == out["a"][0]
        assert out["c"] == "keep"

    def test_remap_original_asset_ids(self) -> None:
        tracks = [
            {"clips": [{"asset_id": "orig1"}, {"asset_id": "asset://abc"}, {"asset_id": ""}, {}]}
        ]
        rr._remap_original_asset_ids(tracks, {"orig1": "asset://deadbeef"})
        clips = tracks[0]["clips"]
        assert clips[0]["asset_id"] == "asset://deadbeef"
        assert clips[1]["asset_id"] == "asset://abc"  # 已是 asset:// 形式保持原样
        assert clips[2]["asset_id"] == ""
        assert "asset_id" not in clips[3]


class TestRunJob:
    def _patch_render(self, monkeypatch, result: RenderResult) -> AsyncMock:
        mock = AsyncMock(return_value=result)
        monkeypatch.setattr(rr.RenderService, "render", mock)
        return mock

    def test_run_job_success(self, monkeypatch) -> None:
        work = rr.resolve_work_dir()
        sha1 = "0123456789abcdef0123"
        _make_asset(work / "assets", sha1)
        self._patch_render(monkeypatch, RenderResult(success=True, duration_sec=1.0))

        store = JobStore()
        store.create_job("job_ok")
        result = asyncio.run(
            rr.run_job("job_ok", _tl(f"asset://{sha1}"), {"width": 64}, {"orig": f"asset://{sha1}"}, store)
        )

        assert result.success is True
        job = store.get_job("job_ok")
        assert job["status"] == "completed"
        assert job["progress"] == 100
        assert job["output_path"].endswith("job_ok.mp4")

    def test_run_job_remaps_and_passes_filtered_params(self, monkeypatch) -> None:
        work = rr.resolve_work_dir()
        sha1 = "0123456789abcdef0123"
        _make_asset(work / "assets", sha1)
        mock = AsyncMock(return_value=RenderResult(success=True, duration_sec=1.0))
        monkeypatch.setattr(rr.RenderService, "render", mock)

        store = JobStore()
        store.create_job("job_args")
        tl = _tl("orig1")
        asyncio.run(rr.run_job("job_args", tl, {"bitrate": "1M", "junk": 1}, {"orig1": f"asset://{sha1}"}, store))

        kwargs = mock.call_args.kwargs
        assert kwargs.get("width") == 64  # 从 timeline 回填
        assert "junk" not in kwargs
        timeline_arg = mock.call_args.args[0]
        assert timeline_arg.tracks[0].clips[0].asset_id == str(work / "assets" / f"{sha1[:16]}.mp4")

    def test_run_job_missing_asset_marks_failed(self, monkeypatch) -> None:
        rr.resolve_work_dir()
        self._patch_render(monkeypatch, RenderResult(success=True))
        store = JobStore()
        store.create_job("job_miss")
        with pytest.raises(rr.AssetNotFoundError):
            asyncio.run(rr.run_job("job_miss", _tl("asset://ffffffffffffffffffff"), {}, {}, store))
        assert store.get_job("job_miss")["status"] == "failed"

    def test_run_job_render_failure_marks_failed(self, monkeypatch) -> None:
        work = rr.resolve_work_dir()
        sha1 = "0123456789abcdef0123"
        _make_asset(work / "assets", sha1)
        self._patch_render(monkeypatch, RenderResult(success=False, error="boom"))
        store = JobStore()
        store.create_job("job_fail")
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(
                rr.run_job("job_fail", _tl(f"asset://{sha1}"), {}, {}, store)
            )
        job = store.get_job("job_fail")
        assert job["status"] == "failed"
        assert "boom" in job["error"]

    def test_run_job_malformed_timeline_marks_failed(self, monkeypatch) -> None:
        rr.resolve_work_dir()
        self._patch_render(monkeypatch, RenderResult(success=True))
        store = JobStore()
        store.create_job("job_bad")
        with pytest.raises(ValueError, match="tracks"):
            asyncio.run(rr.run_job("job_bad", {"no_tracks": True}, {}, {}, store))
        assert store.get_job("job_bad")["status"] == "failed"
