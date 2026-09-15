"""R6：意图落位（引用解析 / 会话指代 / 多候选反问 / 预览 API）测试。

关键断言集中在两件事上：
1. **不猜**：解析不出匹配就给**带选项的问句**，而不是随便挑一个片段；
2. **不越界**：`preview_intent` 只读时间线（前后 timeline 必须完全一致）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services import intent_resolver as ir
from clipwright.services.edit_intent import (
    EditIntent,
    IntentConstraints,
    IntentOp,
    IntentTarget,
)


def _timeline(n: int = 4, asset: str = "a.mp4") -> Timeline:
    clips = [
        Clip(id=f"c{i}", kind=ClipKind.VIDEO, asset_id=asset if i % 2 == 0 else f"b{i}.mp4",
             track_id="v", start_sec=float(i * 3), duration_sec=3.0,
             text=f"镜头{i}")
        for i in range(n)
    ]
    return Timeline(id="t", width=1920, height=1080, fps=30, duration_sec=float(n * 3),
                    tracks=[Track(id="v", name="主", kind=ClipKind.VIDEO, index=0, clips=clips)])


@pytest.fixture(autouse=True)
def _clean_state():
    ir.reset_session_state()
    yield
    ir.reset_session_state()


# ── 1. 会话指代状态 ───────────────────────────────────────


class TestSessionState:
    def test_push_and_recent(self) -> None:
        st = ir.SessionRefState()
        st.push(["c1", "c2"])
        assert st.recent() == ["c1", "c2"]

    def test_push_ignores_empty(self) -> None:
        st = ir.SessionRefState()
        st.push([])
        st.push([""])
        assert st.recent() == []

    def test_latest_first_and_union(self) -> None:
        st = ir.SessionRefState()
        st.push(["c1", "c2"])
        st.push(["c3"])
        assert st.recent() == ["c3"]
        assert st.union_of_recent() == ["c3", "c1", "c2"]

    def test_capped_groups(self) -> None:
        st = ir.SessionRefState()
        for i in range(10):
            st.push([f"c{i}"])
        assert len(st.shortlists) == ir._MAX_SHORTLISTS

    def test_session_state_singleton_per_id(self) -> None:
        a = ir.session_state("s1")
        b = ir.session_state("s1")
        assert a is b
        assert ir.session_state("s2") is not a

    def test_reset(self) -> None:
        ir.session_state("s1").push(["c1"])
        ir.reset_session_state("s1")
        assert ir.session_state("s1").recent() == []
        ir.session_state("s2").push(["c9"])
        ir.reset_session_state()
        assert ir.session_state("s2").recent() == []


class TestDeictic:
    def _state(self) -> ir.SessionRefState:
        st = ir.SessionRefState()
        st.push(["c3", "c4", "c5"])
        return st

    @pytest.mark.parametrize("phrase", ["刚才那段", "刚刚那个", "上一个片段", "那段"])
    def test_recent_phrases(self, phrase: str) -> None:
        assert ir.resolve_deictic(phrase, self._state()) == ["c3", "c4", "c5"]

    @pytest.mark.parametrize("phrase", ["这些", "那些", "它们"])
    def test_plural_phrases(self, phrase: str) -> None:
        assert ir.resolve_deictic(phrase, self._state()) == ["c3", "c4", "c5"]

    def test_ordinal(self) -> None:
        assert ir.resolve_deictic("第2个", self._state()) == ["c4"]

    def test_ordinal_out_of_range(self) -> None:
        assert ir.resolve_deictic("第9个", self._state()) is None

    def test_no_state_returns_none(self) -> None:
        assert ir.resolve_deictic("刚才那段", None) is None
        assert ir.resolve_deictic("刚才那段", ir.SessionRefState()) is None

    def test_non_deictic_returns_none(self) -> None:
        assert ir.resolve_deictic("去掉黑场", self._state()) is None

    def test_empty(self) -> None:
        assert ir.resolve_deictic("", self._state()) is None


# ── 2. 引用解析 ───────────────────────────────────────────


def _resolve(target: IntentTarget, timeline: Timeline, **kw: Any):
    return asyncio.run(ir.resolve_target(target, timeline=timeline, **kw))


class TestResolveTarget:
    def test_all(self) -> None:
        ids, why, degraded = _resolve(IntentTarget(all=True), _timeline(3))
        assert ids == ["c0", "c1", "c2"] and not degraded

    def test_selected(self) -> None:
        ids, _why, _d = _resolve(IntentTarget(selected=True), _timeline(),
                                 selected_clip_ids=["c1"])
        assert ids == ["c1"]

    def test_selected_without_selection_uses_region(self) -> None:
        ids, why, _d = _resolve(IntentTarget(selected=True), _timeline(),
                                region=(0.0, 3.5))
        assert ids == ["c0", "c1"]
        assert any("区间" in w for w in why)

    def test_selected_missing_everything(self) -> None:
        ids, why, _d = _resolve(IntentTarget(selected=True), _timeline())
        assert ids == [] and "没有选中" in why[0]

    def test_time_range(self) -> None:
        ids, _why, _d = _resolve(IntentTarget(time_range=(3.0, 9.0)), _timeline())
        assert ids == ["c1", "c2"]

    def test_time_range_no_overlap(self) -> None:
        ids, _why, _d = _resolve(IntentTarget(time_range=(100.0, 200.0)), _timeline())
        assert ids == []

    def test_ref_valid_and_missing(self) -> None:
        ids, why, _d = _resolve(IntentTarget(ref=["c1", "ghost"]), _timeline())
        assert ids == ["c1"] and any("不存在" in w for w in why)

    def test_index(self) -> None:
        ids, _why, _d = _resolve(IntentTarget(index=[0, 2]), _timeline())
        assert ids == ["c0", "c2"]

    def test_index_out_of_range(self) -> None:
        ids, why, _d = _resolve(IntentTarget(index=[9]), _timeline(2))
        assert ids == [] and any("越界" in w for w in why)

    def test_empty_target(self) -> None:
        ids, why, _d = _resolve(IntentTarget(), _timeline())
        assert ids == [] and "未指定" in why[0]

    def test_content_without_index_is_degraded(self, tmp_path, monkeypatch) -> None:
        """没建索引时必须**标明降级**，不能假装有内容索引。"""
        from clipwright.services import content_index as ci

        monkeypatch.setattr(ci.settings, "library_dir", tmp_path / "lib", raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        (tmp_path / "lib").mkdir()
        ids, why, degraded = _resolve(IntentTarget(filter={"is_black": True}), _timeline())
        assert ids == [] and degraded is True
        assert any("未建索引" in w for w in why)

    def test_content_with_index_maps_to_clip(self, tmp_path, monkeypatch) -> None:
        """镜头 → clip 的关键映射：命中素材的镜头，就要影响到使用该素材的片段。"""
        from clipwright.schema.content import ContentIndex, ShotRecord, ShotStats
        from clipwright.services import content_index as ci

        lib = tmp_path / "lib"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        # 时间线里的 clip 用的是 a.mp4；索引的 asset_path 指向同一个文件
        asset = tmp_path / "a.mp4"
        asset.write_bytes(b"x")
        ci.save_index(ContentIndex(
            sha256="ab" * 32, filename="a.mp4", asset_path=str(asset), duration_sec=6.0,
            shots=[ShotRecord(index=0, t0=0.0, t1=3.0,
                              stats=ShotStats(is_black=True, frame_count=2)),
                   ShotRecord(index=1, t0=3.0, t1=6.0, stats=ShotStats(frame_count=2))]))
        tl = Timeline(id="t", width=1920, height=1080, fps=30, duration_sec=6,
                      tracks=[Track(id="v", name="主", kind=ClipKind.VIDEO, index=0, clips=[
                          Clip(id="c0", kind=ClipKind.VIDEO, asset_id=str(asset), track_id="v",
                               start_sec=0, duration_sec=3),
                          Clip(id="c1", kind=ClipKind.VIDEO, asset_id="other.mp4", track_id="v",
                               start_sec=3, duration_sec=3)])])
        ids, why, degraded = _resolve(IntentTarget(filter={"is_black": True}), tl)
        assert ids == ["c0"], why
        assert any("命中" in w for w in why)

    def test_content_hit_not_in_timeline(self, tmp_path, monkeypatch) -> None:
        from clipwright.schema.content import ContentIndex, ShotRecord, ShotStats
        from clipwright.services import content_index as ci

        lib = tmp_path / "lib"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        ci.save_index(ContentIndex(
            sha256="cd" * 32, filename="zz.mp4", asset_path=str(tmp_path / "zz.mp4"),
            shots=[ShotRecord(index=0, t0=0.0, t1=1.0,
                              stats=ShotStats(is_black=True, frame_count=2))]))
        ids, why, degraded = _resolve(IntentTarget(filter={"is_black": True}), _timeline())
        assert ids == [] and degraded is True
        assert any("不在时间线" in w for w in why)


class TestClipIndexMap:
    def test_path_match_then_name_fallback(self, tmp_path) -> None:
        from clipwright.schema.content import ContentIndex, ShotRecord

        idx = ContentIndex(sha256="ee" * 32, asset_path=str(tmp_path / "sub" / "a.mp4"),
                           shots=[ShotRecord(index=0, t0=0.0, t1=1.0)])
        mapping, exact = ir._clip_index_map(_timeline(2), [idx])
        assert mapping["c0"] is idx          # 同名兜底命中
        assert exact["c0"] is False          # 但不是路径一致 → 降级标记按片段给出

    def test_exact_path_match_flag(self, tmp_path) -> None:
        from clipwright.schema.content import ContentIndex

        asset = tmp_path / "a.mp4"
        asset.write_bytes(b"x")
        idx = ContentIndex(sha256="12" * 32, asset_path=str(asset))
        tl = Timeline(id="t", tracks=[Track(id="v", kind=ClipKind.VIDEO, index=0, clips=[
            Clip(id="x", kind=ClipKind.VIDEO, asset_id=str(asset), track_id="v",
                 start_sec=0, duration_sec=1)])])
        mapping, exact = ir._clip_index_map(tl, [idx])
        assert mapping["x"] is idx and exact["x"] is True

    def test_no_match(self, tmp_path) -> None:
        from clipwright.schema.content import ContentIndex

        mapping, exact = ir._clip_index_map(_timeline(1), [ContentIndex(sha256="ff" * 32)])
        assert mapping["c0"] is None and exact["c0"] is False

    def test_empty_asset_id(self) -> None:
        tl = Timeline(id="t", tracks=[Track(id="v", kind=ClipKind.VIDEO, index=0, clips=[
            Clip(id="x", kind=ClipKind.VIDEO, asset_id="", track_id="v",
                 start_sec=0, duration_sec=1)])])
        mapping, exact = ir._clip_index_map(tl, [])
        assert mapping == {"x": None} and exact == {"x": False}


# ── 3. 整份意图落位 + 反问 ────────────────────────────────


def _resolve_intent(intent: EditIntent, timeline: Timeline, **kw: Any) -> ir.ResolutionReport:
    return asyncio.run(ir.resolve_intent(intent, timeline=timeline, **kw))


class TestResolveIntent:
    def test_ok_path(self) -> None:
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(all=True))])
        report = _resolve_intent(intent, _timeline(3), session_id="s1")
        assert report.ok and report.affected_clip_ids() == ["c0", "c1", "c2"]
        assert report.preview_lines()

    def test_no_match_asks_question_with_options(self) -> None:
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(time_range=(99.0, 100.0)))])
        report = _resolve_intent(intent, _timeline(2), session_id="s1")
        assert not report.ok
        q = report.questions[0]
        assert q.code == "no_match" and q.options        # 必须给具体选项
        assert all("value" in o and "label" in o for o in q.options)

    def test_too_many_asks_question(self) -> None:
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(all=True))])
        report = _resolve_intent(intent, _timeline(20), session_id="s1", clarify_above=5)
        assert report.questions and report.questions[0].code == "too_many"
        assert len(report.questions[0].options) == 3

    def test_session_deictic_fills_selection(self) -> None:
        """没选中、但上一轮提到过片段 → 按会话上下文理解（并注明）。"""
        ir.session_state("s1").push(["c3"])
        intent = EditIntent(goal="把它们删掉",
                            ops=[IntentOp(op="remove", target=IntentTarget(selected=True))])
        report = _resolve_intent(intent, _timeline(4), session_id="s1")
        assert report.affected_clip_ids() == ["c3"]
        assert any("会话上下文" in p for p in report.problems)

    def test_state_updated_after_resolution(self) -> None:
        intent = EditIntent(ops=[IntentOp(op="reorder", target=IntentTarget(index=[1]))])
        _resolve_intent(intent, _timeline(4), session_id="s9")
        assert ir.session_state("s9").recent() == ["c1"]

    def test_multiple_ops(self) -> None:
        intent = EditIntent(ops=[
            IntentOp(op="remove", target=IntentTarget(index=[0])),
            IntentOp(op="reorder", target=IntentTarget(all=True), by="duration"),
        ])
        report = _resolve_intent(intent, _timeline(3), session_id="s1")
        assert len(report.resolved) == 2 and report.ok

    def test_preview_and_serialization(self) -> None:
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(all=True))])
        report = _resolve_intent(intent, _timeline(6), session_id="s1")
        payload = report.as_dict()
        assert payload["resolved"][0]["clip_ids"] == [f"c{i}" for i in range(6)]
        assert "等 6 个片段" in payload["preview"][0]


# ── 4. 预览入口 ───────────────────────────────────────────


class TestPreviewIntent:
    def test_preview_does_not_mutate_timeline(self) -> None:
        tl = _timeline(3)
        before = tl.model_dump()
        res = asyncio.run(ir.preview_intent("去掉黑场，剪到 60 秒", timeline=tl, session_id="p1"))
        assert tl.model_dump() == before        # 预览必须只读
        assert res["intent"] and "总时长" in res["intent"]

    def test_blocked_on_hallucinated_op(self) -> None:
        class _BadLlm:
            async def structured_output(self, **kwargs):
                return {"ops": [{"op": "explode"}]}

        tl = _timeline(2)
        res = asyncio.run(ir.preview_intent("炸一下", timeline=tl, session_id="p1",
                                            llm=_BadLlm()))
        assert res["blocked"] is True
        assert res["problems"] and res["resolution"]["resolved"] == []

    def test_questions_surface_in_resolution(self) -> None:
        tl = _timeline(2)
        res = asyncio.run(ir.preview_intent("把黑场删掉", timeline=tl, session_id="p1"))
        assert res["resolution"]["questions"]


# ── 5. API ────────────────────────────────────────────────


@pytest.fixture()
def api(monkeypatch):
    from clipwright.api import requirements as req_api

    monkeypatch.setattr(req_api, "_require_session_owner", lambda request, sid: None)
    app = FastAPI()
    app.include_router(req_api.router)
    return TestClient(app)


class TestIntentApi:
    def _body(self, **over: Any) -> dict[str, Any]:
        body = {"message": "剪到 60 秒", "timeline": _timeline(3).model_dump(),
                "selected_clip_ids": []}
        body.update(over)
        return body

    def test_preview_ok(self, api) -> None:
        r = api.post("/api/requirements/sess1/intent", json=self._body())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"] == "sess1"
        assert "总时长" in body["intent"] and body["blocked"] is False

    def test_preview_blocks_hallucination_via_api(self, api, monkeypatch) -> None:
        """API 层也要拦住幻觉 op（不能只在服务层拦）。"""
        from clipwright.services import edit_intent as ei

        async def fake_parse(message, **kw):
            # 模拟真实 parse_intent：解析后**必须过校验**，幻觉 op 在这里被判错
            return ei.validate_intent(ei.EditIntent(ops=[ei.IntentOp(op="explode_video")]))

        monkeypatch.setattr(ei, "parse_intent", fake_parse)
        r = api.post("/api/requirements/sess1/intent",
                     json=self._body(message="炸一下", selected_clip_ids=[]))
        assert r.status_code == 200
        assert r.json()["blocked"] is True

    def test_invalid_timeline_400(self, api) -> None:
        r = api.post("/api/requirements/sess1/intent",
                     json={"message": "x", "timeline": {"tracks": "not-a-list"}})
        assert r.status_code == 400

    def test_empty_timeline_allowed(self, api) -> None:
        r = api.post("/api/requirements/sess1/intent", json={"message": "剪到 30 秒"})
        assert r.status_code == 200 and r.json()["blocked"] is False

    def test_region_used_when_no_selection(self, api) -> None:
        r = api.post("/api/requirements/sess1/intent",
                     json=self._body(message="把它们删掉", selected_clip_ids=[],
                                     region_start_sec=0.0, region_end_sec=3.5))
        body = r.json()
        assert body["resolution"]["affected_clip_ids"] == ["c0", "c1"]


class TestReferenceFormsFromText:
    """从自然语言短语到 target 的形态识别（时间区间 / 片段 id / 序号 / 指代）。"""

    def test_time_range_forms(self) -> None:
        from clipwright.services import edit_intent as ei

        for text in ("0 到 3.5 秒", "第 5 秒到第 8 秒", "5-8s", "零到十秒"):
            target = ei._target_from_phrase(text)
            assert target.time_range is not None, text
            lo, hi = target.time_range
            assert hi > lo

    def test_reversed_range_normalized(self) -> None:
        from clipwright.services import edit_intent as ei

        assert ei._target_from_phrase("8 到 3 秒").time_range == (3.0, 8.0)

    def test_clip_id_forms(self) -> None:
        from clipwright.services import edit_intent as ei

        for text in ("c3", "clip_07", "mg_ab12", "anim_1"):
            assert ei._target_from_phrase(text).ref == [text], text

    def test_english_word_is_not_id(self) -> None:
        """普通英文词不能当片段 id（否则 "iphone" 会被当引用去查时间线）。"""
        from clipwright.services import edit_intent as ei

        target = ei._target_from_phrase("iphone")
        assert target.ref == [] and "iphone" in target.query

    def test_ordinal_maps_to_timeline_position(self) -> None:
        from clipwright.services import edit_intent as ei

        assert ei._target_from_phrase("第 1 个").index == [0]
        assert ei._target_from_phrase("第 4 个").index == [3]

    def test_deictic_maps_to_selection(self) -> None:
        from clipwright.services import edit_intent as ei

        for text in ("刚才那段", "它们", "这些片段"):
            target = ei._target_from_phrase(text)
            assert target.selected is True and not target.query, text

    def test_parse_time_range_direct(self) -> None:
        from clipwright.services import edit_intent as ei

        assert ei.parse_time_range("剪掉 10 到 20 秒") == (10.0, 20.0)
        assert ei.parse_time_range("没有区间") is None


class TestClipTextUnion:
    def test_clip_label_participates_in_matching(self, tmp_path, monkeypatch) -> None:
        """时间线上的片段标题是用户自己的话（"产品演示"），索引里查不到也要能命中。"""
        from clipwright.schema.content import ContentIndex, ShotRecord, ShotStats
        from clipwright.services import content_index as ci

        lib = tmp_path / "lib"
        lib.mkdir()
        monkeypatch.setattr(ci.settings, "library_dir", lib, raising=False)
        monkeypatch.setattr(ci.settings, "project_dir", tmp_path / "p", raising=False)
        asset = tmp_path / "a.mp4"
        asset.write_bytes(b"x")
        ci.save_index(ContentIndex(
            sha256="ab" * 32, asset_path=str(asset), duration_sec=6.0,
            shots=[ShotRecord(index=0, t0=0.0, t1=3.0, stats=ShotStats(frame_count=2))]))
        tl = Timeline(id="t", tracks=[Track(id="v", kind=ClipKind.VIDEO, index=0, clips=[
            Clip(id="c0", kind=ClipKind.VIDEO, asset_id=str(asset), track_id="v",
                 start_sec=0, duration_sec=3, text="产品演示"),
            Clip(id="c1", kind=ClipKind.VIDEO, asset_id="zz.mp4", track_id="v",
                 start_sec=3, duration_sec=3, text="无关片段")])])
        ids, why, degraded = asyncio.run(ir.resolve_target(
            IntentTarget(query="产品演示"), timeline=tl))
        assert ids == ["c0"]
        assert any("片段文本命中" in w for w in why)
