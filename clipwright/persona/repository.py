"""Persona 存储库 — YAML / Prompt / RAG 知识库文件存储。

每个 Persona 的磁盘结构：
personas/{id}/
├── persona.yaml           # manifest（元信息 + 四层引用）
├── parameter.yaml         # YAML 参数层
├── prompt.md              # Prompt 指令
├── knowledge/             # RAG 知识库
│   └── index.yaml         #   知识库索引
├── chat_transcript.json   # 对话创建记录（可选）
├── exemplar/              # 示例层（可选）
├── embeddings/            # 嵌入层（可选）
└── models/                # 模型层（可选）
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from clipwright.schema.persona import KnowledgeDoc, PersonaManifest


class PersonaRepository:
    """Persona 存储库，管理 Persona 的 CRUD 操作。"""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls) -> PersonaRepository:
        from clipwright.config import settings
        return cls(settings.persona_dir)

    def list_personas(self) -> list[str]:
        if not self.root_dir.exists():
            return []
        return sorted(
            d.name
            for d in self.root_dir.iterdir()
            if d.is_dir() and (d / "persona.yaml").exists()
        )

    def exists(self, persona_id: str) -> bool:
        return (self.persona_path(persona_id) / "persona.yaml").exists()

    def persona_path(self, persona_id: str) -> Path:
        from clipwright.security import validate_id
        validate_id(persona_id, "persona_id")
        return self.root_dir / persona_id

    # ── 保存 ──

    def save_manifest(self, manifest: PersonaManifest) -> None:
        """保存 Persona 的三个组成部分到磁盘。"""
        pdir = self.persona_path(manifest.persona_id)
        pdir.mkdir(parents=True, exist_ok=True)

        # 1. yaml —— manifest 元信息
        manifest_path = pdir / "persona.yaml"
        with open(manifest_path, "w", encoding="utf-8") as f:
            yaml.dump(
                manifest.model_dump(
                    exclude={"parameter", "prompt", "vision_prompt", "knowledge", "exemplar", "embedding", "model"},
                    mode="json",
                ),
                f,
                allow_unicode=True,
                default_flow_style=False,
            )

        # 2. YAML —— parameter 参数层
        if manifest.parameter:
            param_path = pdir / "parameter.yaml"
            with open(param_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    manifest.parameter.model_dump(mode="json"),
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                )

        # 3. Prompt —— 指令文本
        if manifest.prompt:
            prompt_path = pdir / "prompt.md"
            prompt_path.write_text(manifest.prompt, encoding="utf-8")

        # 3b. Vision Prompt —— 视觉需求提示词（no-clobber：仅当文件不存在时写入）
        if manifest.vision_prompt:
            self._save_vision_prompt_noclobber(pdir, manifest.vision_prompt)

        # 4. RAG —— 知识库文档
        if manifest.knowledge:
            kdir = pdir / "knowledge"
            kdir.mkdir(parents=True, exist_ok=True)
            index = []
            for doc in manifest.knowledge:
                doc_id = doc.id or f"doc_{len(index) + 1}"
                fname = f"{doc_id}.md"
                (kdir / fname).write_text(doc.content, encoding="utf-8")
                index.append({
                    "id": doc_id,
                    "title": doc.title,
                    "source": doc.source,
                    "file": fname,
                    "created_at": doc.created_at,
                })
            # 知识库索引
            index_path = kdir / "index.yaml"
            with open(index_path, "w", encoding="utf-8") as f:
                yaml.dump(index, f, allow_unicode=True, default_flow_style=False)

            # 自动向量化索引
            self._reindex_knowledge(manifest.persona_id)

    # ── 加载（完整加载，含 prompt + knowledge） ──

    def load_manifest(self, persona_id: str) -> PersonaManifest:
        """从磁盘加载完整 Persona（含 prompt 和 knowledge）。"""
        from clipwright.persona.loader import load_persona_manifest
        pdir = self.persona_path(persona_id)
        manifest = load_persona_manifest(pdir)

        # 加载 prompt
        prompt_path = pdir / "prompt.md"
        if prompt_path.exists():
            manifest.prompt = prompt_path.read_text(encoding="utf-8")

        # 加载 vision_prompt
        vision_prompt_path = pdir / "vision_prompt.md"
        if vision_prompt_path.exists():
            manifest.vision_prompt = vision_prompt_path.read_text(encoding="utf-8")

        # 加载 knowledge
        kdir = pdir / "knowledge"
        index_path = kdir / "index.yaml"
        if index_path.exists():
            with open(index_path, encoding="utf-8") as f:
                index = yaml.safe_load(f) or []
            docs = []
            for entry in index:
                file_path = kdir / entry["file"]
                content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
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

    # ── 部分更新 ──

    def save_prompt(self, persona_id: str, prompt_text: str) -> None:
        """单独保存/更新 Prompt。"""
        pdir = self.persona_path(persona_id)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "prompt.md").write_text(prompt_text, encoding="utf-8")

    def save_vision_prompt(self, persona_id: str, vision_text: str) -> None:
        """单独保存/更新视觉需求 Prompt（显式写入，允许覆盖）。"""
        pdir = self.persona_path(persona_id)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "vision_prompt.md").write_text(vision_text, encoding="utf-8")

    def _save_vision_prompt_noclobber(self, pdir: Path, text: str) -> None:
        """写入 vision_prompt.md，但绝不覆盖已存在的文件（保护用户创作内容）。"""
        vision_path = pdir / "vision_prompt.md"
        if vision_path.exists():
            from clipwright.config import logger
            logger.warning("vision_prompt.md 已存在，跳过写入以保护用户内容: %s", vision_path)
            return
        vision_path.write_text(text, encoding="utf-8")

    def add_knowledge_doc(self, persona_id: str, doc: KnowledgeDoc) -> None:
        """追加一篇知识库文档。"""
        pdir = self.persona_path(persona_id)
        kdir = pdir / "knowledge"
        kdir.mkdir(parents=True, exist_ok=True)

        doc_id = doc.id or f"doc_{len(list(kdir.glob('*.md'))) + 1}"
        fname = f"{doc_id}.md"
        (kdir / fname).write_text(doc.content, encoding="utf-8")

        # 更新索引
        index_path = kdir / "index.yaml"
        index: list[dict] = []
        if index_path.exists():
            with open(index_path, encoding="utf-8") as f:
                index = yaml.safe_load(f) or []
        index.append({
            "id": doc_id,
            "title": doc.title,
            "source": doc.source,
            "file": fname,
            "created_at": doc.created_at,
        })
        with open(index_path, "w", encoding="utf-8") as f:
            yaml.dump(index, f, allow_unicode=True, default_flow_style=False)

        # 自动向量化索引
        self._reindex_knowledge(persona_id)

    # ── 向量化索引 ──

    def _reindex_knowledge(self, persona_id: str) -> None:
        """将 Persona 的 knowledge 重新向量化到 ChromaDB。"""
        try:
            manifest = self.load_manifest(persona_id)
            if not manifest.knowledge:
                return
            from clipwright.rag.retriever import Retriever
            retriever = Retriever()
            retriever.index_persona_knowledge(persona_id, manifest.knowledge)
        except Exception as e:
            from clipwright.config import logger
            logger.warning("知识库向量化失败 (non-fatal): %s", e)

    def delete(self, persona_id: str) -> None:
        import shutil
        pdir = self.persona_path(persona_id)
        if pdir.exists():
            shutil.rmtree(pdir)
