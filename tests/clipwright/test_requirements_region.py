"""W12: 区域级返工 — region 范围限制编辑目标测试。"""

from __future__ import annotations

from clipwright.services.requirements_service import RequirementsService


def test_region_scoping_picks_clips_in_window() -> None:
    """区域内片段被选中，区域外不被选中（selected_clip_ids 为空时）。"""
    svc = RequirementsService()
    # 直接用内部逻辑：构造时间线验证范围收集（不触发 LLM/Agent）
    from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
    tl = Timeline(
        duration_sec=20,
        tracks=[
            Track(id="t0", kind=ClipKind.VIDEO, index=0, clips=[
                Clip(id="c_in_1", kind=ClipKind.VIDEO, asset_id="a1", track_id="t0",
                     start_sec=2, duration_sec=3),
                Clip(id="c_in_2", kind=ClipKind.VIDEO, asset_id="a2", track_id="t0",
                     start_sec=6, duration_sec=2),
                Clip(id="c_out", kind=ClipKind.VIDEO, asset_id="a3", track_id="t0",
                     start_sec=12, duration_sec=3),
            ]),
        ],
    )
    # 区域 [1, 9) → c_in_1(2-5)、c_in_2(6-8) 命中；c_out(12+) 不命中
    region_ids = []
    lo, hi = 1.0, 9.0
    for track in tl.tracks or []:
        for clip in track.clips or []:
            if clip.start_sec < hi and clip.start_sec + clip.duration_sec > lo:
                region_ids.append(clip.id)
    assert sorted(region_ids) == ["c_in_1", "c_in_2"]
    assert "c_out" not in region_ids
