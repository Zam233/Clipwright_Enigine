# 帧艺 ClipWright 内容视频编排引擎 — 快速开始

## 环境要求

- Python >= 3.12
- 可选：Anthropic API Key 或 OpenAI API Key（用于 LLM 功能）
- 可选：FFmpeg（用于视频/音频处理工具，推荐安装）

## 安装

```bash
# 克隆仓库
git clone <repo-url>
cd clipwright

# 安装依赖
pip install -e ".[dev]"
```

## 配置

通过环境变量或 `.env` 文件配置。配置文件查找顺序：`clipwright/.env` → 项目根目录 `.env`。

### LLM 配置（核心）

帧艺支持 **4 种 LLM 后端**，用于 StructureAgent 的脚本骨架生成和 PersonaForge 的智能构建：

```bash
# ── 方式一：Anthropic Claude（推荐） ──
CLIPWRIGHT_LLM_PROVIDER=anthropic
CLIPWRIGHT_LLM_API_KEY=sk-ant-...
CLIPWRIGHT_LLM_MODEL=claude-sonnet-4-6
```

```bash
# ── 方式二：OpenAI ──
CLIPWRIGHT_LLM_PROVIDER=openai
CLIPWRIGHT_LLM_API_KEY=sk-...
CLIPWRIGHT_LLM_MODEL=gpt-4o
```

```bash
# ── 方式三：Ollama（本地部署） ──
CLIPWRIGHT_LLM_PROVIDER=ollama
CLIPWRIGHT_LLM_BASE_URL=http://localhost:11434/v1
CLIPWRIGHT_LLM_MODEL=llama3
```

```bash
# ── 方式四：任何 OpenAI 兼容 API（vLLM / Together / Groq 等） ──
CLIPWRIGHT_LLM_PROVIDER=openai
CLIPWRIGHT_LLM_API_KEY=sk-...
CLIPWRIGHT_LLM_BASE_URL=https://api.together.xyz/v1
CLIPWRIGHT_LLM_MODEL=Qwen/Qwen2-72B-Instruct
```

#### 模型选择说明

| Provider | `CLIPWRIGHT_LLM_PROVIDER` | 模型名填什么 | 示例模型 |
|----------|--------------------------|------------|---------|
| Anthropic | `anthropic` | Anthropic Messages API 模型名 | `claude-sonnet-4-6`, `claude-opus-4-6` |
| OpenAI | `openai` | OpenAI Chat Completions 模型名 | `gpt-4o`, `gpt-5.4-mini` |
| OpenAI 兼容 | `openai` + `CLIPWRIGHT_LLM_BASE_URL` | 该服务支持的模型名 | `Qwen/Qwen2-72B`, `mixtral-8x7b` |
| Ollama | `ollama` | Ollama 本地模型名 | `llama3`, `qwen2`, `mistral` |

> `CLIPWRIGHT_LLM_MODEL` 的值会直接透传给 IsoBase → 底层 SDK 的 `model` 参数，不经过任何映射。你的 API 端点期望什么模型名就填什么。

### 嵌入模型与重排序模型配置（RAG 知识库）

```bash
# ── 嵌入模型 ──
# 可选: sentence_transformer (本地) / openai / ollama
CLIPWRIGHT_RAG_EMBED_PROVIDER=sentence_transformer
CLIPWRIGHT_RAG_EMBED_MODEL=BAAI/bge-small-zh-v1.5
CLIPWRIGHT_RAG_EMBED_DIM=512

# ── 重排序模型 ──
CLIPWRIGHT_RAG_RERANK_MODEL=BAAI/bge-reranker-v2-m3

# ── 检索参数 ──
CLIPWRIGHT_RAG_TOP_K=5
CLIPWRIGHT_RAG_RERANK_TOP_K=20
```

- `sentence_transformer`：本地运行，下载 HuggingFace 模型，无需 API key
- `openai`：使用 `text-embedding-3-small` 等 API 模型，需 `CLIPWRIGHT_RAG_EMBED_API_KEY`
- `ollama`：使用本地 Ollama 嵌入端点，需 `CLIPWRIGHT_RAG_EMBED_BASE_URL`

### 服务器配置

```bash
CLIPWRIGHT_HOST=0.0.0.0
CLIPWRIGHT_PORT=8000
CLIPWRIGHT_DEBUG=false
```

### 免 API Key 运行

> **无需任何 API key 也可运行**：无 key 时所有 LLM 功能以降级模式工作，使用内置规则和统计方法。RAG 嵌入模型使用本地的 `sentence-transformers`。视频处理工具（trim / concat / scene_detect 等）只需 FFmpeg。

## 启动

```bash
uvicorn clipwright.main:app --reload
```

访问 `http://localhost:8000` 查看测试前端，或访问 `http://localhost:8000/docs` 查看交互式 API 文档。

## 快速验证

```bash
# 健康检查
curl http://localhost:8000/health
# → {"status":"ok","service":"clipwright-engine"}

# 查看系统能力概览
curl http://localhost:8000/api/plugin/capabilities
# → {"tools":[...],"skills":[...],"material_sources":[...],"plugins":[...]}

# 列出 Persona
curl http://localhost:8000/api/persona/list
# → ["zam_knowledge_critical"]

# 列出所有工具
curl http://localhost:8000/api/tool/list

# 列出所有动画定义
curl http://localhost:8000/api/animation/list

# 运行完整管线
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"persona_id":"zam_knowledge_critical","category_plugin_id":"knowledge_longform","topic":"测试选题"}'
```

## 核心功能一览

| 功能 | 说明 | 快速入口 |
|------|------|---------|
| **6-Agent 管线** | Structure → Material → Edit → Animation → Audio → Quality | `POST /api/pipeline/run` |
| **原子能力工具** | 10 个 FFmpeg 工具（trim/concat/overlay/scene_detect/BPM 等） | `POST /api/tool/execute?name=scene_detect` |
| **可组合技能** | 编排多个工具的高级能力 | `POST /api/skill/execute?name=analyze_video_structure` |
| **素材库** | 跨 JSON 目录 / URL / RAG 知识库的统一搜索 | `POST /api/material/search?query=xxx` |
| **动画系统** | 基于 JSON 规范的屏幕动画 + 文字动画 + 转场动画 (27 个) | `GET /api/animation/list` |
| **Persona** | 四层复合数字人格（参数/示例/嵌入/模型层） | `GET /api/persona/list` |
| **PersonaForge** | 自然语言 / 脚本分析 / 对话引导创建 Persona | `POST /api/persona/forge/from-prompt` |
| **插件系统** | 第三方插件动态加载（注册 Tool / Skill / MaterialSource） | `POST /api/plugin/load/{plugin_id}` |
| **语音转文字** | Whisper 转录 + 文案对齐 | `POST /api/stt/align` |

## 项目结构

```
clipwright/
├── clipwright/
│   ├── main.py            # FastAPI 入口
│   ├── config.py          # 全局配置 + 日志
│   ├── schema/            # 核心数据模型（Pydantic）
│   ├── persona/           # Persona 系统（加载/验证/存储）
│   ├── category/          # 视频类型插件（4 内置）
│   ├── agents/            # Agent 编排（6 个 Agent）
│   ├── plugins/           # 第三方插件系统
│   ├── services/          # 业务服务（Pipeline/LLM/PersonaForge/STT）
│   ├── tool/              # 原子能力层（10 个工具）
│   ├── skill/             # 技能系统（3 内置 + 插件扩展）
│   ├── material/          # 素材库系统（JSON/URL/RAG）
│   ├── animation/         # 动画系统（onscreen/text/transition）
│   ├── rag/               # RAG 检索（分块/嵌入/向量库/重排序）
│   ├── utils/             # 共享工具函数
│   └── api/               # FastAPI 路由（12 个模块）
├── docs/                  # 文档
├── personas/              # Persona YAML 文件
├── plugins/               # 第三方插件安装目录
└── tests/                 # 测试
```
