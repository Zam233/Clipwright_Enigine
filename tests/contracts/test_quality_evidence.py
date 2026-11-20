"""B19：质检证据绑定与结构化 redo_request（与 needs 互斥）。"""

from __future__ import annotations

from clipwright.agents.quality_agent import (
    _bind_issue_evidence,
    _build_redo_request,
    _timeline_sha,
)
from clipwright.schema.agent import QualityIssue
from clipwright.schema.timeline import Clip, Timeline, Track

SHA = "a" * 64


def _issue(**kw) -> QualityIssue:
    base = dict(severity="error", category="material_match", message="画面与文案不匹配",
                location="clip:c12")
    base.update(kw)
    return QualityIssue(**base)


def test_bind_evidence_from_location():
    issues = _bind_issue_evidence([_issue()], timeline_sha=SHA)
    assert issues[0].clip_id == "c12"
    assert issues[0].artifact_sha == SHA
    assert any("location=clip:c12" in e for e in issues[0].evidence)


def test_bind_keeps_explicit_values():
    issue = _issue(clip_id="c99", artifact_sha="b" * 64, evidence=["已有证据"])
    _bind_issue_evidence([issue], timeline_sha=SHA)
    assert issue.clip_id == "c99"
    assert issue.artifact_sha == "b" * 64
    assert issue.evidence == ["已有证据"]


def test_redo_request_none_when_nothing_to_redo():
    assert _build_redo_request([], redo_agent="material", timeline_sha=SHA) is None
    assert _build_redo_request([_issue()], redo_agent="", timeline_sha=SHA) is None


def test_redo_request_shape_and_no_needs():
    issue = _issue(clip_id="c12", artifact_sha=SHA)
    req = _build_redo_request([issue], redo_agent="material", timeline_sha=SHA)
    assert req is not None
    assert req["target_stage"] == "material"
    assert req["scope"] == "clip:c12"
    assert req["base_artifact_sha"] == SHA
    assert req["issue_ids"] == ["material_match:c12"]
    assert "needs" not in req and "issues" not in req


def test_timeline_sha_changes_with_content():
    def tl(dur: float) -> Timeline:
        clip = Clip(id="v1", kind="video", asset_id="a", track_id="tv",
                    start_sec=0, duration_sec=dur)
        return Timeline(id="tl", width=1920, height=1080, fps=30,
                        tracks=[Track(id="tv", name="v", kind="video", index=0, clips=[clip])])

    assert _timeline_sha(tl(5)) != _timeline_sha(tl(6))
    assert _timeline_sha(tl(5)) == _timeline_sha(tl(5))
