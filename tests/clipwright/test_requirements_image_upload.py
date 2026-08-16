"""C6: 需求附件图片理解测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.services.requirements_service import RequirementsService


@pytest.mark.asyncio
async def test_describe_image_success(tmp_path) -> None:
    """图片上传 → VisionService 描述 + 标签注入对话内容。"""
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    svc = RequirementsService()

    with patch(
        "clipwright.services.vision.VisionService.analyze_image",
        new=AsyncMock(return_value={
            "description": "城市夜景车流",
            "tags": ["城市", "夜景"],
            "labels": ["city"],
        }),
    ):
        content = await svc._describe_image(str(img), "ref.png")

    assert "图片附件" in content
    assert "城市夜景车流" in content
    assert "城市" in content
    assert "city" in content


@pytest.mark.asyncio
async def test_describe_image_fallback_on_error(tmp_path) -> None:
    """视觉服务抛错 → 回退占位符，不抛异常。"""
    img = tmp_path / "bad.png"
    img.write_bytes(b"not-an-image")
    svc = RequirementsService()

    with patch(
        "clipwright.services.vision.VisionService.analyze_image",
        new=AsyncMock(side_effect=RuntimeError("vision down")),
    ):
        content = await svc._describe_image(str(img), "bad.png")

    assert "自动理解失败" in content


@pytest.mark.asyncio
async def test_process_upload_image_uses_vision(tmp_path) -> None:
    """process_upload 对图片扩展名走视觉理解路径（不落占位符）。"""
    img = tmp_path / "shot.jpg"
    img.write_bytes(b"fake-jpeg")
    svc = RequirementsService()

    with patch.object(
        svc, "_describe_image",
        new=AsyncMock(return_value="[图片附件: shot.jpg] 画面描述：海边日落"),
    ) as describe:
        result = await svc.process_upload("sess_c6", str(img), "shot.jpg")

    describe.assert_awaited_once()
    assert "海边日落" in result["content_preview"]
    assert result["file_name"] == "shot.jpg"
