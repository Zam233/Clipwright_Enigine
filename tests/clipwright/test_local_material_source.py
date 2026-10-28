"""本地素材源与 local_material 插件单测（素材插件审计的产物之一）。

背景：原来的 `my_material_lib` 是示例插件（catalog 指向不存在文件、URL 指向
cdn.example.com），另有一个 `coverr_material` 声称"无需 Key 的公开 API"，实测其
接口只返回缩略图、拿不到可下载视频——两者都已按"真调用或删除"处理：
Coverr 删除，示例插件换成真正的本地文件夹源（local_material）。
"""

from __future__ import annotations

import pytest

from clipwright.material.local_source import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    LocalFolderSource,
    source_from_env,
)


def _make_tree(root):
    (root / "videos").mkdir(parents=True)
    (root / "photos").mkdir(parents=True)
    (root / "videos" / "city_night.mp4").write_bytes(b"\x00" * 32)
    (root / "videos" / "ocean_waves.mov").write_bytes(b"\x00" * 32)
    (root / "photos" / "city_day.png").write_bytes(b"\x00" * 32)
    (root / "photos" / "notes.txt").write_text("不是媒体", encoding="utf-8")
    (root / "readme.md").write_text("不是媒体", encoding="utf-8")
    return root


class TestIndex:
    def test_indexes_only_media(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        source = LocalFolderSource(root)
        assert source.refresh() == 3

    def test_non_recursive_mode(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        (root / "top_level.mp4").write_bytes(b"\x00" * 8)
        flat = LocalFolderSource(root, recursive=False)
        assert flat.refresh() == 1

    def test_max_files_cap(self, tmp_path) -> None:
        root = tmp_path
        for index in range(6):
            (root / f"clip_{index}.mp4").write_bytes(b"\x00" * 4)
        source = LocalFolderSource(root, max_files=3)
        assert source.refresh() == 3

    def test_missing_dir_is_empty(self, tmp_path) -> None:
        source = LocalFolderSource(tmp_path / "nope")
        assert source.refresh() == 0


class TestSearch:
    @pytest.mark.asyncio
    async def test_filename_match_returns_real_paths(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        source = LocalFolderSource(root)
        results = await source.search("city", top_k=5)
        assert results, "应命中 city_night.mp4 / city_day.png"
        urls = {asset.url for asset, _score in results}
        assert all(url.startswith(str(root)) for url in urls)
        assert all(asset.source == source.source_id for asset, _ in results)

    @pytest.mark.asyncio
    async def test_media_type_filter(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        source = LocalFolderSource(root)
        videos = await source.search("city", top_k=5, media_type="video")
        # MaterialAsset.type 是 str（不是枚举），按字符串断言
        assert videos and all(asset.type == "video" for asset, _ in videos)
        photos = await source.search("city", top_k=5, media_type="photo")
        assert photos and all(asset.type == "image" for asset, _ in photos)

    @pytest.mark.asyncio
    async def test_chinese_keyword_match(self, tmp_path) -> None:
        root = tmp_path
        (root / "海边日落.mp4").write_bytes(b"\x00" * 8)
        source = LocalFolderSource(root)
        results = await source.search("海边", top_k=5)
        assert results and results[0][0].title == "海边日落"

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        source = LocalFolderSource(root)
        assert await source.search("完全不相关的词", top_k=5) == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_neutral_scores(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        source = LocalFolderSource(root)
        results = await source.search("", top_k=2)
        assert len(results) == 2
        assert all(score == 0.5 for _asset, score in results)

    @pytest.mark.asyncio
    async def test_score_ordering_is_deterministic(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        source = LocalFolderSource(root)
        first = [asset.id for asset, _ in await source.search("city", top_k=5)]
        second = [asset.id for asset, _ in await source.search("city", top_k=5)]
        assert first == second

    @pytest.mark.asyncio
    async def test_metadata_carries_local_path(self, tmp_path) -> None:
        root = _make_tree(tmp_path)
        asset, _score = (await LocalFolderSource(root).search("ocean", top_k=1))[0]
        assert asset.metadata["local_path"].endswith("ocean_waves.mov")


class TestEnvFactory:
    def test_unset_env_returns_none(self, monkeypatch) -> None:
        monkeypatch.delenv("CLIPWRIGHT_LOCAL_MATERIAL_DIR", raising=False)
        assert source_from_env() is None

    def test_missing_dir_returns_none(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("CLIPWRIGHT_LOCAL_MATERIAL_DIR", str(tmp_path / "nope"))
        assert source_from_env() is None

    def test_valid_dir_builds_source(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("CLIPWRIGHT_LOCAL_MATERIAL_DIR", str(tmp_path))
        source = source_from_env()
        assert isinstance(source, LocalFolderSource)


class TestPluginContribution:
    def test_plugin_contributes_with_real_dir(self, tmp_path, monkeypatch) -> None:
        _make_tree(tmp_path)
        from plugins.local_material.main import LocalMaterialPlugin
        from clipwright.plugins.contribution import ContributionKind

        monkeypatch.setenv("CLIPWRIGHT_LOCAL_MATERIAL_DIR", str(tmp_path))
        plugin = LocalMaterialPlugin()
        contributions = plugin.contributions()
        assert len(contributions) == 1
        assert contributions[0].kind == ContributionKind.MATERIAL_SOURCE
        assert contributions[0].id == "local_material"

    def test_plugin_zero_contribution_without_dir(self, monkeypatch) -> None:
        from plugins.local_material.main import LocalMaterialPlugin

        monkeypatch.delenv("CLIPWRIGHT_LOCAL_MATERIAL_DIR", raising=False)
        plugin = LocalMaterialPlugin()
        plugin.config = {}
        assert plugin.contributions() == []

    def test_plugin_zero_contribution_for_empty_dir(self, tmp_path, monkeypatch) -> None:
        from plugins.local_material.main import LocalMaterialPlugin

        monkeypatch.setenv("CLIPWRIGHT_LOCAL_MATERIAL_DIR", str(tmp_path))
        assert LocalMaterialPlugin().contributions() == []


class TestExtensions:
    def test_known_extensions(self) -> None:
        assert ".mp4" in VIDEO_EXTS and ".mov" in VIDEO_EXTS
        assert ".png" in IMAGE_EXTS and ".jpg" in IMAGE_EXTS
