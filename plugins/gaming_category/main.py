"""游戏集锦视频类型插件。

特点：反应摄像头 PIP、击杀信息叠加、快速缩放高光、Meme 音效触发、按游戏回合分章。
"""
from __future__ import annotations
from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.plugin import PluginManifest, PluginKind

class GamingCategoryPlugin(BaseCategoryPlugin):
    manifest = PluginManifest(id="gaming_category", name="Gaming Video Type", version="1.0.0",
        kind=PluginKind.CATEGORY, description="Gaming highlight video editing strategy", author="Clipwright Team")

    def get_shot_params(self) -> dict:
        return {"min_shot_sec": 1.5, "max_shot_sec": 6.0, "transition_type": "cut", "transition_duration_sec": 0.05, "cut_on_beat": True}

    def get_structure_template(self) -> list[dict]:
        return [{"role": "intro", "weight": 0.5, "desc": "高能预告"}, {"role": "gameplay", "weight": 5.0, "desc": "游戏实况"},
                {"role": "highlight", "weight": 2.0, "desc": "精彩击杀/操作回放"}, {"role": "outro", "weight": 0.5, "desc": "结尾订阅引导"}]

    def get_annotation_templates(self) -> list[dict]:
        return [{"type": "kill_feed", "position": "top-right", "style": "fps_overlay"},
                {"type": "reaction_pip", "position": "bottom-left", "style": "circle_cam"},
                {"type": "zoom_highlight", "position": "center", "style": "punch_in"},
                {"type": "meme_sfx", "position": "center", "style": "pop"}]

    def get_pacing(self) -> dict:
        return {"cuts_per_minute": 25, "text_density": "medium", "animation_style": "energetic", "bgm_volume": 0.3}

    def initialize(self) -> None:
        CategoryRegistry.register(self, plugin_id=self.manifest.id)
        print("[GamingCategory] 游戏集锦类型已注册")

    def shutdown(self) -> None: pass

__all__ = ["GamingCategoryPlugin"]
