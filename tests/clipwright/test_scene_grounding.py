"""R8：方案接地（场景 → 真实镜头引用）单元测试。

测试的重点不是"能不能匹配上"，而是**接地的诚实性**：

- 匹配不到就**如实留白**（`material_gap`），不许拿最接近的凑数——凑数的引用比没有引用更坏，
  它会让下游以为这一场有素材；
- 引用**必须真实存在**（`validate_material_refs` 是 R8 的验收不变量：占比 = 0）；
- 结构化条件（"全景/特写"）不命中时**退化为文本匹配**而不是直接报"没素材"——
  因为 R2 对看不出景别的镜头如实写 `unknown`，硬过滤会把真素材全滤掉；
- 接地失败/无索引时**一律降级**，不能让管线挂掉。
"""

from __future__ import annotations

import pytest

from clipwright.schema.content import (
    ContentIndex,
    ShotRecord,
    ShotStats,
    ShotVisual,
    format_shot_ref,
)
from clipwright.services import scene_grounding as sg

SHA = "a" * 64


def index_of(*shots: ShotRecord, sha: str = SHA, path: str = "match.mp4") -> ContentIndex:
    return ContentIndex(sha256=sha, asset_path=path, filename=path, shots=list(shots))


def shot(i: int, t0: float, t1: float, desc: str = "", tags: tuple[str, ...] = (),
         **kwargs) -> ShotRecord:
    stats = ShotStats(**kwargs.pop("stats", {}))
    return ShotRecord(index=i, t0=t0, t1=t1, duration_sec=round(t1 - t0, 3), stats=stats,
                      visual=ShotVisual(description=desc, tags=list(tags), **kwargs))


FOOTBALL = index_of(
    shot(0, 0.0, 3.0, "球场全景，观众席", ["足球", "球场"]),
    shot(1, 3.0, 6.0, "球员射门得分", ["足球", "进球"]),
    shot(2, 6.0, 9.0, "球员庆祝", ["足球", "庆祝"]),
)


# ── 检索词构造 ────────────────────────────────────────────


class TestSceneQueryText:
    def test_prefers_visual_description(self):
        text = sg.scene_query_text({"visual_description": "球员射门", "description": "开场"})
        assert text.startswith("球员射门")

    def test_includes_keywords_list(self):
        text = sg.scene_query_text({"title": "开场", "keywords": ["足球", "球场"]})
        assert "足球" in text and "球场" in text

    def test_ignores_empty_and_non_string(self):
        assert sg.scene_query_text({"title": "", "description": None, "keywords": []}) == ""

    def test_is_length_capped(self):
        assert len(sg.scene_query_text({"description": "球" * 1000})) <= 400


# ── 单个场景接地 ──────────────────────────────────────────


class TestGroundScene:
    def test_keyword_hit_pins_real_ref(self):
        """最相关的镜头必须排在第一位（它才是下游首选的那一刀）。

        这里"球员"同时命中 s1（射门得分）与 s2（庆祝），所以不要求只有一个引用——
        关键词打分是**排序**手段，不是精确过滤；精确过滤会让"库里差一点就有"变成"没有"。
        """
        out = sg.ground_scene({"description": "球员射门进球", "duration_sec": 5}, [FOOTBALL])
        assert out["material_refs"][0] == format_shot_ref(SHA, 1)
        assert format_shot_ref(SHA, 1) in out["material_refs"]

    def test_ref_format_is_content_addressed(self):
        out = sg.ground_scene({"description": "球场全景", "duration_sec": 5}, [FOOTBALL])
        for ref in out["material_refs"]:
            assert ref.startswith("asset:") and "#s" in ref

    def test_no_related_content_yields_gap_not_filler(self):
        """素材库里没有相关画面时**不许凑数**——宁可留白。"""
        out = sg.ground_scene({"description": "一碗牛肉面的特写", "duration_sec": 5}, [FOOTBALL])
        assert out["material_refs"] == []
        assert "没有与" in out["material_gap"]

    def test_structural_filter_falls_back_to_text(self):
        """描述里的"全景/特写"会变成 shot_size 硬条件，但索引多半是 unknown——
        此时必须退化为文本匹配，否则明明有素材却报"没料"。"""
        out = sg.ground_scene({"description": "球场全景", "duration_sec": 5}, [FOOTBALL])
        assert out["material_refs"], out
        assert out["material_match"] == "degraded"
        assert "退化" in out.get("material_note", "")

    def test_structural_filter_used_when_it_hits(self):
        idx = index_of(shot(0, 0.0, 3.0, "球场全景", shot_size="wide"),
                       shot(1, 3.0, 6.0, "球场全景", shot_size="close"))
        out = sg.ground_scene({"description": "球场全景", "duration_sec": 5}, [idx])
        assert out["material_match"] == "filtered"
        assert out["material_refs"] == [format_shot_ref(SHA, 0)]

    def test_max_refs_is_respected(self):
        idx = index_of(*[shot(i, i * 2.0, i * 2.0 + 2.0, "球场") for i in range(6)])
        out = sg.ground_scene({"description": "球场", "duration_sec": 5}, [idx], max_refs=2)
        assert len(out["material_refs"]) == 2

    def test_existing_valid_refs_are_kept(self):
        """人工指定/上一轮已接地的引用不能被检索结果覆盖。"""
        keep = format_shot_ref(SHA, 2)
        out = sg.ground_scene(
            {"description": "球员射门", "material_refs": [keep], "duration_sec": 5}, [FOOTBALL])
        assert out["material_refs"] == [keep]

    def test_existing_bogus_refs_are_replaced(self):
        out = sg.ground_scene(
            {"description": "球员射门", "material_refs": ["asset:deadbeef#s9"],
             "duration_sec": 5}, [FOOTBALL])
        assert "asset:deadbeef#s9" not in out["material_refs"]
        assert out["material_refs"][0] == format_shot_ref(SHA, 1)

    def test_drops_material_gap_when_refs_found(self):
        out = sg.ground_scene({"description": "球员射门", "material_gap": "旧缺口",
                               "duration_sec": 5}, [FOOTBALL])
        assert "material_gap" not in out

    def test_no_indexes_yields_gap(self):
        out = sg.ground_scene({"description": "球员射门", "duration_sec": 5}, [])
        assert out["material_refs"] == []
        assert "没有素材索引" in out["material_gap"]

    def test_original_scene_is_not_mutated(self):
        scene = {"description": "球员射门", "duration_sec": 5}
        sg.ground_scene(scene, [FOOTBALL])
        assert "material_refs" not in scene

    def test_scores_are_reported_for_traceability(self):
        out = sg.ground_scene({"description": "球员射门进球", "duration_sec": 5}, [FOOTBALL])
        assert set(out["material_scores"]) == set(out["material_refs"])


# ── 批量接地 ──────────────────────────────────────────────


class TestGroundScenes:
    def test_grounds_each_scene(self):
        scenes = [
            {"title": "开场", "description": "球场全景", "duration_sec": 5},
            {"title": "进球", "description": "球员射门得分", "duration_sec": 6},
        ]
        out, notes = sg.ground_scenes(scenes, [FOOTBALL])
        assert len(out) == 2
        assert all(s["material_refs"] for s in out)
        assert any("2/2" in n for n in notes)

    def test_reports_missing_indexes_once(self):
        out, notes = sg.ground_scenes([{"title": "x", "description": "y"}], [])
        assert out[0]["material_refs"] == []
        assert any("没有内容索引" in n for n in notes)

    def test_notes_each_gap(self):
        out, notes = sg.ground_scenes(
            [{"title": "美食", "description": "牛肉面特写"}], [FOOTBALL])
        assert out[0]["material_refs"] == []
        assert any("未接上素材" in n for n in notes)

    def test_empty_input(self):
        assert sg.ground_scenes([], [FOOTBALL]) == ([], [])

    def test_non_dict_entries_are_skipped(self):
        out, _notes = sg.ground_scenes([{"title": "a", "description": "球场"}, "bad"], [FOOTBALL])
        assert len(out) == 1

    def test_multi_asset_search_covers_all_indexes(self):
        other = index_of(shot(0, 0.0, 2.0, "城市夜景", ["城市"]), sha="b" * 64, path="city.mp4")
        out, _notes = sg.ground_scenes(
            [{"title": "夜景", "description": "城市夜景"}], [FOOTBALL, other])
        assert out[0]["material_refs"] == [format_shot_ref("b" * 64, 0)]


# ── 不变量校验（R8 验收指标）──────────────────────────────


class TestValidateMaterialRefs:
    def test_all_valid_returns_empty(self):
        scenes = [{"material_refs": [format_shot_ref(SHA, 0)]}]
        assert sg.validate_material_refs(scenes, [FOOTBALL]) == []

    def test_unknown_ref_is_reported(self):
        scenes = [{"material_refs": ["asset:ffffffffffff#s0"]}]
        problems = sg.validate_material_refs(scenes, [FOOTBALL])
        assert len(problems) == 1 and "不存在" in problems[0]

    def test_gap_without_refs_is_not_an_error(self):
        """库里确实没有 → 如实留白，不是错；硬编一个才算错。"""
        assert sg.validate_material_refs([{"material_refs": [], "material_gap": "没有"}], [FOOTBALL]) == []

    def test_refs_and_gap_together_is_contradiction(self):
        scenes = [{"material_refs": [format_shot_ref(SHA, 0)], "material_gap": "没有"}]
        assert any("矛盾" in p for p in sg.validate_material_refs(scenes, [FOOTBALL]))

    def test_non_list_refs_reported(self):
        assert sg.validate_material_refs([{"material_refs": "asset:x#s0"}], [FOOTBALL])

    def test_missing_key_is_fine(self):
        assert sg.validate_material_refs([{"title": "无引用字段"}], [FOOTBALL]) == []

    def test_grounded_scenes_pass_invariant(self):
        """端到端不变量：接地产出的规划书，引用不存在素材的占比 = 0。"""
        scenes = [
            {"title": "开场", "description": "球场全景"},
            {"title": "进球", "description": "球员射门得分"},
            {"title": "美食", "description": "牛肉面特写"},
            {"title": "空场景", "description": ""},
        ]
        out, _notes = sg.ground_scenes(scenes, [FOOTBALL])
        assert sg.validate_material_refs(out, [FOOTBALL]) == []

    def test_wrong_asset_sha_is_reported(self):
        idx = index_of(shot(0, 0.0, 2.0, "球场"), sha="c" * 64)
        scenes = [{"material_refs": [format_shot_ref(SHA, 0)]}]      # sha 对不上
        assert sg.validate_material_refs(scenes, [idx])


# ── 下游承接 ──────────────────────────────────────────────


class TestDownstream:
    def test_clip_metadata_carries_refs(self):
        meta = sg.refs_to_clip_metadata({"material_refs": [format_shot_ref(SHA, 1)]})
        assert meta == {"material_refs": [format_shot_ref(SHA, 1)]}

    def test_clip_metadata_is_capped(self):
        refs = [format_shot_ref(SHA, i) for i in range(9)]
        meta = sg.refs_to_clip_metadata({"material_refs": refs})
        assert len(meta["material_refs"]) == sg.MAX_REFS_PER_SCENE

    def test_clip_metadata_carries_gap(self):
        meta = sg.refs_to_clip_metadata({"material_gap": "库里没有"})
        assert meta == {"material_gap": "库里没有"}

    def test_clip_metadata_empty_when_ungrounded(self):
        """没有接地的场景不产生空 key（避免时间线上出现一堆空 metadata）。"""
        assert sg.refs_to_clip_metadata({"title": "x"}) == {}

    def test_summary_text_lists_refs_and_gaps(self):
        text = sg.summary_text([
            {"title": "进球", "material_refs": [format_shot_ref(SHA, 1)]},
            {"title": "美食", "material_gap": "库里没有"},
        ])
        assert "进球" in text and format_shot_ref(SHA, 1) in text
        assert "无可用素材" in text

    def test_summary_respects_limit(self):
        scenes = [{"title": f"s{i}", "material_refs": ["asset:x#s0"]} for i in range(20)]
        assert len(sg.summary_text(scenes, limit=3).splitlines()) == 3

    def test_load_project_indexes_never_raises(self, monkeypatch):
        """索引读失败必须降级为空列表——接地失败不该让管线挂掉。"""
        from clipwright.services import content_query

        def boom(*a, **kw):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(content_query, "load_indexes", boom)
        assert sg.load_project_indexes("p1") == []
