"""渲染后复核的问题路由（V4 残余）测试。

覆盖：
- 关键词归类（剪辑/动画/声音/素材/说不清）；
- 派工列表（含证据与切点）与主重做对象投票；
- 只有 suggestions 时也能派工（低优先级）；
- 视觉评分门槛（默认不启用 / 门槛内 / 门槛外 / 分数非法）；
- 转 QualityIssue（严重度与分类映射到既有 redo_agent 体系）；
- 摘要与空输入。
"""

from __future__ import annotations

import pytest

from clipwright.services.post_review import (
    KIND_TO_AGENT,
    choose_redo_agent,
    classify_finding,
    route_findings,
    summarize,
    to_quality_issues,
    visual_score_gate,
)


class TestClassify:
    @pytest.mark.parametrize(("detail", "expected"), [
        ("字幕被下三路 overlay 遮住了", "animation"),
        ("MG 动画入场元素压住了标题", "animation"),
        ("切点处画面跳变明显", "edit"),
        ("镜头顺序突兀，节奏拖沓", "edit"),
        ("边界有爆音，音量突增", "audio"),
        ("人声与口型不同步", "audio"),
        ("第 3 秒是全黑帧，素材没加载出来", "material"),
        ("出现整幅纯色占位块", "material"),
        ("这里感觉不太对", "other"),
        ("", "other"),
    ])
    def test_keyword_classification(self, detail: str, expected: str) -> None:
        assert classify_finding(detail) == expected

    def test_case_insensitive_mg(self) -> None:
        assert classify_finding("MG 层错位") == "animation"
        assert classify_finding("Overlay 覆盖到字幕") == "animation"


class TestRouteFindings:
    def _verdict(self) -> dict:
        return {
            "score": 55,
            "issues": [
                {"severity": "high", "detail": "第 2 秒字幕被 overlay 压住"},
                {"severity": "medium", "detail": "切点处有明显跳变"},
                {"severity": "low", "detail": "某处感觉一般"},
            ],
            "suggestions": ["缩短入场时间"],
            "summary": "两处明显问题",
            "source": "visual_eval",
        }

    def test_issues_become_findings_with_agent_and_evidence(self) -> None:
        findings = route_findings(self._verdict(), evidence=["/tmp/cut_0.png", "/tmp/overview.png"],
                                  cut_times=[1.5, 2.0])
        assert len(findings) == 3
        assert findings[0]["kind"] == "animation"
        assert findings[0]["redo_agent"] == "animation"
        assert findings[0]["severity"] == "high"
        assert findings[0]["evidence"] == ["/tmp/cut_0.png", "/tmp/overview.png"]
        assert findings[0]["cut_times"] == [1.5, 2.0]
        assert findings[1]["redo_agent"] == "edit"
        # 说不清的那条按 default_kind 兜底（复核围绕剪辑点，默认 edit）
        assert findings[2]["kind"] == "edit"

    def test_default_kind_none_keeps_other(self) -> None:
        findings = route_findings(
            {"issues": [{"severity": "high", "detail": "说不清"}]},
            default_kind="none",
        )
        assert findings[0]["kind"] == "other"
        assert findings[0]["redo_agent"] == ""

    def test_suggestions_only_still_routed_as_low(self) -> None:
        findings = route_findings({"issues": [], "suggestions": ["把字幕挪开，别被 overlay 盖住"]})
        assert len(findings) == 1
        assert findings[0]["severity"] == "low"
        assert findings[0]["kind"] == "animation"
        assert findings[0]["from"] == "suggestion"

    def test_empty_inputs(self) -> None:
        assert route_findings(None) == []
        assert route_findings({}) == []
        assert route_findings({"issues": [{"detail": "   "}]}) == []
        assert route_findings({"issues": ["字符串形式的 issue 被忽略"]}) == []


class TestRedoAgentVote:
    def test_highest_severity_wins(self) -> None:
        findings = route_findings({
            "issues": [
                {"severity": "low", "detail": "切点轻微跳变"},
                {"severity": "high", "detail": "字幕被 overlay 遮挡"},
            ],
        })
        assert choose_redo_agent(findings) == "animation"

    def test_tie_breaks_by_priority_order(self) -> None:
        findings = [
            {"redo_agent": "edit", "severity": "high"},
            {"redo_agent": "material", "severity": "high"},
        ]
        assert choose_redo_agent(findings) == "material"

    def test_no_agent(self) -> None:
        assert choose_redo_agent([]) == ""
        assert choose_redo_agent([{"redo_agent": "", "severity": "high"}]) == ""

    def test_every_kind_has_agent_mapping(self) -> None:
        assert set(KIND_TO_AGENT) == {"animation", "edit", "audio", "material", "other"}


class TestScoreGate:
    def test_disabled_by_default(self) -> None:
        assert visual_score_gate(10, 0) is True
        assert visual_score_gate(None, 0) is True
        assert visual_score_gate(10, None) is True
        assert visual_score_gate(10, "abc") is True

    def test_threshold(self) -> None:
        assert visual_score_gate(80, 70) is True
        assert visual_score_gate(70, 70) is True
        assert visual_score_gate(69.9, 70) is False

    def test_invalid_score_fails_only_when_threshold_enabled(self) -> None:
        assert visual_score_gate(None, 70) is False
        assert visual_score_gate("not-a-number", 70) is False


class TestQualityIssues:
    def test_severity_and_category_mapping(self) -> None:
        findings = [
            {"kind": "animation", "severity": "high", "detail": "字幕被遮", "evidence": ["/tmp/a.png"]},
            {"kind": "edit", "severity": "medium", "detail": "切点跳变"},
            {"kind": "audio", "severity": "low", "detail": "轻微爆音"},
            {"kind": "material", "severity": "high", "detail": "黑帧"},
            {"kind": "other", "severity": "medium", "detail": "说不清"},
        ]
        issues = to_quality_issues(findings)
        assert [i["severity"] for i in issues] == ["error", "warning", "info", "error", "warning"]
        assert [i["category"] for i in issues] == [
            "animation", "structure", "audio", "material", "semantic",
        ]
        assert issues[0]["message"].startswith("[渲染后复核]")
        assert issues[0]["file_path"] == "/tmp/a.png"

    def test_cap(self) -> None:
        findings = [{"kind": "edit", "severity": "high", "detail": f"问题 {i}"} for i in range(10)]
        assert len(to_quality_issues(findings, max_issues=3)) == 3


class TestSummarize:
    def test_counts_and_redo(self) -> None:
        findings = route_findings({
            "issues": [
                {"severity": "high", "detail": "黑帧"},
                {"severity": "high", "detail": "纯色占位"},
                {"severity": "low", "detail": "切点跳变"},
            ],
        })
        summary = summarize(findings)
        assert summary["total"] == 3
        assert summary["by_kind"] == {"material": 2, "edit": 1}
        assert summary["high_count"] == 2
        assert summary["redo_agent"] == "material"
        assert summary["needs_redo"] is True

    def test_no_high_means_no_redo(self) -> None:
        summary = summarize([{"kind": "edit", "severity": "low", "detail": "x"}])
        assert summary["needs_redo"] is False

    def test_empty(self) -> None:
        summary = summarize([])
        assert summary == {"total": 0, "by_kind": {}, "high_count": 0, "redo_agent": "", "needs_redo": False}
