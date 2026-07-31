"""视频类型插件基类。

每个视频类型插件需要继承 BaseCategoryPlugin 并实现其接口。
Persona 配置层不直接调用原子能力，必须经过类型插件层翻译。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from clipwright.plugins.base import BasePlugin
from clipwright.schema.persona import ParameterLayer
from clipwright.schema.timeline import Clip, Timeline


class BaseCategoryPlugin(BasePlugin):
    """视频类型插件基类。"""

    # 插件标识
    plugin_id: str = ""
    display_name: str = ""
    description: str = ""

    def initialize(self) -> None:
        """默认初始化（注册到 CategoryRegistry）。子类可覆盖。"""
        pass

    def shutdown(self) -> None:
        """默认关闭。子类可覆盖。"""
        pass

    @abstractmethod
    def translate_persona(self, params: ParameterLayer) -> dict[str, Any]:
        """将 Persona 参数翻译为具体的剪辑参数。

        Persona 层的配置是 UP 主维度的（如 cut_density: high），
        此方法将其翻译为该视频类型的具体剪辑参数（如 min_shot_ms: 800）。
        """
        ...

    @abstractmethod
    def get_shot_params(self, translated: dict[str, Any]) -> dict[str, Any]:
        """获取镜头级别剪辑参数。"""
        ...

    def post_process_timeline(self, timeline: Timeline) -> Timeline:
        """对生成的时间线做类型特定的后处理。

        基类提供默认实现（直接返回），子类可覆盖。
        """
        return timeline

    def validate_clip(self, clip: Clip) -> bool:
        """验证单个片段是否符合本类型的规范。"""
        return True

    def get_mg_style_guidance(self) -> str:
        """返回本视频类型的 MG 动画风格指引。

        该指引会被注入 LLM MG 生成器（llm_mg）的 system prompt，
        使生成的动画符合视频类型的视觉气质。
        基类返回空字符串（不注入）；子类可覆盖，返回自然语言风格描述。
        """
        return ""
