# RAG 向量检索与重排序

为 Persona 知识库提供语义检索能力，支持向量搜索 + Cross-Encoder 重排序双阶段检索。

## 架构

```
知识库文档
  │ chunker.py  按 Markdown 标题 + 句子分割
  ▼
文档块 (Chunk)
  │ embedder.py  sentence-transformers / OpenAI
  ▼
向量索引 (ChromaDB)
  │
  ▼
用户查询
  │ embedder.py  查询嵌入
  ▼
向量检索 (Top-20)
  │ reranker.py  Cross-Encoder 重打分
  ▼
重排序 (Top-5)
  │ retriever.py  组装上下文
  ▼
注入 LLM Prompt
```

## 配置

通过 `.env` 文件或环境变量配置：

```bash
# ── 嵌入模型 ──
# 可选: sentence_transformer (本地) / openai / ollama
CLIPWRIGHT_RAG_EMBED_PROVIDER=sentence_transformer
# 模型名:
#   sentence_transformer → HuggingFace 模型名 (BAAI/bge-small-zh-v1.5)
#   openai              → OpenAI 模型名 (text-embedding-3-small)
#   ollama              → Ollama 模型名 (nomic-embed-text)
CLIPWRIGHT_RAG_EMBED_MODEL=BAAI/bge-small-zh-v1.5
CLIPWRIGHT_RAG_EMBED_DIM=512
# 嵌入模型的独立 API 地址（openai/ollama 模式）：
CLIPWRIGHT_RAG_EMBED_API_KEY=
CLIPWRIGHT_RAG_EMBED_BASE_URL=
# 示例：Ollama 嵌入
# CLIPWRIGHT_RAG_EMBED_PROVIDER=ollama
# CLIPWRIGHT_RAG_EMBED_MODEL=nomic-embed-text
# CLIPWRIGHT_RAG_EMBED_BASE_URL=http://localhost:11434/v1

# ── 重排序模型 ──
# 模型名（本地 CrossEncoder 或兼容 API 模型名）
CLIPWRIGHT_RAG_RERANK_MODEL=BAAI/bge-reranker-v2-m3
# 重排序的独立 API 地址（可选，用于 API 模式的 rerank 服务）
CLIPWRIGHT_RAG_RERANK_BASE_URL=
CLIPWRIGHT_RAG_RERANK_API_KEY=

# ── 检索参数 ──
CLIPWRIGHT_RAG_TOP_K=5              # 最终返回数
CLIPWRIGHT_RAG_RERANK_TOP_K=20      # 重排序候选数

# ── 分块参数 ──
CLIPWRIGHT_RAG_CHUNK_SIZE=512       # 每块最大字符数
CLIPWRIGHT_RAG_CHUNK_OVERLAP=64     # 块间重叠字符数
```

### Provider 说明

| Provider | 嵌入位置 | 典型模型 | 需另配 base_url? |
|----------|---------|---------|-----------------|
| `sentence_transformer` | 本地 | BAAI/bge-small-zh-v1.5 | 可选（HF 镜像） |
| `openai` | API | text-embedding-3-small | 可选 |
| `ollama` | 本地 API | nomic-embed-text | 需要 `http://localhost:11434/v1` |

嵌入和重排序的 base_url 彼此独立，也独立于 `CLIPWRIGHT_LLM_BASE_URL`。

## API

### 建立索引

```bash
curl -X POST /api/persona/{id}/rag/index \
  -H "Content-Type: application/json" \
  -d '{"force_rebuild": true}'
```

### 语义检索

```bash
curl -X POST /api/persona/{id}/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query": "冷峻风格怎么体现", "top_k": 5, "rerank": true}'
```

### 检查索引状态

```bash
curl /api/persona/{id}/rag/status
```

### 删除索引

```bash
curl -X DELETE /api/persona/{id}/rag/index
```

## 工作原理

### 分块策略

1. 按 Markdown H1/H2 标题分割，保持语义完整性
2. 长段落按句号/换行二次切分
3. 短段落向前合并（不超过 chunk_size）
4. 块间重叠 chunk_overlap 个字符

### 双阶段检索

```
阶段 1 — 向量检索（ANN）：
  查询 → SentenceTransformer 嵌入 → ChromaDB cosine 相似度 → Top-20

阶段 2 — 重排序（Cross-Encoder）：
  (query, passage) 对 → BGE-Reranker 逐对打分 → 按分重排 → Top-5
```

向量检索速度快但精度有限，重排序精度高但计算量大。双阶段结合兼得速度与精度。

### 存储位置

```
personas/{id}/knowledge/
├── .chroma/              # ChromaDB 向量库（自动管理）
├── index.yaml            # 文档索引
└── doc_*.md              # 文档正文
```
