"""LUT 调色预设插件 — 提供精选 .cube LUT 文件和自动风格匹配。

预设列表：
  - cinematic_teal_orange: 电影感青橙调
  - vintage_film: 复古胶片
  - cyberpunk_neon: 赛博朋克霓虹
  - documentary_neutral: 纪录片中性
  - food_warm: 美食暖调
  - noir_bw: 黑白黑色电影

通过 StyleInterpreterPlugin 注册，根据 Persona 语气自动推荐 LUT。
注意：实际 .cube 文件需用户自行放置到 PluginData/luts/ 目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clipwright.plugins.base import StyleInterpreterPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

LUT_PRESETS: dict[str, dict[str, str]] = {
    "cinematic_teal_orange": {"name": "电影青橙", "file": "cinematic_teal_orange.cube", "mood": "dramatic"},
    "vintage_film": {"name": "复古胶片", "file": "vintage_film.cube", "mood": "nostalgic"},
    "cyberpunk_neon": {"name": "赛博霓虹", "file": "cyberpunk_neon.cube", "mood": "energetic"},
    "documentary_neutral": {"name": "纪录片中性", "file": "documentary_neutral.cube", "mood": "neutral"},
    "food_warm": {"name": "美食暖调", "file": "food_warm.cube", "mood": "warm"},
    "noir_bw": {"name": "黑白黑色", "file": "noir_bw.cube", "mood": "serious"},
}

LUT_DIR = Path("PluginData/luts")


class LutPresetsPlugin(StyleInterpreterPlugin):
    manifest = PluginManifest(
        id="lut_presets", name="LUT Color Grading Presets", version="1.0.0",
        kind=PluginKind.STYLE,
        description="Curated LUT color grading presets with auto style matching",
        author="Clipwright Team",
    )

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：风格解释器 + DIAGRAM_STYLE_PRESET 钩子。

        副作用（建 LUT 目录）放 setup()；可用 LUT 数量仅用于日志。
        """
        available = {k: v for k, v in LUT_PRESETS.items() if (LUT_DIR / v["file"]).exists()}
        logger.info("[LutPresets] %d/%d LUT 预设可用 (目录: %s)",
                    len(available), len(LUT_PRESETS), LUT_DIR)
        if not available:
            logger.info("[LutPresets] 提示: 将 .cube 文件放入 %s/ 以启用", LUT_DIR)
        try:
            priority = int((self.config or {}).get("priority", 90))
        except (TypeError, ValueError):
            priority = 90
        return [
            Contribution(
                ContributionKind.STYLE_INTERPRETER, self.manifest.id, self,
                meta={"priority": priority},
            ),
            Contribution(
                ContributionKind.HOOK, "lut_presets_diagram_style",
                self._style_preset_hook, meta={"point": "diagram_style_preset"},
            ),
        ]

    def setup(self) -> None:
        """副作用位（P1）：确保 LUT 目录存在（与注册无关，须无条件执行）。"""
        LUT_DIR.mkdir(parents=True, exist_ok=True)

    async def interpret(
        self, visual_config: dict[str, Any], persona_context: dict[str, Any]
    ) -> dict[str, Any]:
        """根据 Persona 语气推荐 LUT（轮 84：修正签名以匹配基类抽象方法）。"""
        identity = persona_context.get("identity", {}) or {}
        tone = str(
            visual_config.get("tone")
            or identity.get("tone")
            or persona_context.get("tone")
            or ""
        ).lower()
        mood_map = {
            "严肃": "documentary_neutral", "专业": "documentary_neutral",
            "活泼": "cyberpunk_neon", "搞笑": "cyberpunk_neon",
            "怀旧": "vintage_film", "温暖": "food_warm",
            "戏剧": "cinematic_teal_orange", "电影": "cinematic_teal_orange",
        }
        for keyword, preset_id in mood_map.items():
            if keyword in tone:
                preset = LUT_PRESETS.get(preset_id, {})
                lut_path = LUT_DIR / preset.get("file", "")
                if lut_path.exists():
                    return {"lut_path": str(lut_path), "lut_name": preset.get("name", preset_id)}
        return {}

    @staticmethod
    def _style_preset_hook(context: dict[str, Any]) -> dict[str, Any]:
        return context

    def shutdown(self) -> None:
        pass


__all__ = ["LutPresetsPlugin", "LUT_PRESETS"]
