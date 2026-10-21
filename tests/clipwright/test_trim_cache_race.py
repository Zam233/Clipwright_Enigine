"""裁剪缓存并发落位的回归（第 32 轮 V32 实测踩到的 Windows 文件占用）。

同一素材、同一起点、同一时长会算出同一个 cache_key；两个裁剪线程若写同一个
`.part` 临时文件，第二个 `os.replace` 在 Windows 上会撞"文件被占用"，整条渲染失败。
这里锁住两条防线：临时名每次唯一、后缀留在末尾（ffmpeg 按后缀推断 muxer）。
"""

from __future__ import annotations

from clipwright.services.render import RenderService


class TestAtomicTempPath:
    def test_suffix_stays_at_end(self) -> None:
        path = RenderService._atomic_temp_path("C:/x/trim_abc.mp4")
        assert path.endswith(".mp4")
        assert ".part" in path
        assert not path.endswith(".mp4.part")

    def test_unique_token_makes_paths_distinct(self) -> None:
        base = "C:/x/trim_abc.mp4"
        first = RenderService._atomic_temp_path(base, unique="aaaa1111")
        second = RenderService._atomic_temp_path(base, unique="bbbb2222")
        assert first != second
        assert first.endswith(".mp4") and second.endswith(".mp4")

    def test_without_token_is_stable(self) -> None:
        base = "C:/x/trim_abc.mp4"
        assert RenderService._atomic_temp_path(base) == RenderService._atomic_temp_path(base)

    def test_extensionless_path(self) -> None:
        assert RenderService._atomic_temp_path("C:/x/noext", unique="t1").endswith(".t1.part")
