"""画中画必须用「与主画面不同」的素材（E2E 2026-10-28）。

现象：成片里有 5 段画中画（54.75s），逐段核对发现 **5/5 与同刻主画面 asset_id
完全相同** —— 右下角小窗里就是主画面的缩小复制品，零信息量。

根因（edit_agent 旧实现）：
```python
pip_clip = self._make_clip(..., asset=unit["asset"], processed_path=unit["processed_path"])
if pip_clip.asset_id == vid_track.clips[-1].asset_id:
    notes.append("画中画与主画面同源，建议更换素材或移除画中画")   # 只写备注
pip_clip.image_rect = {"x": 0.65, "y": 0.55, "w": 0.3, "h": 0.3}   # 写死
pip_track.clips.append(pip_clip)                                   # 照样 append
```

修复后的契约：
1. PiP 源必须与主画面**不同**；拿不到就不建 PiP（并留痕）；
2. `image_rect` 可由 `scene.pip_rect` / `visual_description.pip_rect` 指定（钳制到画布内），
   否则退回右下 30%；
3. 裁剪失败也算"拿不到"，不硬塞。
"""

from __future__ import annotations

from clipwright.agents.edit_agent import EditAgent

#: 只测纯逻辑，不走 __init__（避免构造 LLMService）
agent = EditAgent.__new__(EditAgent)

CANDIDATES = [
    {
        "scene_index": 3,
        "suggested_assets": [
            {"asset_id": "a_main", "local_path": "/tmp/main.mp4", "score": 0.9},
            {"asset_id": "a_alt", "local_path": "/tmp/alt.mp4", "score": 0.7},
        ],
    },
    {
        "scene_index": 4,
        "suggested_assets": [
            {"asset_id": "a_only", "url": "https://x/only.mp4", "score": 0.8},
        ],
    },
]


def test_pick_pip_alt_skips_main_asset():
    alt = agent._pick_pip_alt_asset(CANDIDATES, 3, exclude={"/tmp/main.mp4"})

    assert alt is not None
    assert alt["asset_id"] == "a_alt", "必须挑到与主画面不同的那段"


def test_pick_pip_alt_returns_none_when_only_main_exists():
    alt = agent._pick_pip_alt_asset(CANDIDATES, 4, exclude={"https://x/only.mp4"})

    assert alt is None, "只有主画面同源素材时宁可不建画中画"


def test_pick_pip_alt_ignores_other_scenes():
    alt = agent._pick_pip_alt_asset(CANDIDATES, 99, exclude=set())

    assert alt is None


def test_pip_rect_default_and_override():
    assert agent._pip_rect_for({}) == {"x": 0.65, "y": 0.55, "w": 0.3, "h": 0.3}

    rect = agent._pip_rect_for({"pip_rect": {"x": 0.05, "y": 0.05, "w": 0.4, "h": 0.4}})
    assert rect == {"x": 0.05, "y": 0.05, "w": 0.4, "h": 0.4}

    # visual_description 里也能给
    rect2 = agent._pip_rect_for(
        {"visual_description": {"pip_rect": {"x": 0.6, "y": 0.1, "w": 0.25, "h": 0.25}}}
    )
    assert rect2["x"] == 0.6 and rect2["w"] == 0.25


def test_pip_rect_is_clamped_inside_canvas():
    rect = agent._pip_rect_for({"pip_rect": {"x": 0.95, "y": 1.2, "w": 0.3, "h": 0.02}})

    assert rect["x"] + rect["w"] <= 1.0001
    assert rect["y"] + rect["h"] <= 1.0001
    assert rect["h"] >= 0.1, "太小的画中画会被钳到 10%"
