"""动画 overlay 分簇渲染（用户 2026-10-28 反馈：成片里没有动画）。

现象：`final_flash.mp4` 在 33.6s / 83.4s 等动画点**没有任何动画**，但渲染报 success。

根因（实测 hyperframes stderr 原文）：
    Disk capture may need ~168044.5 MB of temporary frame storage,
    but only 30885.8 MB is free
`render_overlays()` 把全部 52 个动画 overlay 编成一张**覆盖整片 702s** 的透明 MOV
（702 × 30fps = 20259 帧 PNG ≈ 164GB 临时盘），hyperframes 直接拒渲 → 图解动画
整批丢失；而旧的渲染代码"失败就什么都不记"，照样返回成功。

修复：按时间簇分块（簇内间隔 ≤1s 合并），每簇只渲自己的跨度（多数 5-7s），
再用 `_apply_mg_overlay_chained` 一次性叠回；失败的簇进 warnings/日志。
"""

from __future__ import annotations

from clipwright.services.render import _split_overlay_clusters


def _ov(start: float, dur: float) -> dict:
    return {"start_sec": start, "duration_sec": dur}


def test_overlapping_overlays_merge_into_one_cluster():
    clusters = _split_overlay_clusters([_ov(0, 5), _ov(4, 5), _ov(9, 2)])

    assert len(clusters) == 1, "首尾相接/重叠的动画应当同簇渲染"
    start, span, members = clusters[0]
    assert start == 0
    assert span == 11
    assert len(members) == 3


def test_gap_splits_clusters():
    clusters = _split_overlay_clusters([_ov(0, 5), _ov(30, 6), _ov(60, 4)])

    assert len(clusters) == 3
    assert [c[0] for c in clusters] == [0, 30, 60]


def test_one_second_gap_still_merges():
    clusters = _split_overlay_clusters([_ov(0, 5), _ov(6, 5)])

    assert len(clusters) == 1, "间隔 ≤1s 视为连续段落"


def test_span_is_measured_to_cluster_end():
    clusters = _split_overlay_clusters([_ov(10, 3), _ov(12, 8)])

    start, span, _ = clusters[0]
    assert start == 10
    assert span == 10, "簇跨度按最后一个成员的结束时间算"


def test_empty_and_zero_duration_are_dropped():
    assert _split_overlay_clusters([]) == []
    assert _split_overlay_clusters([_ov(5, 0)]) == []


def test_frame_budget_shrinks_vs_whole_film():
    """整片一张 MOV 的帧数 vs 分簇帧数（这是 hyperframes 拒渲的直接原因）。"""
    overlays = [_ov(i * 13.5, 6) for i in range(52)]  # 52 段铺满 702s
    clusters = _split_overlay_clusters(overlays)
    whole = int(max(o["start_sec"] + o["duration_sec"] for o in overlays) * 30)
    clustered = int(sum(span for _, span, _ in clusters) * 30)

    assert clustered < whole
    assert max(span for _, span, _ in clusters) <= 30, "单簇不超过 30s，避免再次触发磁盘抓帧上限"
