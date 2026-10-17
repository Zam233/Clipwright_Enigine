"""渲染后复核的问题路由（V4 残余）。

video-use 的自评闭环里，复核结论是**按问题类型分派给不同修复动作**的：
字幕被遮 → 回去改字幕层；画面跳变 → 回去改剪辑点；爆音 → 回去改音频。
本项目此前只把复核结论写进 `warnings`（人看），没有回路由——复核"看见了"却没人接。

本模块把复核结论变成可派工的结构：

- `classify_finding()`：按问题描述归类（edit / animation / audio / material / other）；
- `route_findings()`：把一次复核（视觉裁决 + 切点复核图）转成 `PostReviewFinding` 列表，
  每条带 `redo_agent`（可直接喂给既有自愈机制）与证据路径；
- `choose_redo_agent()`：整轮复核的**主**重做对象（按严重度加权，取最该修的那一类）；
- `visual_score_gate()`：视觉评分门槛判定（`min_score <= 0` 表示不启用门槛）；
- `to_quality_issues()`：转成 `QualityIssue`，让 `QualityAgent` 的既有分类/分数/自愈映射复用。

关键词表是刻意保守的：匹配不上就归 `other`（不假装知道该重做谁），
只有能明确指出"哪里错了"的描述才会被路由。
"""

from __future__ import annotations

from typing import Any, Iterable

# 问题类型 → 负责重做的 Agent
KIND_TO_AGENT: dict[str, str] = {
    "animation": "animation",   # 动画/overlay/MG 相关：元素遮挡、错位、缺动画
    "edit": "edit",             # 剪辑点相关：跳变、闪帧、顺序、节奏
    "audio": "audio",           # 声音相关：爆音、突变、静音、不同步
    "material": "material",     # 素材相关：黑帧、纯色、占位、素材与文案不符
    "other": "",                # 说不清 → 不派工（宁可不重做，也不要瞎重做）
}

# 严重度 → 参与「主重做对象」投票时的权重
_SEVERITY_WEIGHT = {"high": 3, "error": 3, "medium": 2, "warning": 2, "low": 1, "info": 1}

# 关键词表：命中即归类。顺序有意义——先判更具体的类别。
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("material", (
        "黑帧", "全黑", "纯色", "空镜", "空帧", "占位", "加载中", "素材缺失",
        "与文案不符", "不相符", "码率", "花屏", "绿屏", "模糊不清",
    )),
    ("audio", (
        "爆音", "爆裂", "破音", "杂音", "爆闪声", "音量", "静音", "无声", "音频",
        "不同步", "对不上口型", "呼吸声", "咔哒", "波峰",
    )),
    ("animation", (
        "动画", "mg", "overlay", "叠加层", "元素", "字幕被", "遮挡", "盖住", "压字",
        "入场", "出场", "转场动画", "关键字幕", "标题", "图形",
    )),
    ("edit", (
        "跳变", "跳切", "闪帧", "闪烁", "硬切", "切点", "剪辑点", "顺序", "节奏",
        "突兀", "时长", "拖沓", "重复镜头", "机位跳",
    )),
]


def classify_finding(detail: str) -> str:
    """按问题描述归类；匹配不上返回 `other`。"""
    text = (detail or "").strip().lower()
    if not text:
        return "other"
    for kind, keywords in _KEYWORDS:
        if any(keyword.lower() in text for keyword in keywords):
            return kind
    return "other"


def _severity_of(raw: Any) -> str:
    value = str(raw or "medium").strip().lower()
    return value if value in _SEVERITY_WEIGHT else "medium"


def route_findings(
    verdict: dict[str, Any] | None,
    *,
    evidence: Iterable[str] = (),
    cut_times: Iterable[float] = (),
    default_kind: str = "edit",
) -> list[dict[str, Any]]:
    """把一次复核裁决转成带派工信息的问题列表。

    参数：
    - `verdict`：视觉裁决（`MGVisualEvaluator` 的输出，含 `issues`/`suggestions`/`score`）；
    - `evidence`：证据文件路径（复核图），写进每条 finding 便于人工回看；
    - `cut_times`：本次复核覆盖的切点（写进 finding 供定位）；
    - `default_kind`：**没有可用描述**时（例如只有 suggestions）的兜底类别——
      复核是围绕剪辑点做的，因此默认 `edit`。
    """
    evidence_list = [str(p) for p in evidence]
    cut_list = [round(float(t), 3) for t in cut_times]
    findings: list[dict[str, Any]] = []
    if not verdict:
        return findings

    for issue in (verdict.get("issues") or []):
        if not isinstance(issue, dict):
            continue
        detail = str(issue.get("detail") or "").strip()
        if not detail:
            continue
        kind = classify_finding(detail)
        if kind == "other":
            kind = default_kind if default_kind in KIND_TO_AGENT else "other"
        findings.append({
            "kind": kind,
            "redo_agent": KIND_TO_AGENT.get(kind, ""),
            "severity": _severity_of(issue.get("severity")),
            "detail": detail,
            "evidence": evidence_list,
            "cut_times": cut_list,
            "source": str(verdict.get("source") or "visual_eval"),
        })

    # 只有建议没有问题清单时：把建议也转成低优先级的 finding（仍能派工）
    if not findings:
        for suggestion in (verdict.get("suggestions") or [])[:3]:
            text = str(suggestion).strip()
            if not text:
                continue
            kind = classify_finding(text)
            findings.append({
                "kind": kind if kind != "other" else default_kind,
                "redo_agent": KIND_TO_AGENT.get(kind if kind != "other" else default_kind, ""),
                "severity": "low",
                "detail": text,
                "evidence": evidence_list,
                "cut_times": cut_list,
                "source": str(verdict.get("source") or "visual_eval"),
                "from": "suggestion",
            })
    return findings


def choose_redo_agent(findings: list[dict[str, Any]]) -> str:
    """整轮复核的主重做对象：按严重度加权票数取最高；平票时按 素材>动画>声音>剪辑 排序。"""
    scores: dict[str, int] = {}
    for finding in findings or []:
        agent = str(finding.get("redo_agent") or "")
        if not agent:
            continue
        scores[agent] = scores.get(agent, 0) + _SEVERITY_WEIGHT.get(
            _severity_of(finding.get("severity")), 2)
    if not scores:
        return ""
    priority = ["material", "animation", "audio", "edit"]
    return sorted(scores.items(), key=lambda kv: (-kv[1], priority.index(kv[0]) if kv[0] in priority else 99))[0][0]


def visual_score_gate(score: Any, min_score: Any) -> bool:
    """视觉评分是否通过门槛。

    `min_score <= 0`（默认）表示**不启用**门槛——与 `quality_min_score` 的既有语义一致，
    避免默认行为变化。`score` 非法（None/非数值）时视为不通过（保守：没有分数就不放行门槛判定，
    但调用方通常会先判 `min_score` 是否启用）。
    """
    try:
        threshold = int(min_score or 0)
    except (TypeError, ValueError):
        threshold = 0
    if threshold <= 0:
        return True
    try:
        value = float(score)
    except (TypeError, ValueError):
        return False
    return value >= threshold


# 问题类型 → QualityAgent 的问题分类（复用其「分类 → redo_agent」映射）
_KIND_TO_QUALITY_CATEGORY = {
    "animation": "animation",
    "edit": "structure",
    "audio": "audio",
    "material": "material",
    "other": "semantic",
}


def to_quality_issues(findings: list[dict[str, Any]], *, max_issues: int = 5) -> list[dict[str, Any]]:
    """转成 `QualityIssue` 可用的字典列表（调用方 `QualityIssue(**item)`）。

    `severity` 映射：high → error（致命，触发自愈），medium/low → warning/info。
    分类沿用 `QualityAgent` 已有的映射表，因此 `redo_agent` 的推导不需要在质检里再写一遍。
    """
    issues: list[dict[str, Any]] = []
    for finding in (findings or [])[:max_issues]:
        severity = _severity_of(finding.get("severity"))
        kind = str(finding.get("kind") or "other")
        issues.append({
            "severity": "error" if severity == "high" else ("warning" if severity == "medium" else "info"),
            "category": _KIND_TO_QUALITY_CATEGORY.get(kind, "semantic"),
            "message": f"[渲染后复核] {finding.get('detail', '')}",
            "file_path": (finding.get("evidence") or [""])[0],
        })
    return issues


def summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """复核结论摘要（写进 warnings / 结果字段，便于前端与日志一眼看清）。"""
    by_kind: dict[str, int] = {}
    for finding in findings or []:
        kind = str(finding.get("kind") or "other")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    high = [f for f in (findings or []) if _severity_of(f.get("severity")) == "high"]
    return {
        "total": len(findings or []),
        "by_kind": by_kind,
        "high_count": len(high),
        "redo_agent": choose_redo_agent(findings or []),
        "needs_redo": bool(high),
    }
