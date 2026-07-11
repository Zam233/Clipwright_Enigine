"""Material Library Example Plugin.

Demonstrates three material source types:
1. JsonCatalogSource — from a JSON catalog file
2. UrlMaterialSource — pre-configured URLs
3. RagKnowledgeSource — RAG knowledge base for a Persona
"""

from __future__ import annotations

from pathlib import Path

from clipwright.material import (
    JsonCatalogSource,
    MaterialRegistry,
    RagKnowledgeSource,
    UrlMaterialSource,
)
from clipwright.plugins import CapabilityPlugin
from clipwright.schema.plugin import PluginManifest, PluginKind


class MyMaterialLibPlugin(CapabilityPlugin):
    """示例素材库插件：注册三类素材源。"""

    manifest = PluginManifest(
        id="my_material_lib",
        name="My Material Library",
        version="1.0.0",
        kind=PluginKind.MATERIAL_SOURCE,
        description="Example material library: JSON catalog + URL + RAG",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        plugin_dir = Path(__file__).resolve().parent

        # 1. JSON Catalog 源
        catalog_path = plugin_dir / "catalog.json"
        if catalog_path.exists():
            json_source = JsonCatalogSource(
                source_id="my_json_catalog",
                catalog_path=catalog_path,
                source_name="JSON Catalog",
            )
            MaterialRegistry.register(json_source, plugin_id=self.manifest.id)

        # 2. URL 素材源
        url_source = UrlMaterialSource(
            source_id="my_url_lib",
            base_url="https://cdn.example.com/materials",
            source_name="URL Library",
        )
        url_source.add_url(
            url="videos/ocean_broll.mp4",
            title="Ocean B-Roll",
            tags=["nature", "ocean", "b-roll"],
        )
        url_source.add_url(
            url="videos/city_timelapse.mp4",
            title="City Timelapse",
            tags=["city", "timelapse", "urban"],
        )
        MaterialRegistry.register(url_source, plugin_id=self.manifest.id)

        # 3. RAG 知识库（指向 demo Persona）
        rag_source = RagKnowledgeSource(
            source_id="my_rag_knowledge",
            persona_id="zam_knowledge_critical",
            source_name="RAG Knowledge",
        )
        MaterialRegistry.register(rag_source, plugin_id=self.manifest.id)

    def shutdown(self) -> None:
        pass


__all__ = ["MyMaterialLibPlugin"]
