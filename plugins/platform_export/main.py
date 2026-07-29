"""平台导出预设插件 — 各视频平台的专属导出参数。

支持平台：Bilibili / Douyin(TikTok) / YouTube / 微信视频号 / 小红书
通过 PRE_RENDER hook 注入平台参数，POST_RENDER hook 提取封面和元数据。
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.hooks import HookRegistry, HookPoint
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

# 平台导出预设
PLATFORM_PRESETS: dict[str, dict[str, Any]] = {
    "bilibili": {
        "name": "Bilibili",
        "width": 1920, "height": 1080, "fps": 60,
        "bitrate": "6M", "encoder": "libx264", "preset": "medium",
        "aspect": "16:9", "max_duration_sec": 14400,
        "thumbnail_count": 3,
    },
    "bilibili_4k": {
        "name": "Bilibili 4K",
        "width": 3840, "height": 2160, "fps": 60,
        "bitrate": "20M", "encoder": "libx264", "preset": "slow",
        "aspect": "16:9", "max_duration_sec": 14400,
        "thumbnail_count": 3,
    },
    "douyin": {
        "name": "抖音/TikTok",
        "width": 1080, "height": 1920, "fps": 30,
        "bitrate": "5M", "encoder": "libx264", "preset": "medium",
        "aspect": "9:16", "max_duration_sec": 600,
        "thumbnail_count": 1, "vertical": True,
    },
    "youtube": {
        "name": "YouTube",
        "width": 3840, "height": 2160, "fps": 30,
        "bitrate": "12M", "encoder": "libx264", "preset": "slow",
        "aspect": "16:9", "max_duration_sec": 43200,
        "thumbnail_count": 3,
    },
    "wechat_channels": {
        "name": "微信视频号",
        "width": 1080, "height": 1920, "fps": 30,
        "bitrate": "4M", "encoder": "libx264", "preset": "medium",
        "aspect": "9:16", "max_duration_sec": 1800,
        "thumbnail_count": 1, "vertical": True,
    },
    "xiaohongshu": {
        "name": "小红书",
        "width": 1080, "height": 1440, "fps": 30,
        "bitrate": "5M", "encoder": "libx264", "preset": "medium",
        "aspect": "3:4", "max_duration_sec": 900,
        "thumbnail_count": 1,
    },
}


def _pre_render_hook(context: dict[str, Any]) -> dict[str, Any]:
    """PRE_RENDER hook: 注入平台导出参数。"""
    platform = context.get("platform", "")
    if platform and platform in PLATFORM_PRESETS:
        preset = PLATFORM_PRESETS[platform]
        settings = context.get("settings", {})
        settings.update({
            "width": preset["width"],
            "height": preset["height"],
            "fps": preset["fps"],
            "bitrate": preset["bitrate"],
        })
        context["settings"] = settings
        logger.info("[PlatformExport] 注入 %s 预设: %dx%d@%dfps", platform, preset["width"], preset["height"], preset["fps"])
    return context


def _post_render_hook(context: dict[str, Any]) -> dict[str, Any]:
    """POST_RENDER hook: 提取封面截图。"""
    platform = context.get("platform", "")
    output_path = context.get("output_path", "")
    if platform and output_path and platform in PLATFORM_PRESETS:
        preset = PLATFORM_PRESETS[platform]
        count = preset.get("thumbnail_count", 1)
        context["thumbnail_count"] = count
        logger.info("[PlatformExport] %s 渲染完成，需提取 %d 张封面", platform, count)
    return context


class PlatformExportPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="platform_export", name="Platform Export Presets", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Platform-specific export presets for Bilibili/Douyin/YouTube/WeChat/Xiaohongshu",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        HookRegistry.register(HookPoint.PRE_RENDER, _pre_render_hook, plugin_id=self.manifest.id)
        HookRegistry.register(HookPoint.POST_RENDER, _post_render_hook, plugin_id=self.manifest.id)
        logger.info("[PlatformExport] %d 个平台预设已注册: %s", len(PLATFORM_PRESETS), ", ".join(PLATFORM_PRESETS.keys()))

    def shutdown(self) -> None:
        pass


__all__ = ["PlatformExportPlugin", "PLATFORM_PRESETS"]
