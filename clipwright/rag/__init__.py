"""RAG 模块 — 向量检索 + 重排序。

为 Persona 知识库提供语义检索能力：
- chunker: 文档分块
- embedder: 嵌入模型（多 Provider）
- vector_store: 向量数据库（ChromaDB）
- reranker: 重排序（Cross-Encoder）
- retriever: 检索管线（整合以上）
"""
