"""平台导出预设插件 — 各视频平台的专属导出参数（**纯数据贡献**）。

2026-11-08 审计整改（B8-T8.5）：旧实现把 6 个预设写在插件里、却靠 `pre_render` hook
往 `context["settings"]` 里塞值——而 engine 只把 `{"task_id"}` 传进 hook，平台名与
设置项都不在上下文里，所以**这段逻辑从未执行过**（典型的"钩子形状错了"）。

现在改成：
- 6 个预设以 `export_preset` 纯数据贡献（`object` 必须为 `None`，全部信息在 meta）；
- engine 的 `/api/render/presets` 与 `_resolve_settings` 合并"内置预设 + 贡献预设"；
- hook 只做**声明式补丁**：从上下文拿 `preset`，把该预设的参数并回 `settings`
  （engine 已把 `settings`/`preset` 放进 PRE_RENDER 上下文）。

禁用本插件 → 平台预设消失（口径 A：能力缺失是一等状态，不做内置兜底）。
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind

#: 平台导出预设（meta 形状，由 `_validate_export_preset` 在注册期校验）
PLATFORM_PRESETS: dict[str, dict[str, Any]] = {
    "bilibili": {
        "width": 1920, "height": 1080, "fps": 60,
        "bitrate": "6M", "audio_bitrate": "320k",
        "encoder": "libx264",
        "note": "B站推荐 1080p60 高码率（16:9）",
        "thumbnail_count": 3, "max_duration_sec": 14400,
    },
    "bilibili_4k": {
        "width": 3840, "height": 2160, "fps": 60,
        "bitrate": "20M", "audio_bitrate": "320k",
        "encoder": "libx264",
        "note": "B站 4K（16:9）",
        "thumbnail_count": 3, "max_duration_sec": 14400,
    },
    "douyin": {
        "width": 1080, "height": 1920, "fps": 30,
        "bitrate": "5M", "audio_bitrate": "192k",
        "encoder": "libx264",
        "note": "抖音/TikTok 竖屏（9:16）",
        "thumbnail_count": 3, "max_duration_sec": 600, "vertical": True,
    },
    "youtube": {
        "width": 3840, "height": 2160, "fps": 30,
        "bitrate": "12M", "audio_bitrate": "192k",
        "encoder": "libx264",
        "note": "YouTube 4K（16:9）",
        "thumbnail_count": 3, "max_duration_sec": 43200,
    },
    "wechat_channels": {
        "width": 1080, "height": 1920, "fps": 30,
        "bitrate": "4M", "audio_bitrate": "192k",
        "encoder": "libx264",
        "note": "微信视频号 竖屏（9:16）",
        "thumbnail_count": 1, "max_duration_sec": 1800, "vertical": True,
    },
    "xiaohongshu": {
        "width": 1080, "height": 1440, "fps": 30,
        "bitrate": "5M", "audio_bitrate": "192k",
        "encoder": "libx264",
        "note": "小红书 3:4",
        "thumbnail_count": 1, "max_duration_sec": 900,
    },
}


def _pre_render_hook(context: dict[str, Any]) -> dict[str, Any]:
    """PRE_RENDER：把当前预设的参数并回 settings（声明式补丁，不计算）。"""
    preset_name = str(context.get("preset") or "")
    preset = PLATFORM_PRESETS.get(preset_name)
    if preset is None:
        return {}
    settings = context.get("settings")
    if not isinstance(settings, dict):
        return {}
    for key in ("width", "height", "fps", "bitrate", "audio_bitrate", "encoder"):
        if key in preset:
            settings[key] = preset[key]
    logger.info(
        "[PlatformExport] %s 预设已并入渲染参数: %dx%d@%sfps",
        preset_name, preset["width"], preset["height"], preset["fps"],
    )
    return {"settings": settings}


def _post_render_hook(context: dict[str, Any]) -> dict[str, Any]:
    """POST_RENDER：告诉宿主该预设需要几张封面（实际抽帧由宿主执行）。"""
    preset_name = str(context.get("preset") or "")
    preset = PLATFORM_PRESETS.get(preset_name)
    if preset is None or not context.get("output_path"):
        return {}
    count = int(preset.get("thumbnail_count", 0) or 0)
    logger.info("[PlatformExport] %s 渲染完成，需提取 %d 张封面", preset_name, count)
    return {"thumbnail_count": count}


class PlatformExportPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="platform_export", name="Platform Export Presets", version="1.1.0",
        kind=PluginKind.CAPABILITY,
        description="平台专属导出预设（纯数据贡献）：B站/抖音/YouTube/视频号/小红书",
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        """6 个 `export_preset` 纯数据贡献 + 两个声明式 hook。"""
        items: list[Any] = []
        for preset_id, spec in PLATFORM_PRESETS.items():
            display = str(spec.get("note") or preset_id)
            payload = {
                "name": display,
                "width": spec["width"], "height": spec["height"],
                "fps": spec["fps"], "bitrate": spec["bitrate"],
            }
            items.append(
                Contribution(
                    ContributionKind.EXPORT_PRESET, preset_id, None,
                    # 顶层字段供 `_validate_export_preset` 校验；title/payload 是前端
                    # `usePluginContributions('export_preset')` 的既有契约（D-3h）。
                    meta={"name": display, "title": display, "payload": payload, **spec},
                ),
            )
        items.append(
            Contribution(
                ContributionKind.HOOK, "platform_export_pre_render", _pre_render_hook,
                meta={"point": "pre_render"},
            ),
        )
        items.append(
            Contribution(
                ContributionKind.HOOK, "platform_export_post_render", _post_render_hook,
                meta={"point": "post_render"},
            ),
        )
        return items

    def shutdown(self) -> None:
        pass


__all__ = ["PlatformExportPlugin", "PLATFORM_PRESETS"]
