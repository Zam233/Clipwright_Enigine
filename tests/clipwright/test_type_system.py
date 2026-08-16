"""P10: 类型系统 — B21 transform 实现 + B23 引用检查 + B24 热注册回滚 测试。"""

from __future__ import annotations

from clipwright.category.dynamic import DynamicCategoryPlugin
from clipwright.schema.timeline import Timeline, Track, Clip, ClipKind


def _timeline() -> Timeline:
    return Timeline(
        id="t", width=1920, height=1080, fps=30, duration_sec=10,
        tracks=[Track(id="v1", name="V1", kind=ClipKind.VIDEO, index=0, clips=[
            Clip(id="c1", kind=ClipKind.VIDEO, asset_id="a", track_id="v1",
                 start_sec=0, duration_sec=10, source_offset_sec=0,
                 speed=1, volume=1, opacity=1, keyframes=[]),
        ])],
    )


def test_transform_fps_resolution_caption() -> None:
    """B21: transform 覆盖 fps/分辨率 + 插入标题字幕。"""
    plugin = DynamicCategoryPlugin({
        "id": "t1", "name": "测试", "shot_params": {},
        "persona_mapping": {},
        "transform": {"fps": 60, "width": 1080, "height": 1920,
                      "add_title_caption": "竖屏测试"},
    })
    tl = plugin.post_process_timeline(_timeline())
    assert tl.fps == 60
    assert tl.width == 1080
    assert tl.height == 1920
    assert any(t.kind in (ClipKind.CAPTION, ClipKind.TEXT) for t in tl.tracks)


def test_transform_duration_cap() -> None:
    """B21: duration_cap_sec 截断超长片段。"""
    plugin = DynamicCategoryPlugin({
        "id": "t2", "name": "测试", "shot_params": {},
        "persona_mapping": {}, "transform": {"duration_cap_sec": 5},
    })
    tl = plugin.post_process_timeline(_timeline())
    assert tl.tracks[0].clips[0].duration_sec == 5


def test_validate_definition_bad_transition() -> None:
    """B23: 非法转场类型 → 400。"""
    from fastapi.testclient import TestClient
    from clipwright.main import app
    client = TestClient(app)
    resp = client.post("/api/type-maker/create", json={
        "id": "t_invalid", "name": "坏类型",
        "shot_params": {"min_shot_sec": 1, "max_shot_sec": 5,
                        "transition_type": ";rm -rf", "transition_duration_sec": 0.5},
        "persona_mapping": {},
    })
    assert resp.status_code == 400


def test_validate_definition_ok() -> None:
    """B23: 合法定义通过（创建成功或 409 已存在均可接受，但不 400）。"""
    from fastapi.testclient import TestClient
    from clipwright.main import app
    client = TestClient(app)
    resp = client.post("/api/type-maker/create", json={
        "id": "t_valid_ok", "name": "好类型",
        "shot_params": {"min_shot_sec": 1, "max_shot_sec": 5,
                        "transition_type": "fade", "transition_duration_sec": 0.5},
        "persona_mapping": {"rhythm": {"source": "rhythm.cut_density_tier", "transform": "direct"}},
    })
    assert resp.status_code in (200, 409)
