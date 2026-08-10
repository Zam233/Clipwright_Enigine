"""E8/E9: VisionService.analyze_image 结果缓存（path+mtime key，TTL 1h，上限 512）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.services.vision import (
    VisionService,
    clear_vision_cache,
    _VISION_CACHE,
    _VISION_CACHE_MAX,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_vision_cache()
    yield
    clear_vision_cache()


def _make_service() -> VisionService:
    return VisionService.__new__(VisionService)


class TestVisionCache:
    @pytest.mark.asyncio
    async def test_same_image_llm_called_once(self, tmp_path) -> None:
        p = tmp_path / "a.png"
        p.write_bytes(b"fake-png")
        svc = _make_service()
        classifier = AsyncMock(return_value={"tags": ["a"], "description": "d", "labels": [], "model": "llm/m"})
        with (
            patch("clipwright.config.settings.vision_provider", "llm", create=True),
            patch.object(svc, "_classify_llm", classifier),
        ):
            r1 = await svc.analyze_image(str(p))
            r2 = await svc.analyze_image(str(p))

        assert r1 == r2
        assert classifier.call_count == 1

    @pytest.mark.asyncio
    async def test_changed_file_reanalyzes(self, tmp_path) -> None:
        p = tmp_path / "b.png"
        p.write_bytes(b"v1")
        svc = _make_service()
        classifier = AsyncMock(return_value={"tags": ["a"], "description": "d", "labels": [], "model": "llm/m"})
        with (
            patch("clipwright.config.settings.vision_provider", "llm", create=True),
            patch.object(svc, "_classify_llm", classifier),
        ):
            await svc.analyze_image(str(p))
            # 修改文件内容 → mtime 变化 → 重新分析（显式设置不同的 mtime 保证纳秒级变化）
            import os
            st1 = p.stat().st_mtime_ns
            p.write_bytes(b"v2")
            st2 = p.stat().st_mtime_ns
            if st2 == st1:  # 粗粒度 FS 兜底：强制推进 mtime
                os.utime(p, (st1 // 10**9 + 1, st1 // 10**9 + 1))
            await svc.analyze_image(str(p))

        assert classifier.call_count == 2

    @pytest.mark.asyncio
    async def test_file_not_found_not_cached(self, tmp_path) -> None:
        svc = _make_service()
        res = await svc.analyze_image(str(tmp_path / "ghost.png"))
        assert "error" in res
        assert len(_VISION_CACHE) == 0

    @pytest.mark.asyncio
    async def test_cache_bounded(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("clipwright.services.vision._VISION_CACHE_MAX", 2)
        svc = _make_service()
        classifier = AsyncMock(return_value={"tags": [], "description": "d", "labels": [], "model": "llm/m"})
        files = []
        for i in range(5):
            p = tmp_path / f"c{i}.png"
            p.write_bytes(b"x")
            files.append(p)
        with (
            patch("clipwright.config.settings.vision_provider", "llm", create=True),
            patch.object(svc, "_classify_llm", classifier),
        ):
            for p in files:
                await svc.analyze_image(str(p))

        assert len(_VISION_CACHE) <= 2
