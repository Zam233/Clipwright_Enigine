"""R4：素材内容检索与查询工具（M1 里程碑）。

分组：
1. **结构化过滤**（零模型）：人脸/景别/运动/时长/黑场/过曝/锐度/标签/语音/音频事件/时间区间；
2. **关键词打分**：标签精确命中优先、文本覆盖率、无命中为 0；
3. **检索主入口**：命中排序、`why` 证据、空索引/空条件的说明、结构化条件前置过滤；
4. **时间查询**：`shot_at`（半开区间与边界）；
5. **上下文注入**：`index_context_text`（有索引给摘要、无索引返回 ""，且刻意短）；
6. **工具**：四种模式 + 错误路径 + 注册与 schema；
7. **Agent 接线**：RequirementsAgent 在有索引时把内容摘要写进 user_prompt，
   无索引时**零变化**（与既有注入同一约定）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from clipwright.schema.content import (
    ContentIndex,
    ShotRecord,
    ShotStats,
)
from clipwright.services import content_query as cq
from clipwright.services import content_index as ci


# ── 夹具：构造确定性索引（隔离掉模型质量，只测检索层） ──


def _shot(index: int, t0: float, t1: float, **kw) -> ShotRecord:
    visual = kw.pop("visual", {})
    speech = kw.pop("speech", {})
    audio = kw.pop("audio_events", [])
    stats = ShotStats(**kw.pop("stats", {}))
    return ShotRecord(index=index, t0=t0, t1=t1, visual=visual, speech=speech,
                      audio_events=audio, stats=stats)


def _index(sha: str = "aa" * 32, filename: str = "街拍.mp4") -> ContentIndex:
    """一段"内容已知"的素材：5 个镜头，覆盖人脸/景别/黑场/过曝/语音/音频事件。"""
    return ContentIndex(
        sha256=sha, filename=filename, duration_sec=15.0, width=1920, height=1080,
        shots=[
            _shot(0, 0.0, 3.0, visual={"description": "咖啡杯特写，手部动作", "tags": ["咖啡杯"],
                                       "shot_size": "close", "has_face": False},
                  stats={"brightness": 120.0, "sharpness": 900.0}),
            _shot(1, 3.0, 7.0, visual={"description": "人物中景说话", "tags": ["人物"],
                                       "shot_size": "medium", "has_face": True},
                  speech={"text": "这个价格很划算", "keywords": ["价格", "划算"]},
                  stats={"brightness": 110.0, "sharpness": 800.0}),
            _shot(2, 7.0, 9.0, visual={"description": "", "tags": [], "shot_size": "unknown"},
                  stats={"brightness": 2.0, "is_black": True, "frame_count": 2,
                         "underexposed_ratio": 0.99}),
            _shot(3, 9.0, 10.0, visual={"description": "观众欢呼", "tags": ["多人"],
                                        "shot_size": "wide", "camera_motion": "high"},
                  audio_events=[{"kind": "loud", "t0": 9.1, "t1": 9.8}],
                  stats={"brightness": 200.0, "motion": 30.0, "frame_count": 2}),
            _shot(4, 10.0, 15.0, visual={"description": "白墙", "tags": ["空镜"],
                                         "shot_size": "full"},
                  stats={"brightness": 250.0, "overexposed_ratio": 0.8, "frame_count": 2,
                         "underexposed_ratio": 0.0}),
        ])


@pytest.fixture()
def indexed(tmp_path, monkeypatch):
    """把"素材库"指向 tmp，并写入一份确定性索引（不跑任何模型）。"""
    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
    monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "projects", raising=False)
    idx = _index()
    ci.save_index(idx)
    return idx


def _refs(hits: list[dict[str, Any]]) -> list[str]:
    return [h["ref"] for h in hits]


# ── 1. 结构化过滤 ─────────────────────────────────────────


class TestFilterShots:
    def test_no_filter_returns_all(self) -> None:
        assert len(cq.filter_shots(_index())) == 5

    def test_has_face(self) -> None:
        got = cq.filter_shots(_index(), has_face=True)
        assert [s.index for s in got] == [1]

    def test_has_face_none_is_kept_when_not_requested(self) -> None:
        """`has_face` 三态：None 不过滤，True/False 才是硬条件。"""
        assert len(cq.filter_shots(_index(), has_face=None)) == 5
        assert [s.index for s in cq.filter_shots(_index(), has_face=False)] == [0, 2, 3, 4]

    def test_shot_size_single_and_multi(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), shot_size="close")] == [0]
        assert [s.index for s in cq.filter_shots(_index(), shot_size=["wide", "full"])] == [3, 4]

    def test_motion(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), motion="high")] == [3]

    def test_duration_range(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), min_duration=4.0)] == [1, 4]
        assert [s.index for s in cq.filter_shots(_index(), max_duration=3.0)] == [0, 2, 3]

    def test_black_and_white(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), is_black=True)] == [2]
        assert cq.filter_shots(_index(), is_white=True) == []
        assert cq.filter_shots(_index(), is_black=False)[0].index == 0

    def test_brightness_range(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), max_brightness=50)] == [2]
        assert [s.index for s in cq.filter_shots(_index(), min_brightness=200)] == [3, 4]

    def test_quality_ratios(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), max_overexposed=0.1)] == [0, 1, 2, 3]
        assert [s.index for s in cq.filter_shots(_index(), max_underexposed=0.1)] == [0, 1, 3, 4]

    def test_min_sharpness(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), min_sharpness=850)] == [0]

    def test_tags_any_match(self) -> None:
        # 多标签是 OR；结果保持镜头顺序（不再排序，调用方按需排）
        assert [s.index for s in cq.filter_shots(_index(), tags=["人物", "咖啡杯"])] == [0, 1]

    def test_has_speech(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), has_speech=True)] == [1]
        assert len(cq.filter_shots(_index(), has_speech=False)) == 4

    def test_event_kinds(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), event_kinds=["loud"])] == [3]

    def test_time_range(self) -> None:
        assert [s.index for s in cq.filter_shots(_index(), time_range=(3.5, 8.5))] == [1, 2]

    def test_combined_conditions(self) -> None:
        got = cq.filter_shots(_index(), has_face=True, min_duration=3.0)
        assert [s.index for s in got] == [1]

    def test_impossible_condition(self) -> None:
        assert cq.filter_shots(_index(), has_face=True, shot_size="wide") == []


# ── 2. 关键词打分 ─────────────────────────────────────────


class TestKeywordScore:
    def test_tag_hit_scores_highest(self) -> None:
        idx = _index()
        score, why = cq.keyword_score(idx.shots[0], "找咖啡杯特写")
        assert score == pytest.approx(cq.TAG_WEIGHT)
        assert any("标签命中" in w for w in why)

    def test_text_hit_without_tag(self) -> None:
        idx = _index()
        score, why = cq.keyword_score(idx.shots[1], "价格划算吗")
        assert 0 < score <= 1.0
        assert any("关键词命中" in w for w in why)

    def test_no_hit_is_zero(self) -> None:
        score, why = cq.keyword_score(_index().shots[2], "咖啡杯")
        assert score == 0.0 and why == []

    def test_empty_query(self) -> None:
        assert cq.keyword_score(_index().shots[0], "") == (0.0, [])

    def test_speech_keywords_count(self) -> None:
        score, _ = cq.keyword_score(_index().shots[1], "划算")
        assert score > 0


# ── 3. 检索主入口 ─────────────────────────────────────────


class TestSearch:
    def test_search_finds_tagged_shot_first(self, indexed) -> None:
        res = asyncio.run(cq.search_shots("咖啡杯", use_embeddings=False, rerank=False))
        assert res["hits"], res
        assert res["hits"][0]["ref"] == "asset:aaaaaaaaaaaa#s0"
        assert res["hits"][0]["why"]

    def test_search_with_filters_prefilters(self, indexed) -> None:
        res = asyncio.run(cq.search_shots(
            "人物", use_embeddings=False, rerank=False, filters={"has_face": True}))
        assert [h["index"] for h in res["hits"]] == [1]

    def test_filters_can_exclude_everything(self, indexed) -> None:
        """条件互斥 → 候选为空，提示必须指向"结构化条件"而不是"得分不够"。"""
        res = asyncio.run(cq.search_shots(
            "咖啡杯", use_embeddings=False, rerank=False,
            filters={"has_face": True, "is_black": True}))
        assert res["hits"] == []
        assert "结构化条件" in res["note"]

    def test_filter_passes_but_query_scores_zero(self, indexed) -> None:
        """条件筛出了镜头但查询词对它无关：**仍要返回该镜头**（条件即答案），
        并在 `why` 里标明是条件命中——否则"黑场在哪"这种问题永远问不出结果。"""
        res = asyncio.run(cq.search_shots(
            "咖啡杯", use_embeddings=False, rerank=False, filters={"is_black": True}))
        assert [h["index"] for h in res["hits"]] == [2]
        assert "结构化条件命中" in res["hits"][0]["why"]
        assert res["hits"][0]["score"] == pytest.approx(cq.MIN_SCORE)

    def test_list_mode_for_list_questions(self, indexed) -> None:
        """清单式问法（"哪些镜头有人脸"）直接给符合条件的清单，不要求文本命中。"""
        res = asyncio.run(cq.search_shots("哪些镜头有人脸", use_embeddings=False, rerank=False))
        assert res["list_mode"] is True
        assert [h["index"] for h in res["hits"]] == [1]

    def test_order_only_question_lists_sorted(self, indexed) -> None:
        """"最长的镜头"是排序而不是过滤：文本分全 0 时必须给有序清单而不是空结果。"""
        res = asyncio.run(cq.search_shots("最长的那一个镜头", use_embeddings=False, rerank=False))
        assert res["list_mode"] is True
        assert res["hits"][0]["index"] == 4          # 10.0–15.0s，5s 最长
        assert "排序" in res["hits"][0]["why"][0]

    def test_no_indexes(self, tmp_path, monkeypatch) -> None:
        lib = tmp_path / "empty"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        res = asyncio.run(cq.search_shots("任何", use_embeddings=False))
        assert res["hits"] == [] and "索引" in res["note"]

    def test_low_score_filtered_out(self, indexed) -> None:
        res = asyncio.run(cq.search_shots("完全无关的查询词", use_embeddings=False,
                                          rerank=False, min_score=0.2))
        assert res["hits"] == []

    def test_top_k_limits(self, indexed) -> None:
        res = asyncio.run(cq.search_shots("人", use_embeddings=False, rerank=False, top_k=2))
        assert len(res["hits"]) <= 2

    def test_embeddings_unavailable_degrades_to_keyword(self, indexed, monkeypatch) -> None:
        """嵌入不可用（未装/加载失败）时必须退化为关键词，而不是报错或返回空。"""
        monkeypatch.setattr(cq, "_embed_texts", lambda texts: [])
        res = asyncio.run(cq.search_shots("咖啡杯", use_embeddings=True, rerank=False))
        assert res["hits"] and res["embedding_used"] is False

    def test_rerank_failure_keeps_order(self, indexed, monkeypatch) -> None:
        class _Boom:
            async def rerank(self, *a, **k):
                raise RuntimeError("重排挂了")

        monkeypatch.setattr("clipwright.rag.reranker.Reranker", _Boom)
        res = asyncio.run(cq.search_shots("咖啡杯", use_embeddings=False, rerank=True))
        assert res["hits"][0]["ref"] == "asset:aaaaaaaaaaaa#s0"

    def test_render_hits_is_compact(self) -> None:
        text = cq.render_hits("q", [{
            "t0": 0.0, "t1": 1.0, "filename": "a.mp4", "index": 0, "score": 0.9,
            "text": "描述", "why": ["标签命中 X"],
        }], 5)
        assert text.count("\n") <= 2 and "0.9" in text

    def test_render_hits_empty(self) -> None:
        assert cq.render_hits("q", [], 0) == ""


class TestRouteQuery:
    """自然语言 → 结构化条件/排序的路由（R4 的关键：用户不会说 is_black=true）。"""

    @pytest.mark.parametrize("question,key,value", [
        ("黑场在哪一段", "is_black", True),
        ("有没有全白的画面", "is_white", True),
        ("哪些镜头有人脸", "has_face", True),
        ("找空镜", "has_face", False),
        ("特写的镜头", "shot_size", ["extreme_close", "close"]),
        ("全景镜头", "shot_size", ["wide", "full"]),
        ("有掌声的那段", "event_kinds", ["loud"]),
        ("静音的片段", "event_kinds", ["silence"]),
        ("有语音的镜头", "has_speech", True),
        ("静止不动的镜头", "motion", ["static", "low"]),
        ("运动剧烈的镜头", "motion", ["medium", "high"]),
        # R10 端到端 demo 发现的缺口：不带程度词的"运动的"原本没被路由，
        # 于是"有哪些画面是运动的"变成清单查询、把**所有**镜头都列出来（看着有输出、其实没过滤）
        ("有哪些画面是运动的？", "motion", ["medium", "high"]),
        ("有动感的段落", "motion", ["medium", "high"]),
    ])
    def test_filter_routing(self, question: str, key: str, value: Any) -> None:
        assert cq.route_query(question)["filters"].get(key) == value

    def test_overexposed_is_partial_not_white(self) -> None:
        """"过曝"是局部高光溢出，不等于全白画面（实测路由成 is_white 会一个都筛不出）。"""
        routed = cq.route_query("过曝的镜头")
        assert routed["filters"] == {"min_overexposed": 0.3}

    @pytest.mark.parametrize("question,field,desc", [
        ("最长的镜头", "duration", True),
        ("最短的一段", "duration", False),
        ("最亮的那个镜头", "brightness", True),
        ("最模糊的镜头", "sharpness", False),
    ])
    def test_order_routing(self, question: str, field: str, desc: bool) -> None:
        assert cq.route_query(question)["order_by"] == (field, desc)

    def test_list_detection(self) -> None:
        assert cq.route_query("列出所有镜头")["list_all"] is True

    def test_list_question_still_applies_filters(self) -> None:
        """清单式问法（"有哪些…"）不能**吞掉**结构化条件，否则会列出不相关的镜头。"""
        routed = cq.route_query("有哪些画面是运动的？")
        assert routed["list_all"] is True
        assert routed["filters"].get("motion") == ["medium", "high"]
        assert cq.route_query("素材里一共有多少个镜头")["list_all"] is True
        assert cq.route_query("找咖啡杯")["list_all"] is False

    def test_clean_query_drops_structural_words(self) -> None:
        """命中的结构化说法必须从查询词里剔除，否则会污染关键词打分。"""
        cleaned = cq.route_query("哪些镜头有人脸")["clean_query"]
        assert "人脸" not in cleaned and "哪些" not in cleaned

    def test_filler_words_removed(self) -> None:
        assert cq.route_query("最长的那一个镜头")["clean_query"] == ""

    def test_plain_query_untouched(self) -> None:
        routed = cq.route_query("咖啡杯特写")
        assert routed["filters"] == {} or "shot_size" in routed["filters"]
        assert routed["order_by"] is None
        assert "咖啡杯" in routed["clean_query"]

    def test_route_can_be_disabled(self, indexed) -> None:
        """`route=False` 时按原样当关键词查（给调用方留一条确定性通路）。"""
        res = asyncio.run(cq.search_shots("哪些镜头有人脸", use_embeddings=False,
                                          rerank=False, route=False))
        assert res.get("list_mode", False) is False

    def test_explicit_filters_win_over_routing(self, indexed) -> None:
        res = asyncio.run(cq.search_shots(
            "黑场在哪", use_embeddings=False, rerank=False, filters={"has_face": True}))
        assert [h["index"] for h in res["hits"]] == [1]   # 显式条件优先，不被路由覆盖


class TestVisualOnlyHitsSurvive:
    """真实素材实测修的两个缺陷（合成基准里完全没暴露，因为那时是直接调
    `sem.rank_shots(min_score=0)`，从没走 `search_shots` 的融合 + 门槛）。

    缺陷现象：CLIP 视觉分**排序完全正确**（海浪镜头排第一），但融合分只有
    0.04 左右，被绝对的 `min_score=0.05` 整体吃掉 → 所有真实素材查询返回 0 命中。
    根因：把绝对阈值套在"尺度随可用信号组合变化"的融合分上。
    """

    def _index_with_visual(self) -> ContentIndex:
        from clipwright.services import content_semantic as sem

        def _shot(i: int, vec: list[float], text: str) -> ShotRecord:
            s = ShotRecord(index=i, t0=float(i), t1=float(i) + 1, duration_sec=1.0,
                           index_text=text)
            s.embeddings = {sem.VISUAL_MODALITY: vec}
            return s

        # 真实素材实测的量级：命中约 0.27，不相关约 0.16
        return ContentIndex(sha256="cc" * 32, asset_path="real.mp4", filename="real.mp4",
                            shots=[_shot(0, [1.0, 0.0], "水面"),
                                   _shot(1, [0.0, 1.0], "食物")])

    def test_visual_only_match_is_returned(self, monkeypatch) -> None:
        from clipwright.services import content_semantic as sem

        monkeypatch.setattr(cq, "_embed_texts", lambda texts: [])       # 文本向量不可用
        # 视觉：镜头 0 明显更像（0.27），镜头 1 不相关（0.16）
        monkeypatch.setattr(sem, "semantic_scores",
                            lambda q, shots, **kw: {0: 0.27, 1: 0.16})
        idx = self._index_with_visual()
        res = asyncio.run(cq.search_shots("找海边的镜头", indexes=[idx],
                                          rerank=False, min_score=0.05))
        assert [h["index"] for h in res["hits"]] == [0], res
        assert res["hits"][0]["visual"] == 0.27
        assert any("画面像" in w for w in res["hits"][0]["why"])

    def test_unrelated_visual_is_still_rejected(self, monkeypatch) -> None:
        """放宽门槛不等于"什么都返回"：低于 VISUAL_FLOOR 的必须仍然被拒。"""
        from clipwright.services import content_semantic as sem

        monkeypatch.setattr(cq, "_embed_texts", lambda texts: [])
        monkeypatch.setattr(sem, "semantic_scores",
                            lambda q, shots, **kw: {0: 0.21, 1: 0.16})
        idx = self._index_with_visual()
        res = asyncio.run(cq.search_shots("找海边的镜头", indexes=[idx], rerank=False))
        assert res["hits"] == []
        assert "画面" in res["note"]

    def test_visual_score_affects_ranking(self, monkeypatch) -> None:
        from clipwright.services import content_semantic as sem

        monkeypatch.setattr(cq, "_embed_texts", lambda texts: [])
        monkeypatch.setattr(sem, "semantic_scores",
                            lambda q, shots, **kw: {0: 0.24, 1: 0.30})
        idx = self._index_with_visual()
        res = asyncio.run(cq.search_shots("某个画面", indexes=[idx], rerank=False))
        assert [h["index"] for h in res["hits"]] == [1, 0]


class TestDegradationIsLoud:
    """降级必须**可见**：embedder 挂了与"素材里确实没有"是两件完全不同的事。

    真实素材实测：本机 `.env` 把嵌入 provider 指向一个无效的云端 key，
    于是文本向量路静默消失，所有查询返回 0 命中——不给出降级原因根本查不出来。
    """

    def test_no_hit_note_lists_degraded_reasons(self, monkeypatch) -> None:
        monkeypatch.setattr(cq, "_embed_texts", lambda texts: [])
        idx = ContentIndex(sha256="dd" * 32, asset_path="x.mp4", filename="x.mp4",
                           shots=[ShotRecord(index=0, t0=0.0, t1=1.0, duration_sec=1.0)])
        res = asyncio.run(cq.search_shots("海边", indexes=[idx], rerank=False))
        assert res["hits"] == []
        reasons = res.get("degraded") or []
        assert any("文本向量" in r for r in reasons)
        assert any("画面向量" in r for r in reasons)
        assert "降级原因" in res["note"]

    def test_successful_search_reports_signal_flags(self, indexed) -> None:
        res = asyncio.run(cq.search_shots("咖啡", rerank=False))
        assert "embedding_used" in res and "visual_used" in res


class TestShotAt:
    def test_inside_shot(self, indexed) -> None:
        hit = asyncio.run(cq.shot_at(4.5))
        assert hit and hit["index"] == 1 and hit["has_face"] is True

    def test_boundary_belongs_to_later_shot(self, indexed) -> None:
        assert asyncio.run(cq.shot_at(3.0))["index"] == 1

    def test_out_of_range(self, indexed) -> None:
        assert asyncio.run(cq.shot_at(99.0)) is None
        assert asyncio.run(cq.shot_at(-1.0)) is None

    def test_reports_flags(self, indexed) -> None:
        hit = asyncio.run(cq.shot_at(8.0))
        assert hit["is_black"] is True and hit["shot_size"] == "unknown"


class TestAssetsOverview:
    def test_overview_rows(self, indexed) -> None:
        rows = cq.assets_overview(cq.load_indexes())
        assert len(rows) == 1
        row = rows[0]
        assert row["shot_count"] == 5 and row["with_face"] == 1 and row["black_shots"] == 1
        assert "咖啡杯" in row["top_tags"]

    def test_load_indexes_by_sha(self, indexed) -> None:
        assert len(cq.load_indexes(sha256="aa" * 32)) == 1
        assert cq.load_indexes(sha256="bb" * 32) == []


# ── 5. 上下文注入 ─────────────────────────────────────────


class TestIndexContext:
    def test_with_index_returns_compact_text(self, indexed) -> None:
        text = cq.index_context_text(max_assets=1, max_shots=3)
        assert text and "街拍.mp4" in text
        assert "5 个镜头" in text
        # 刻意很短：1 行头 + 最多 3 个镜头样本
        assert len(text.splitlines()) <= 4

    def test_without_index_returns_empty(self, tmp_path, monkeypatch) -> None:
        lib = tmp_path / "empty"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        assert cq.index_context_text() == ""

    def test_more_assets_than_limit_is_noted(self, tmp_path, monkeypatch) -> None:
        lib = tmp_path / "library"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        ci.save_index(_index(sha="11" * 32, filename="a.mp4"))
        ci.save_index(_index(sha="22" * 32, filename="b.mp4"))
        text = cq.index_context_text(max_assets=1, max_shots=1)
        assert "另有 1 个素材" in text


# ── 6. 工具 ───────────────────────────────────────────────


def _run_tool(**kwargs):
    from clipwright.tool.content_query import ContentQueryTool

    return asyncio.run(ContentQueryTool().execute(**kwargs))


class TestContentQueryTool:
    def test_summary_mode(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="summary")
        assert res.status == ToolStatus.SUCCESS
        assert res.output["count"] == 1 and res.output["assets"][0]["filename"] == "街拍.mp4"

    def test_summary_without_index(self, tmp_path, monkeypatch) -> None:
        from clipwright.schema.tool import ToolStatus

        lib = tmp_path / "empty"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        res = _run_tool(mode="summary")
        assert res.status == ToolStatus.SUCCESS and res.output["count"] == 0

    def test_search_mode(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="search", query="咖啡杯", use_embeddings=False, rerank=False)
        assert res.status == ToolStatus.SUCCESS
        assert res.output["hits"][0]["ref"] == "asset:aaaaaaaaaaaa#s0"

    def test_search_requires_query(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="search")
        assert res.status == ToolStatus.ERROR and "query" in (res.error or "")

    def test_filter_mode(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="filter", filters={"is_black": True})
        assert res.status == ToolStatus.SUCCESS
        assert res.output["count"] == 1 and res.output["hits"][0]["index"] == 2

    def test_filter_requires_filters(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="filter")
        assert res.status == ToolStatus.ERROR and "filters" in (res.error or "")

    def test_shot_at_mode(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="shot_at", t=4.0)
        assert res.status == ToolStatus.SUCCESS and res.output["hit"]["index"] == 1

    def test_shot_at_requires_t(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="shot_at")
        assert res.status == ToolStatus.ERROR and "t" in (res.error or "")

    def test_shot_at_miss_is_success_with_note(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="shot_at", t=500.0)
        assert res.status == ToolStatus.SUCCESS and res.output["hit"] is None

    def test_unknown_mode(self, indexed) -> None:
        from clipwright.schema.tool import ToolStatus

        res = _run_tool(mode="delete")
        assert res.status == ToolStatus.ERROR and "未知 mode" in (res.error or "")

    def test_registered_with_schema(self) -> None:
        from clipwright.tool import register_builtin_tools
        from clipwright.tool.content_query import ContentQueryTool
        from clipwright.tool.registry import ToolRegistry

        register_builtin_tools()
        tools = ToolRegistry.list()
        names = [getattr(t, "name", str(t)) for t in (tools.values() if isinstance(tools, dict) else tools)]
        assert "content_query" in names
        schema = ContentQueryTool().to_llm_tool()
        assert schema["name"] == "content_query"
        assert "mode" in schema["input_schema"]["properties"]


# ── 7. Agent 接线 ─────────────────────────────────────────


class TestRequirementsWiring:
    def _agent(self) -> Any:
        from clipwright.agents.requirements_agent import RequirementsAgent
        from clipwright.schema.agent import AgentContext, RequirementsInput

        agent = RequirementsAgent()
        agent._llm = AsyncMock()
        captured: dict[str, str] = {}

        async def _structured_output(system_prompt="", user_prompt="", **kwargs):
            captured["user_prompt"] = user_prompt
            captured["system_prompt"] = system_prompt
            return {"brief_draft": {"title": "T", "overview": "O", "key_points": []}}

        agent._llm.structured_output = _structured_output
        inp = RequirementsInput(context=AgentContext(
            pipeline_id="p1", persona_id="default", category_plugin_id="vlog_daily",
            topic="主题", extra_params={}), topic="主题")
        return agent, inp, captured

    def test_index_context_injected_when_available(self, indexed) -> None:
        agent, inp, captured = self._agent()
        asyncio.run(agent._generate_brief(inp, inp.context))
        assert "已建索引的素材内容" in captured["user_prompt"]
        assert "街拍.mp4" in captured["user_prompt"]

    def test_zero_change_without_index(self, tmp_path, monkeypatch) -> None:
        lib = tmp_path / "empty"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        agent, inp, captured = self._agent()
        asyncio.run(agent._generate_brief(inp, inp.context))
        assert "已建索引的素材内容" not in captured["user_prompt"]
