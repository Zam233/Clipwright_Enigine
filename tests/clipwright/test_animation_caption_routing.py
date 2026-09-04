"""AnimationAgent 字幕轨路由 — 防止场景描述文本污染字幕轨。

背景（Task 4）：
`_handle_text_animation` 对「长文本（>50 字符）+ typewriter」路由到 `_handle_caption`
创建 CAPTION clip。但 `_extract_text_content` 第 3 步的 description 正则兜底会提取
场景描述文本（「混剪」/「镜头」-style），这些文本被误路由进字幕轨造成污染。

修复后预期：
- 长场景描述（description 正则兜底提取）+ typewriter → 不产生 kind=CAPTION clip，
  降级为普通 TEXT clip（保留入场 keyframes 动画）。
- 长真实旁白（marker.text / metadata.text）+ typewriter → 仍正常产出 CAPTION clip。

（确保 `_handle_caption` 仍被保留，audio_agent.py 的字幕链路不受影响。）
"""

from __future__ import annotations

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.animation.catalog import AnimationCatalog
from clipwright.schema.timeline import Clip, ClipKind, Track


# >50 字符的真实旁白
_LONG_REAL_TEXT = (
    "人工智能正在改变内容创作的每一个环节，从脚本撰写到画面匹配，"
    "再到声音合成与字幕生成，创作者只需要把精力聚焦在创意与表达本身。"
)


def _mk_text_track() -> Track:
    return Track(id="t1", name="文字轨", kind=ClipKind.TEXT, index=1)


def _mk_video_clip(
    start_sec: float = 2.0,
    duration_sec: float = 8.0,
    *,
    description: str = "",
    text: str = "",
    label: str = "",
    source_title: str = "",
) -> Clip:
    meta: dict = {}
    if description:
        meta["description"] = description
    if text:
        meta["text"] = text
    if label:
        meta["label"] = label
    if source_title:
        meta["source_title"] = source_title
    return Clip(
        id="vid1",
        kind=ClipKind.VIDEO,
        asset_id="a.mp4",
        track_id="t0",
        start_sec=start_sec,
        duration_sec=duration_sec,
        metadata=meta,
    )


def _mk_style() -> dict:
    return AnimationCatalog.resolve_persona_style(None, None)


class TestCaptionRouting:
    """长文本 + typewriter 的字幕路由守卫。"""

    @staticmethod
    def _agent() -> AnimationAgent:
        return AnimationAgent()

    def test_long_scene_description_not_captioned(self) -> None:
        """长场景描述（description 正则兜底）+ typewriter → 不产生 CAPTION clip。"""
        track = _mk_text_track()
        vid = _mk_video_clip(description="[文字动画]打字：" + _LONG_REAL_TEXT)
        marker = {"type": "text", "anim_id": "typewriter", "name": "打字"}
        # 断言前提：确实验证了 _extract_text_content 走的是 description 兜底分支
        extracted = AnimationAgent._extract_text_content(vid, marker)
        assert len(extracted) > 50

        self._agent()._handle_text_animation(
            track, vid, "typewriter", "打字", marker, _mk_style(),
        )

        assert len(track.clips) == 1
        produced = track.clips[0]
        assert str(produced.kind) != "caption", (
            f"场景描述不应进入字幕轨，实际 kind={produced.kind}"
        )
        assert str(produced.kind) == "text"
        # 降级为普通 TEXT clip，保留入场动画 keyframes
        assert produced.metadata["anim_type"] == "typewriter"
        assert len(produced.keyframes) >= 2

    def test_long_metadata_text_still_captioned(self) -> None:
        """长真实旁白（metadata.text）+ typewriter → 仍正常产出 CAPTION clip。"""
        track = _mk_text_track()
        vid = _mk_video_clip(text=_LONG_REAL_TEXT)
        marker = {"type": "text", "anim_id": "typewriter", "name": "打字"}

        self._agent()._handle_text_animation(
            track, vid, "typewriter", "打字", marker, _mk_style(),
        )

        assert len(track.clips) == 1
        produced = track.clips[0]
        assert str(produced.kind) == "caption"
        assert produced.metadata["category"] == "caption"

    def test_long_marker_text_still_captioned(self) -> None:
        """长真实旁白（marker.text）+ typewriter → 仍正常产出 CAPTION clip。"""
        track = _mk_text_track()
        vid = _mk_video_clip(description="场景：混剪科技短片")
        marker = {
            "type": "text", "anim_id": "typewriter", "name": "打字",
            "text": _LONG_REAL_TEXT,
        }

        self._agent()._handle_text_animation(
            track, vid, "typewriter", "打字", marker, _mk_style(),
        )

        assert len(track.clips) == 1
        produced = track.clips[0]
        assert str(produced.kind) == "caption"
        assert produced.metadata["category"] == "caption"

    def test_long_marker_label_still_captioned(self) -> None:
        """长真实旁白（metadata.label）+ typewriter → 仍正常产出 CAPTION clip。"""
        track = _mk_text_track()
        vid = _mk_video_clip(label=_LONG_REAL_TEXT)
        marker = {"type": "text", "anim_id": "typewriter", "name": "打字"}

        self._agent()._handle_text_animation(
            track, vid, "typewriter", "打字", marker, _mk_style(),
        )

        assert len(track.clips) == 1
        produced = track.clips[0]
        assert str(produced.kind) == "caption"

    def test_long_scene_description_short_text_fallback_keeps_keyframes(self) -> None:
        """场景描述降级路径保留入场动画 keyframes（opacity 0→1）。"""
        track = _mk_text_track()
        vid = _mk_video_clip(description="[文字动画]打字：" + _LONG_REAL_TEXT)
        marker = {"type": "text", "anim_id": "typewriter", "name": "打字"}

        self._agent()._handle_text_animation(
            track, vid, "typewriter", "打字", marker, _mk_style(),
        )

        produced = track.clips[0]
        opacities = [
            kf["properties"]["opacity"]
            for kf in produced.keyframes
            if "opacity" in kf.get("properties", {})
        ]
        assert opacities[0] == 0
        assert 1 in opacities
