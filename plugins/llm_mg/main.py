"""LLM Motion Graphics Generator Plugin — 主入口。"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

from .generator import MGGenerator
from .storage import MGStorage


class LLMMGPlugin(CapabilityPlugin):
    """LLM 驱动的 MG 动画生成插件。

    从自然语言需求动态生成完整的 MG 动画 JSON，
    通过现有 MGRenderer → Hyperframes 管线渲染为视频覆盖层。
    """

    manifest = PluginManifest(
        id="llm_mg",
        name="LLM Motion Graphics Generator",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="LLM 驱动的动态 MG 动画生成 — 从自然语言需求生成 HTML/CSS 动画",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        super().__init__()
        self._generator: MGGenerator | None = None
        self._storage: MGStorage | None = None

    def initialize(self) -> None:
        """初始化插件：加载生成器和存储。"""
        self._generator = MGGenerator()
        self._storage = MGStorage()
        logger.info("LLMMGPlugin initialized, templates=%d", len(self._storage.get_templates()))

    def shutdown(self) -> None:
        """插件卸载。"""
        self._generator = None
        self._storage = None

    async def generate_mg(
        self,
        description: str,
        text_content: str,
        persona_style: dict[str, Any] | None = None,
        scene_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成 MG 动画。

        Args:
            description: 自然语言动画需求描述
            text_content: 动画中的文本内容（| 分隔多段）
            persona_style: Persona visual_config 风格参数
            scene_context: 当前场景上下文 {title, keywords}

        Returns:
            {success, html, mg_def, method, fallback_template, generation_id}
        """
        if self._generator is None:
            self._generator = MGGenerator()
        return await self._generator.generate(
            description=description,
            text_content=text_content,
            persona_style=persona_style or {},
            scene_context=scene_context or {},
        )

    def save_as_template(self, generation_id: str, custom_name: str = "") -> str:
        """将生成的 MG 动画保存为可复用模板。"""
        if self._storage is None:
            self._storage = MGStorage()
        return self._storage.save_as_template(generation_id, custom_name)

    def list_templates(self) -> list[dict]:
        """列出所有可用模板。"""
        if self._storage is None:
            self._storage = MGStorage()
        return self._storage.get_templates()

    def list_generations(self) -> list[dict]:
        """列出未保存的生成记录。"""
        if self._storage is None:
            self._storage = MGStorage()
        return self._storage.list_generations()
