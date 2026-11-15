"""text_effect_card：渐变/撕纸/霓虹/故障静态卡 PNG。"""

from __future__ import annotations

import pytest

from clipwright.tool.text_effect_card import TextEffectCardTool


@pytest.mark.asyncio
async def test_gradient_paper_glitch_card(tmp_path):
    out = tmp_path / "card.png"
    result = await TextEffectCardTool().execute(
        "渐变霓虹",
        style={
            "font_size": 96,
            "fill": {"type": "gradient", "colors": ["#ff00ff", "#00ffff"]},
            "box": True,
            "background": {"type": "paper"},
            "glitch": True,
            "glow": True,
            "glow_color": "#00e5ff",
        },
        output_path=str(out),
    )
    assert result.status == "success", result.error
    assert out.exists() and out.stat().st_size > 1000
    from PIL import Image
    img = Image.open(out)
    assert img.mode == "RGBA"
    assert img.size == (1920, 1080)
    # 中心区域确有非透明像素
    alpha = img.getchannel("A")
    assert alpha.getbbox() is not None
