"""文档分块器 — 将知识库文档分割为可检索的块。

策略：
1. 优先按 Markdown 标题（H1/H2）分割，保持语义完整性
2. 长段落按句号/换行二次切分
3. 短段落向前合并（不超过 chunk_size）
4. 块间重叠 chunk_overlap 个字符
"""

from __future__ import annotations

import re
from typing import Any

from clipwright.config import settings


class Chunk:
    """单个文档块。"""
    id: str
    text: str
    metadata: dict[str, Any]

    def __init__(self, id: str, text: str, **metadata: Any) -> None:
        self.id = id
        self.text = text
        self.metadata = metadata

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "metadata": self.metadata}


def chunk_document(
    content: str,
    source: str = "",
    persona_id: str = "",
    doc_id: str = "",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Chunk]:
    """将一篇文档分割为 Chunk 列表。

    Args:
        content: 文档内容
        source: 来源文件名
        persona_id: 所属 Persona
        doc_id: 文档 ID
        chunk_size: 最大块字符数
        chunk_overlap: 块间重叠字符数
    """
    chunk_size = chunk_size or settings.rag_chunk_size
    chunk_overlap = chunk_overlap or settings.rag_chunk_overlap
    chunks: list[Chunk] = []

    # 1. 按 Markdown 标题分节
    sections = _split_by_headings(content)

    for sec_index, section in enumerate(sections):
        text = section.strip()
        if not text:
            continue

        # 2. 如果单节不超长，直接作为一块
        if len(text) <= chunk_size:
            chunk_id = f"{doc_id}_s{sec_index}" if doc_id else f"sec_{sec_index}"
            chunks.append(Chunk(
                id=chunk_id,
                text=text,
                source=source,
                persona_id=persona_id,
                section_index=sec_index,
            ))
            continue

        # 3. 长节按句子切分
        sentences = _split_sentences(text)
        current = ""
        for sent in sentences:
            if len(current) + len(sent) > chunk_size and current:
                chunk_id = f"{doc_id}_s{sec_index}_c{len(chunks)}" if doc_id else f"sec_{sec_index}_c{len(chunks)}"
                chunks.append(Chunk(
                    id=chunk_id,
                    text=current.strip(),
                    source=source,
                    persona_id=persona_id,
                    section_index=sec_index,
                ))
                # 保留尾部 chunk_overlap 字符用于重叠
                overlap = current[-chunk_overlap:] if len(current) > chunk_overlap else ""
                current = overlap + sent
            else:
                current += sent

        if current.strip():
            chunk_id = f"{doc_id}_s{sec_index}_c{len(chunks)}" if doc_id else f"sec_{sec_index}_c{len(chunks)}"
            chunks.append(Chunk(
                id=chunk_id,
                text=current.strip(),
                source=source,
                persona_id=persona_id,
                section_index=sec_index,
            ))

    return chunks


def _split_by_headings(text: str) -> list[str]:
    """按 Markdown H1/H2 分割文本。"""
    sections = re.split(r"^#(#?) ", text, flags=re.MULTILINE)
    result: list[str] = []
    for i, s in enumerate(sections):
        if not s.strip():
            continue
        if i == 0:
            result.append(s)
        else:
            # s 的第一个换行前的内容是标题文字
            lines = s.split("\n", 1)
            heading = lines[0].strip()
            body = lines[1] if len(lines) > 1 else ""
            # 重构标题标记
            prefix = "## " if s.startswith("#") else "# "  # 实际 pattern 消耗掉了 #
            result.append(f"{prefix}{heading}\n{body}")
    return result


def _split_sentences(text: str) -> list[str]:
    """按句号/问号/感叹号/换行分割句子，保留分隔符。"""
    # 先按换行分割
    paragraphs = text.split("\n")
    sentences: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            sentences.append("\n")
            continue
        # 按中文/英文标点分割
        parts = re.split(r"(?<=[。！？.!?\n])\s*", para)
        for p in parts:
            if p.strip():
                sentences.append(p)
    return sentences
