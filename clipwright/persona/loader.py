"""Persona YAML 加载器。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

import yaml

from clipwright.schema.persona import (
    EmbeddingLayer,
    ExemplarLayer,
    KnowledgeDoc,
    ModelLayer,
    ParameterLayer,
    PersonaManifest,
)


class PersonaLoadError(Exception):
    """Persona 加载失败异常。"""


def load_persona_manifest(persona_dir: Path) -> PersonaManifest:
    """从目录加载 Persona manifesto（persona.yaml）。"""
    manifest_path = persona_dir / "persona.yaml"
    if not manifest_path.exists():
        raise PersonaLoadError(f"Persona manifest not found: {manifest_path}")

    with open(manifest_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    manifest = PersonaManifest(**data)

    # 尝试加载各层
    param_path = persona_dir / "parameter.yaml"
    if param_path.exists():
        with open(param_path, encoding="utf-8") as f:
            param_data = yaml.safe_load(f)
        if param_data:
            manifest.parameter = ParameterLayer(**param_data)

    exemplar_dir = persona_dir / "exemplar"
    exemplar_index = exemplar_dir / "index.yaml"
    if exemplar_index.exists():
        with open(exemplar_index, encoding="utf-8") as f:
            exemplar_data = yaml.safe_load(f)
        if exemplar_data:
            manifest.exemplar = ExemplarLayer(**exemplar_data)

    embeddings_dir = persona_dir / "embeddings"
    stats_path = embeddings_dir / "stats.yaml"
    if stats_path.exists():
        with open(stats_path, encoding="utf-8") as f:
            embed_data = yaml.safe_load(f)
        if embed_data:
            manifest.embedding = EmbeddingLayer(**embed_data)

    # 加载 Prompt
    prompt_path = persona_dir / "prompt.md"
    if prompt_path.exists():
        manifest.prompt = prompt_path.read_text(encoding="utf-8")

    # 加载 RAG 知识库
    kdir = persona_dir / "knowledge"
    index_path = kdir / "index.yaml"
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            index_data = yaml.safe_load(f) or []
        docs = []
        for entry in index_data:
            fpath = kdir / entry["file"]
            content = fpath.read_text(encoding="utf-8") if fpath.exists() else ""
            docs.append(KnowledgeDoc(
                id=entry.get("id", ""),
                title=entry.get("title", ""),
                content=content,
                source=entry.get("source", ""),
                created_at=entry.get("created_at", ""),
            ))
        if docs:
            manifest.knowledge = docs

    return manifest


def load_persona_by_id(
    persona_id: str,
    persona_root: Optional[Path] = None,
) -> PersonaManifest:
    """按 ID 加载 Persona。

    Args:
        persona_id: Persona 唯一 ID（对应 personas/{persona_id}/ 目录）
        persona_root: Persona 根目录，默认使用全局配置路径
    """
    if persona_root is None:
        from clipwright.config import settings
        persona_root = settings.persona_dir

    persona_dir = persona_root / persona_id
    return load_persona_manifest(persona_dir)


def load_persona_or_default(
    persona_id: str,
    persona_root: Optional[Path] = None,
) -> PersonaManifest:
    """按 ID 加载 Persona；不存在时回退到默认 Persona（而非抛异常使管线失败）。"""
    try:
        return load_persona_by_id(persona_id, persona_root)
    except PersonaLoadError:
        from clipwright.config import logger
        logger.warning("Persona %s 不存在，回退到默认 Persona 配置", persona_id)
        pid = persona_id or "default"
        return PersonaManifest(
            persona_id=pid,
            persona_name="默认",
            parameter=ParameterLayer(persona_id=pid),
        )


def resolve_inheritance(manifest: PersonaManifest) -> PersonaManifest:
    """解析 Persona 的继承链，合并所有覆盖和组合。

    当前实现仅做单层继承解析。多层继承需要递归。
    """
    if not manifest.inherits:
        return manifest

    from clipwright.config import settings
    parent = load_persona_by_id(manifest.inherits, settings.persona_dir)

    if manifest.parameter is None and parent.parameter is not None:
        manifest.parameter = parent.parameter.model_copy(deep=True)

    if manifest.exemplar is None and parent.exemplar is not None:
        manifest.exemplar = parent.exemplar.model_copy(deep=True)

    if manifest.embedding is None and parent.embedding is not None:
        manifest.embedding = parent.embedding.model_copy(deep=True)

    if manifest.model is None and parent.model is not None:
        manifest.model = parent.model.model_copy(deep=True)

    return manifest
