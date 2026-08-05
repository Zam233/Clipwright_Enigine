"""T2 marker JSON payload 提取测试。

覆盖修复：structure_agent 写入 `[逻辑动画]mg_dynamic:{"description":...,"text":...}`
JSON payload 标记时，catalog._extract() 不得把整个 JSON 串当作 marker["text"]，
否则 JSON 片段会被 FallbackEngine.extract_keywords 拆碎并渲染为屏上大字。
"""

from __future__ import annotations

import json

import pytest

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.animation.catalog import AnimationCatalog
from clipwright.schema.timeline import Clip, ClipKind, Track


class TestCatalogJsonMarkerParsing:
    def test_parse_mg_dynamic_json_marker(self) -> None:
        desc = (
            '开场画面 [逻辑动画]mg_dynamic:'
            '{"description":"三阶段增长柱状图","text":"2023|2024|2025","style":"tech_dark"}'
        )
        markers = AnimationCatalog.parse_marker_from_description(desc)
        assert markers, "应解析出至少一个标记"
        marker = markers[0]
        assert marker["text"] == "2023|2024|2025"
        assert marker["description"] == "三阶段增长柱状图"
        assert marker["style"] == "tech_dark"
        assert not marker["text"].strip().startswith("{"), "text 不得残留原始 JSON"

    def test_parse_mg_dynamic_no_text_field(self) -> None:
        """payload 无 text 字段 → text 回退到 description（绝不保留 JSON）。"""
        desc = (
            '[逻辑动画]mg_dynamic:'
            '{"description":"纯描述无文字","style":"tech_dark"}'
        )
        markers = AnimationCatalog.parse_marker_from_description(desc)
        assert markers
        marker = markers[0]
        assert marker["text"] == "纯描述无文字"
        assert not marker["text"].strip().startswith("{")

    def test_parse_plain_marker_unchanged(self) -> None:
        """普通文字标记行为不变。"""
        markers = AnimationCatalog.parse_marker_from_description("[逻辑动画]箭头：A→B→C")
        assert markers
        assert markers[0]["text"] == "A→B→C"


class TestHandleMgAnimationStaleJson:
    @pytest.mark.asyncio
    async def test_handle_mg_animation_strips_stale_json(self, monkeypatch) -> None:
        """旧时间线残留的 JSON marker["text"] 不得泄漏到 anim_clip.text。"""
        from clipwright.animation.mg_renderer import MGRenderer

        # 避免依赖真实模板/渲染：桩掉 load_animation 与 render
        monkeypatch.setattr(
            MGRenderer, "load_animation",
            staticmethod(lambda anim_id: {
                "id": anim_id,
                "params": {"text": {"default": ""}},
                "duration_sec": 3.0,
                "elements": [],
            }),
        )
        monkeypatch.setattr(
            MGRenderer, "render",
            staticmethod(lambda mg_def, mg_params, width, height, fps: "<html>fake</html>"),
        )

        stale_text = json.dumps(
            {"description": "x", "text": "A|B"}, ensure_ascii=False
        )
        desc = f"场景 [逻辑动画]mg_dynamic:{stale_text}"
        vid_clip = Clip(
            id="vc1", kind=ClipKind.VIDEO, asset_id="a1", track_id="tr_video",
            start_sec=0.0, duration_sec=5.0,
            metadata={"description": desc},
        )
        anim_track = Track(id="tr_anim", kind=ClipKind.ANIMATION, index=1)

        agent = AnimationAgent()
        agent._tl_width, agent._tl_height, agent._tl_fps = 1920, 1080, 30.0

        await agent._handle_mg_animation(
            anim_track, vid_clip, "mg_fake", "测试MG",
            stale_text, 3.0, {"text": stale_text},
        )

        assert anim_track.clips, "应创建动画 clip"
        anim_clip = anim_track.clips[0]
        assert anim_clip.text == "A|B"
        assert not anim_clip.text.strip().startswith("{")
