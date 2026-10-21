"""lyric_mv 类型插件单测（PRD Part II / V35 的类型侧）。

这个插件是**第一个按新契约写全的内置类型**（10 槽位 + 参数规则 + 代理指标 + 声明），
所以这里既验"规范自洽"，也验"判定器给它一条真 MV 形态时间线时会通过"。
"""

from __future__ import annotations

import pytest

from clipwright.category.lyric_mv import LyricMvPlugin
from clipwright.schema.category_spec import rule_key_allowed, validate_bundle
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


@pytest.fixture(scope="module")
def plugin() -> LyricMvPlugin:
    return LyricMvPlugin()


@pytest.fixture()
def spec_registered():
    """临时把 lyric_mv 放进注册表（genre-check 走 load_spec，要求注册表里有它）。"""
    from clipwright.category.registry import CategoryRegistry

    added = CategoryRegistry.get("lyric_mv") is None
    if added:
        CategoryRegistry.register(LyricMvPlugin())
    yield
    if added:
        CategoryRegistry.unregister("lyric_mv")


class TestBundle:
    def test_bundle_has_no_issues(self, plugin) -> None:
        issues = validate_bundle(
            plugin.get_prompt_bundle(), plugin.get_param_rules(), plugin.get_signature_traits()
        )
        assert issues == [], [i.model_dump() for i in issues]

    def test_declares_all_ten_slots(self, plugin) -> None:
        slots = set(plugin.get_prompt_bundle().normalized().slots)
        assert slots == {
            "genre", "anti_patterns", "lexicon",
            "structure", "material", "edit", "animation", "audio", "subtitle", "quality",
        }

    def test_prompt_budget_within_limit(self, plugin) -> None:
        assert plugin.get_prompt_bundle().total_chars() <= 4000

    def test_genre_text_says_what_it_is_not(self, plugin) -> None:
        bundle = plugin.get_prompt_bundle()
        assert "歌词" in bundle.text_of("genre")
        assert "口播" in bundle.text_of("anti_patterns")

    def test_param_rule_keys_whitelisted(self, plugin) -> None:
        assert all(rule_key_allowed(rule.key) for rule in plugin.get_param_rules())

    def test_cut_on_beat_is_required(self, plugin) -> None:
        rules = {rule.key: rule for rule in plugin.get_param_rules()}
        assert rules["cut_on_beat"].mode == "require"
        assert rules["cut_on_beat"].enforce == "strict"

    def test_traits_match_mv_identity(self, plugin) -> None:
        traits = {trait.name: trait for trait in plugin.get_signature_traits()}
        assert traits["avg_shot_ms"].mode == "require_le"
        assert traits["text_overlay_ratio"].mode == "require_ge"

    def test_declares_search_hint_and_requires(self, plugin) -> None:
        declares = plugin.get_declares()
        assert "版画" in declares.search_hint
        assert "beat_grid" in declares.requires and "lyrics" in declares.requires


class TestRegistration:
    def test_builtin_registration_includes_lyric_mv(self) -> None:
        """内置注册函数必须包含 lyric_mv（此前它根本没注册）。

        刻意**不启动整个 app**：app 的 lifespan 会重置注册表与运行记录，在单测里
        启动它属于副作用过大的做法（实测会污染 test_pipeline_runs 的用例）。
        """
        from clipwright.category.registry import CategoryRegistry
        from clipwright.main import _register_builtin_plugins

        saved = dict(CategoryRegistry._plugins)
        try:
            CategoryRegistry._plugins.clear()
            _register_builtin_plugins()
            assert "lyric_mv" in CategoryRegistry._plugins
            assert len(CategoryRegistry._plugins) >= 5
        finally:
            CategoryRegistry._plugins.clear()
            CategoryRegistry._plugins.update(saved)


def _mv_timeline(*, clips: int = 8, clip_sec: float = 3.0, text_ratio: float = 0.9) -> Timeline:
    video_clips = [
        Clip(id=f"v{i}", kind=ClipKind.VIDEO, asset_id=f"a{i}.mp4", track_id="tv",
             start_sec=round(i * clip_sec, 3), duration_sec=clip_sec)
        for i in range(clips)
    ]
    total = clips * clip_sec
    # text_overlay_ratio 统计 TEXT 轨（歌词文字），不是 CAPTION 轨（字幕）
    text_sec = total * text_ratio
    return Timeline(
        id="tl_mv", width=1920, height=1080, fps=30, duration_sec=total,
        tracks=[
            Track(id="tv", name="视频轨", kind=ClipKind.VIDEO, index=0, clips=video_clips),
            Track(id="tt", name="文字轨", kind=ClipKind.TEXT, index=1, clips=[
                Clip(id="t0", kind=ClipKind.TEXT, asset_id="", track_id="tt",
                     start_sec=0.0, duration_sec=text_sec, text="纸上的墨还没干"),
            ]),
        ],
    )


class TestGenreCheckOnRealShapes:
    def test_mv_shaped_timeline_passes(self, spec_registered) -> None:
        from clipwright.api.type_maker import run_genre_check

        result = run_genre_check("lyric_mv", {
            "timeline": _mv_timeline().model_dump(mode="json"),
            "bpm": 128,
        })
        assert result["traits_declared"] == 2
        assert result["ok"] is True
        assert result["judge"] == "skipped"  # D7：裁判默认关，如实标注
        assert all(t["passed"] for t in result["traits"])

    def test_long_shot_timeline_fails(self, spec_registered) -> None:
        from clipwright.api.type_maker import run_genre_check

        result = run_genre_check("lyric_mv", {
            "timeline": _mv_timeline(clips=1, clip_sec=20.0, text_ratio=0.1).model_dump(mode="json"),
        })
        assert result["ok"] is False
        failed = [t["name"] for t in result["traits"] if not t["passed"]]
        assert "avg_shot_ms" in failed

    def test_unknown_type_has_no_traits(self, spec_registered) -> None:
        from clipwright.api.type_maker import run_genre_check

        result = run_genre_check("no_such_type", {
            "timeline": _mv_timeline().model_dump(mode="json"),
        })
        assert result["traits_declared"] == 0
