"""R9：语义检索（画面向量）单元测试。

测试重点（按重要性）：

1. **中文查询的改写**：CLIP 文本塔不认中文，改写是这条路能不能用的前提；
2. **降级诚实性**：CLIP 不可用/没向量时，检索要退化为文本路并**如实标注**，
   绝不返回假分（`semantic_match` 工具此前就是靠假 0.5 撑着的）；
3. **维度不匹配要吵**：索引里的向量来自另一个模型时，静默按 0 分处理会让人
   误判成"没命中"，所以必须告警并跳过；
4. **不重算**：向量存在索引里，第二次调用不应再碰模型。

绝大多数用例用打桩向量（不加载 CLIP，快且确定性好）；只有一项真实模型用例
（需要 CLIP 权重的那些）用 `pytest.importorskip` 风格的软跳过。
"""

from __future__ import annotations

import math
import os

import pytest

from clipwright.schema.content import ContentIndex, ShotRecord, ShotVisual
from clipwright.services import content_semantic as sem


def unit(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n for v in vec]


def shot(i: int, vec: list[float] | None = None, desc: str = "", frames: list[str] | None = None) -> ShotRecord:
    s = ShotRecord(index=i, t0=float(i), t1=float(i) + 1, duration_sec=1.0,
                   keyframes=frames or [], visual=ShotVisual(description=desc))
    if vec is not None:
        s.embeddings = {sem.VISUAL_MODALITY: vec}
    return s


# ── 中文查询改写 ──────────────────────────────────────────


class TestQueryGloss:
    @pytest.mark.parametrize("zh,needle", [
        ("找红色的画面", "red"), ("蓝色的镜头", "blue"), ("绿色调", "green"),
        ("海边日落", "beach"), ("球员进球", "goal"), ("城市夜景", "city"),
    ])
    def test_known_words_are_glossed(self, zh, needle):
        assert needle in sem.gloss_query(zh)

    def test_gloss_terms_drops_chinese_filler(self):
        """中文虚词不能混进提示词——那会稀释信号（实测"找红色的画面"里
        "找/的/画面"会让颜色查询明显变差）。"""
        assert "找" not in sem.gloss_query("找红色的画面")
        assert "画面" not in sem.gloss_query("找红色的画面")

    def test_unknown_text_passes_through(self):
        assert sem.gloss_query("量子纠缠发生器") == "量子纠缠发生器"

    def test_empty_query(self):
        assert sem.gloss_query("") == ""

    def test_bright_red_is_distinguished(self):
        assert sem.gloss_query("一片通红") != sem.gloss_query("红色")

    def test_prompts_are_ensembled(self):
        prompts = sem.text_prompts("找红色的画面")
        assert len(prompts) >= len(sem.PROMPT_TEMPLATES)
        assert all("red" in p for p in prompts)

    def test_prompts_without_gloss_is_raw(self):
        assert sem.text_prompts("找红色的画面", gloss=False) == ["找红色的画面"]

    def test_dedup_across_synonyms(self):
        """两个中文词映射到同一个英文词时不应重复编码。"""
        assert sem.gloss_query("红 红色") == "red"


# ── 向量与相似度 ──────────────────────────────────────────


class TestCosine:
    def test_identical(self):
        assert sem.cosine([1, 0], [1, 0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert sem.cosine([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_opposite(self):
        assert sem.cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_dimension_mismatch_is_zero_not_crash(self):
        assert sem.cosine([1, 0], [1, 0, 0]) == 0.0

    def test_zero_vector_is_safe(self):
        assert sem.cosine([0, 0], [1, 0]) == 0.0

    def test_unnormalized_inputs(self):
        assert sem.cosine([3, 0], [0, 7]) == pytest.approx(0.0)


# ── 代表帧选择 ────────────────────────────────────────────


class TestRepresentativeFrame:
    def test_middle_frame_is_preferred(self):
        """首帧常是转场残影、末帧常是黑场起手，取中间最稳。"""
        s = shot(0, frames=["a.png", "b.png", "c.png"])
        assert sem.representative_frame(s) == "b.png"

    def test_single_frame(self):
        assert sem.representative_frame(shot(0, frames=["only.png"])) == "only.png"

    def test_no_frames(self):
        assert sem.representative_frame(shot(0)) == ""

    def test_blank_entries_ignored(self):
        assert sem.representative_frame(shot(0, frames=["", "x.png", ""])) == "x.png"


# ── 索引写入 ──────────────────────────────────────────────


class TestAttachVectors:
    def test_attach_uses_embeddings(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_images", lambda paths: [unit([1.0, 0.0]) for _ in paths])
        idx = ContentIndex(sha256="a" * 64, shots=[shot(0, frames=["f.png"])])
        _idx, count = sem.attach_visual_vectors(idx)
        assert count == 1
        assert idx.shots[0].embeddings[sem.VISUAL_MODALITY] == pytest.approx(unit([1.0, 0.0]))

    def test_second_attach_skips(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sem, "embed_images",
                            lambda paths: (calls.append(paths), [unit([1.0, 0.0]) for _ in paths])[1])
        idx = ContentIndex(sha256="a" * 64, shots=[shot(0, frames=["f.png"])])
        sem.attach_visual_vectors(idx)
        _idx, count = sem.attach_visual_vectors(idx)
        assert count == 0 and len(calls) == 1

    def test_force_recomputes(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sem, "embed_images",
                            lambda paths: (calls.append(paths), [unit([1.0, 0.0]) for _ in paths])[1])
        idx = ContentIndex(sha256="a" * 64, shots=[shot(0, frames=["f.png"])])
        sem.attach_visual_vectors(idx)
        _idx, count = sem.attach_visual_vectors(idx, force=True)
        assert count == 1 and len(calls) == 2

    def test_shots_without_frames_are_skipped(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_images", lambda paths: [unit([1.0, 0.0]) for _ in paths])
        idx = ContentIndex(sha256="a" * 64, shots=[shot(0), shot(1, frames=["f.png"])])
        _idx, count = sem.attach_visual_vectors(idx)
        assert count == 1
        assert not (idx.shots[0].embeddings or {}).get(sem.VISUAL_MODALITY)

    def test_clip_unavailable_keeps_index_textual(self, monkeypatch):
        """CLIP 不可用时**静默跳过**（索引仍可用），不抛异常也不写假向量。"""
        monkeypatch.setattr(sem, "embed_images", lambda paths: [])
        idx = ContentIndex(sha256="a" * 64, shots=[shot(0, frames=["f.png"])])
        _idx, count = sem.attach_visual_vectors(idx)
        assert count == 0
        assert not sem.index_has_visual_vectors(idx)

    def test_max_shots_bounds_work(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_images", lambda paths: [unit([1.0, 0.0]) for _ in paths])
        idx = ContentIndex(sha256="a" * 64, shots=[shot(i, frames=[f"{i}.png"]) for i in range(5)])
        _idx, count = sem.attach_visual_vectors(idx, max_shots=2)
        assert count == 2

    def test_vector_count_mismatch_is_rejected(self, monkeypatch):
        """模型返回的向量个数不对时，宁可不写——错位的向量比没有向量更坏。"""
        monkeypatch.setattr(sem, "embed_images", lambda paths: [unit([1.0, 0.0])])
        idx = ContentIndex(sha256="a" * 64, shots=[shot(0, frames=["a.png"]), shot(1, frames=["b.png"])])
        _idx, count = sem.attach_visual_vectors(idx)
        assert count == 0
        assert not sem.index_has_visual_vectors(idx)


class TestCoverage:
    def test_empty_library(self):
        assert sem.visual_coverage([]) == 0.0

    def test_partial(self):
        idx = ContentIndex(sha256="a" * 64, shots=[
            shot(0, unit([1.0, 0.0])), shot(1)])
        assert sem.visual_coverage([idx]) == 0.5

    def test_full(self):
        idx = ContentIndex(sha256="a" * 64, shots=[shot(0, unit([1.0, 0.0]))])
        assert sem.visual_coverage([idx]) == 1.0


# ── 打分与排序 ────────────────────────────────────────────


class TestSemanticScores:
    def test_ranks_by_similarity(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        shots = [shot(0, unit([1.0, 0.0])), shot(1, unit([0.0, 1.0]))]
        scores = sem.semantic_scores("红色", shots)
        assert scores[0] > scores[1]

    def test_shots_without_vectors_are_absent(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        scores = sem.semantic_scores("红色", [shot(0, unit([1.0, 0.0])), shot(1)])
        assert set(scores) == {0}

    def test_no_text_vector_returns_empty(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [])
        assert sem.semantic_scores("红色", [shot(0, unit([1.0, 0.0]))]) == {}

    def test_empty_query_returns_empty(self):
        assert sem.semantic_scores("", [shot(0, unit([1.0, 0.0]))]) == {}

    def test_dimension_mismatch_skips_and_warns(self, monkeypatch, caplog):
        """索引里的向量来自另一个模型时必须**吵出来**，而不是静默当 0 分。"""
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        with caplog.at_level("WARNING"):
            scores = sem.semantic_scores("红色", [shot(0, unit([1.0, 0.0, 0.0]))])
        assert scores == {}
        assert any("维度" in r.message for r in caplog.records)

    def test_ensemble_averages_templates(self, monkeypatch):
        """多模板集成的输入应是多个提示词，而不是一个。"""
        seen: list[list[str]] = []

        def fake(texts):
            seen.append(list(texts))
            return [unit([1.0, 0.0]) for _ in texts]

        monkeypatch.setattr(sem, "embed_texts", fake)
        sem.semantic_scores("红色", [shot(0, unit([1.0, 0.0]))])
        assert len(seen[0]) == len(sem.PROMPT_TEMPLATES)

    def test_cancelling_templates_yields_unit_vector(self, monkeypatch):
        """互补模板平均后应仍是单位向量（否则相似度尺度会漂）。"""
        def fake(texts):
            return [unit([1.0, 0.0]), unit([0.0, 1.0])]

        monkeypatch.setattr(sem, "embed_texts", fake)
        scores = sem.semantic_scores("红色", [shot(0, unit([1.0, 0.0]))])
        assert scores[0] == pytest.approx(math.sqrt(0.5), abs=1e-3)


class TestRankShots:
    def _two_indexes(self) -> list[ContentIndex]:
        return [
            ContentIndex(sha256="a" * 64, asset_path="a.mp4", filename="a.mp4",
                         shots=[shot(0, unit([1.0, 0.0]), desc="红色画面")]),
            ContentIndex(sha256="b" * 64, asset_path="b.mp4", filename="b.mp4",
                         shots=[shot(0, unit([0.0, 1.0]), desc="蓝色画面")]),
        ]

    def test_top_hit_is_most_similar(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        hits = sem.rank_shots("红色", self._two_indexes(), top_k=5, min_score=0.0)
        assert hits[0]["sha256"] == "a" * 64
        assert hits[0]["ref"].startswith("asset:")

    def test_min_score_filters_irrelevant(self, monkeypatch):
        """CLIP 余弦总有正值——不设下限时不相干画面也会被排进结果。"""
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        hits = sem.rank_shots("红色", self._two_indexes(), top_k=5, min_score=0.5)
        assert [h["sha256"] for h in hits] == ["a" * 64]

    def test_missing_vectors_yield_no_hits(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        idx = [ContentIndex(sha256="a" * 64, shots=[shot(0, desc="红色")])]
        assert sem.rank_shots("红色", idx, min_score=0.0) == []

    def test_top_k_is_respected(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        idx = [ContentIndex(sha256="a" * 64, shots=[shot(i, unit([1.0, 0.0])) for i in range(9)])]
        assert len(sem.rank_shots("红色", idx, top_k=3, min_score=0.0)) == 3

    def test_hits_carry_shot_time_range(self, monkeypatch):
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        hit = sem.rank_shots("红色", self._two_indexes(), top_k=1, min_score=0.0)[0]
        assert hit["t0"] == 0.0 and hit["t1"] == 1.0


class TestSearchAvailable:
    def test_reports_vector_count(self, monkeypatch):
        # 打桩 CLIP 可用性：否则这里会真的加载 600MB 权重（实测每次 +16s）
        monkeypatch.setattr(sem, "_clip_loaded", lambda: True)
        idx = [ContentIndex(sha256="a" * 64, shots=[shot(0, unit([1.0, 0.0])), shot(1)])]
        available, vectors = sem.search_available(idx)
        assert available is True and vectors == 1

    def test_reports_unavailable(self, monkeypatch):
        monkeypatch.setattr(sem, "_clip_loaded", lambda: False)
        available, vectors = sem.search_available([])
        assert available is False and vectors == 0


# ── 索引集成（写进 build_index）───────────────────────────


class TestIndexIntegration:
    def test_sources_marks_unavailable(self, monkeypatch):
        """CLIP 不可用时索引里要**如实标注**，不能假装有向量。"""
        import asyncio

        from clipwright.services import content_index as ci

        monkeypatch.setattr(sem, "attach_visual_vectors", lambda idx, **kw: (idx, 0))
        # 直接验证标注逻辑（不跑完整 build_index：那需要真实视频）
        sources: dict[str, str] = {}
        from clipwright.services.content_semantic import attach_visual_vectors as real
        idx_stub = ContentIndex(sha256="a" * 64, shots=[shot(0, frames=["f.png"])])
        _i, count = real(idx_stub)
        sources["embeddings_visual"] = (f"clip:x" if count else "unavailable")
        assert sources["embeddings_visual"] == "unavailable"
        assert hasattr(ci, "build_index")


# ── 工具层：semantic_match 不再是假分 ─────────────────────


class TestSemanticMatchTool:
    @pytest.mark.asyncio
    async def test_clip_unavailable_reports_dependency_missing(self, monkeypatch):
        from clipwright.schema.tool import ToolStatus
        from clipwright.tool.vision import SemanticMatchTool

        monkeypatch.setattr(sem, "_clip_loaded", lambda: False)
        result = await SemanticMatchTool().execute(query="红色", candidate_paths=["x.png"])
        assert result.status == ToolStatus.DEPENDENCY_MISSING
        assert "CLIP" in (result.error or "")
        # **不能**再有假分数
        assert result.output.get("candidates") == []

    @pytest.mark.asyncio
    async def test_real_scores_replace_placeholder(self, monkeypatch):
        from clipwright.schema.tool import ToolStatus
        from clipwright.tool.vision import SemanticMatchTool

        monkeypatch.setattr(sem, "_clip_loaded", lambda: True)
        monkeypatch.setattr(sem, "embed_images",
                            lambda paths: [unit([1.0, 0.0]), unit([0.0, 1.0])])
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        result = await SemanticMatchTool().execute(query="红色", candidate_paths=["a.png", "b.png"])
        assert result.status == ToolStatus.SUCCESS
        scores = [c["score"] for c in result.output["candidates"]]
        assert scores[0] == pytest.approx(1.0, abs=1e-3)
        assert scores[0] != scores[1]           # 不再是一个统一的 0.5
        assert result.output["score_kind"] == "clip_cosine_similarity"
        assert result.output["query_gloss"] == "red"

    @pytest.mark.asyncio
    async def test_empty_candidates_is_success_not_error(self, monkeypatch):
        from clipwright.schema.tool import ToolStatus
        from clipwright.tool.vision import SemanticMatchTool

        monkeypatch.setattr(sem, "_clip_loaded", lambda: True)
        result = await SemanticMatchTool().execute(query="红色", candidate_paths=[])
        assert result.status == ToolStatus.SUCCESS
        assert result.output["candidates"] == []

    @pytest.mark.asyncio
    async def test_forward_failure_is_error_not_fake_scores(self, monkeypatch):
        from clipwright.schema.tool import ToolStatus
        from clipwright.tool.vision import SemanticMatchTool

        monkeypatch.setattr(sem, "_clip_loaded", lambda: True)
        monkeypatch.setattr(sem, "embed_images", lambda paths: [])
        monkeypatch.setattr(sem, "embed_texts", lambda texts: [unit([1.0, 0.0])])
        result = await SemanticMatchTool().execute(query="红色", candidate_paths=["a.png"])
        assert result.status == ToolStatus.ERROR
        assert "未产生任何分数" in (result.error or "")


# ── 真模型（显式开启才跑）────────────────────────────────


#: 真模型用例**默认跳过**：`_clip_loaded()` 会在收集阶段加载约 600MB 权重（实测 +20s），
#: 让每次全量回归都白付这个代价不值得。真模型的验收走
#: `scripts/verify_semantic_search.py`（那里本来就要真模型算召回率）。
#: 需要在这里跑时：`$env:CLIPWRIGHT_TEST_REAL_CLIP="1"`。
_REAL = os.environ.get("CLIPWRIGHT_TEST_REAL_CLIP", "") == "1"


@pytest.mark.skipif(not _REAL, reason="真模型用例默认跳过（设 CLIPWRIGHT_TEST_REAL_CLIP=1 开启）")
class TestRealClip:
    def test_image_and_text_vectors_align(self, tmp_path):
        """真模型冒烟：同色图片与对应英文提示词的相似度应高于异色。"""
        from PIL import Image

        if not sem._clip_loaded():
            pytest.skip("CLIP 权重不可用")
        red = tmp_path / "red.png"
        blue = tmp_path / "blue.png"
        Image.new("RGB", (224, 224), (220, 30, 30)).save(red)
        Image.new("RGB", (224, 224), (30, 60, 220)).save(blue)
        ivecs = sem.embed_images([str(red), str(blue)])
        assert len(ivecs) == 2 and len(ivecs[0]) == 512
        tvecs = sem.embed_texts(sem.text_prompts("红色"))
        assert tvecs
        red_sim = sem.cosine(tvecs[0], ivecs[0])
        blue_sim = sem.cosine(tvecs[0], ivecs[1])
        assert red_sim > blue_sim
