"""Material 系统 — 素材库注册与搜索。

使用方法：
    from clipwright.material import MaterialRegistry, JsonCatalogSource

    source = JsonCatalogSource("my_lib", "materials/catalog.json")
    MaterialRegistry.register(source)
    results = await MaterialRegistry.search("nature background")
"""

from clipwright.material.base import MaterialSource
from clipwright.material.json_source import JsonCatalogSource
from clipwright.material.local_source import LocalFolderSource, source_from_env
from clipwright.material.rag_source import RagKnowledgeSource
from clipwright.material.registry import MaterialRegistry
from clipwright.material.url_source import UrlMaterialSource

__all__ = [
    "MaterialSource",
    "MaterialRegistry",
    "JsonCatalogSource",
    "LocalFolderSource",
    "UrlMaterialSource",
    "RagKnowledgeSource",
    "source_from_env",
]
