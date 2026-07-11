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

### 视觉识别模型配置（识图 + 自动标签）

```bash
# ── 方式一：LLM 多模态（推荐，精度最高） ──
CLIPWRIGHT_VISION_PROVIDER=llm
# 复用已配置的 LLM（支持 Qwen-VL / Claude 3 Vision / GPT-4V 等）
# CLIPWRIGHT_LLM_PROVIDER=openai
# CLIPWRIGHT_LLM_MODEL=qwen-vl-plus
# CLIPWRIGHT_LLM_API_KEY=sk-...
```

```bash
# ── 方式二：transformers 专用模型 ──
CLIPWRIGHT_VISION_PROVIDER=transformers
CLIPWRIGHT_VISION_MODEL=google/vit-base-patch16-224  # HuggingFace 模型名
CLIPWRIGHT_VISION_TOP_K=5                   # 返回 Top-K 分类结果
CLIPWRIGHT_VISION_DEVICE=cpu                # cpu / cuda
```

```bash
# ── 方式三：无模型（仅文件名分析） ──
CLIPWRIGHT_VISION_PROVIDER=none
```

### Provider 说明

| Provider | 依赖 | 精度 | 适合场景 |
|----------|------|------|---------|
| `llm` | 配置好的 LLM API Key + 支持多模态的模型 | ★★★★★ | Qwen-VL / Claude 3 Sonnet / GPT-4o 等 |
| `transformers` | `pip install transformers torch` | ★★★☆ | 本地运行，无需 API Key |
| `none` | 无 | ★★ | 快速测试，仅文件名 |

**LLM 多模态推荐模型**：
- Qwen-VL-Plus / Qwen-VL-Max（阿里，中文最佳）
- Claude 3.5 Sonnet / Claude 3 Opus（Anthropic）
- GPT-4o / GPT-4o-mini（OpenAI）
- Llama 3.2 Vision（Ollama 本地）

### 语音转文字（STT）配置

ClipWright 的语音转文字服务支持三种后端，自动探测可用性：

#### 方式一：OpenAI Whisper（推荐，精度最高）

```bash
# 安装
pip install openai-whisper

# 首次调用时自动下载模型，无需额外配置
# 模型大小可选：tiny / base / small / medium / large
# 通过 API 参数控制：
#   POST /api/stt/transcribe
#   { "audio_path": "/path/to/audio.wav", "model_size": "base" }
```

- 优势：本地运行，数据不出机器，精度高
- 模型大小：`tiny`(39M) / `base`(74M) / `small`(244M) / `medium`(769M) / `large`(1550M)
- 首次使用会从 HuggingFace 下载模型，之后缓存到 `~/.cache/whisper/`

#### 方式二：faster-whisper（速度更快，内存更少）

```bash
pip install faster-whisper
```

- 比原版 Whisper 快 4x，内存占用低 50%
- 与 Whisper 使用相同的 API 参数，自动切换
- 同样本地运行，不需要 API Key

#### 方式三：文案对齐模式（无需 AI，零依赖）

```bash
# 不需要安装任何额外包
# 通过 POST /api/stt/align 使用
# 传入已有文案 + 音频路径，按句子比例分配时间戳
```

- 不依赖 Whisper 模型，适用于已有完整配音稿的场景
- 精度不如 AI 转录，但胜在零成本零依赖

#### 三种方式的关系

```
STT 调用
  ↓
Whisper 可用？ → 是 → OpenAI Whisper 转录
  ↓ 否
faster-whisper 可用？ → 是 → faster-whisper 转录
  ↓ 否
已提供文案？ → 是 → 文案对齐（时长分配）
  ↓ 否
返回提示：请安装 Whisper 或提供文案
```

### 服务器配置

```bash
CLIPWRIGHT_HOST=0.0.0.0
CLIPWRIGHT_PORT=8000
CLIPWRIGHT_DEBUG=false
```

## 素材库接入

ClipWright 的素材库通过 `MaterialRegistry` 统一管理，Pipeline 运行时 MaterialAgent 跨所有已注册的素材源搜索。支持 4 种接入方式：

### 方式一：JSON 目录文件（最快上手）

创建 `materials/my_catalog.json`：

```json
[
  {
    "id": "bg_city",
    "title": "城市夜景",
    "type": "video",
    "local_path": "/absolute/path/to/city_night.mp4",
    "url": "https://example.com/videos/city.mp4",
    "tags": ["城市", "夜景", "b-roll"],
    "duration_sec": 30,
    "resolution": "1920x1080"
  },
  {
    "id": "music_bg",
    "title": "背景音乐",
    "type": "audio",
    "local_path": "/audio/bgm.mp3",
    "tags": ["音乐", "背景"],
    "duration_sec": 120
  }
]
```

在 `clipwright/main.py` 的 lifespan 中添加注册：

```python
from clipwright.material import JsonCatalogSource, MaterialRegistry

json_source = JsonCatalogSource(
    source_id="my_catalog",
    catalog_path="materials/my_catalog.json",
    source_name="我的素材目录",
)
MaterialRegistry.register(json_source)
```

重启服务后，MaterialAgent 就能搜到这个源了。

**大规模素材目录（100+ 条）**：默认使用关键词匹配标题/标签。当素材量大时，调用 `build_index()` 启用向量检索：

```python
# 在注册之后调用一次（首次会下载嵌入模型）
await json_source.build_index()

# 之后 search() 自动使用嵌入模型 + ChromaDB + 可选的 Cross-Encoder 重排序
# 代替关键词匹配
```

向量检索流程：

```
素材 JSON（如 10000 条）
  ↓ build_index()
素材文本 → sentence-transformer → ChromaDB 向量索引
  ↓ search(query)
① 向量检索（余弦相似度，Top-20）
② Cross-Encoder 重排序（Top-5）
③ 返回 MaterialAsset + 分数
```

索引持久化在 `.chroma_db/` 目录，重启服务后无需重建。配置嵌入模型和重排序模型的参数见上方"嵌入模型与重排序模型配置"一节。

### 方式二：URL 素材源（远程文件）

适用于素材存储在 CDN/NAS 的场景：

```python
from clipwright.material import UrlMaterialSource, MaterialRegistry

url_source = UrlMaterialSource(
    source_id="cdn_lib",
    base_url="https://cdn.example.com/materials",
    source_name="CDN 素材",
)
url_source.add_url("videos/ocean.mp4", "海洋", tags=["nature"])
url_source.add_url("videos/timelapse.mp4", "延时摄影", tags=["city"])
MaterialRegistry.register(url_source)
```

### 方式三：RAG 知识库（文本素材）

将 Persona 的文档作为素材源：

```python
from clipwright.material import RagKnowledgeSource, MaterialRegistry

rag_source = RagKnowledgeSource(
    source_id="persona_knowledge",
    persona_id="zam_knowledge_critical",
    source_name="知识库",
)
# 建索引（如未建）
await rag_source.index_persona("zam_knowledge_critical")
MaterialRegistry.register(rag_source)
```

### 方式四：作为第三方插件（自动加载）

创建 `plugins/my_library/plugin.yaml`：

```yaml
id: "my_library"
name: "我的素材库"
version: "1.0.0"
kind: "capability"
entry_point: "my_library.main"
```

创建 `plugins/my_library/main.py`：

```python
from clipwright.material import JsonCatalogSource, MaterialRegistry
from clipwright.plugins import CapabilityPlugin
from clipwright.schema.plugin import PluginManifest, PluginKind

class MyLibraryPlugin(CapabilityPlugin):
    manifest = PluginManifest(id="my_library", name="我的素材库",
        version="1.0.0", kind=PluginKind.CAPABILITY)
    def initialize(self) -> None:
        src = JsonCatalogSource("my_lib", "plugins/my_library/catalog.json")
        MaterialRegistry.register(src, plugin_id=self.manifest.id)
    def shutdown(self) -> None: pass

__all__ = ["MyLibraryPlugin"]
```

重启后插件自动加载：`[Clipwright] Loaded 1 third-party plugins: my_library`

### 验证素材库是否生效

```bash
# 列出已注册的素材源
curl http://localhost:8000/api/material/sources

# 搜索素材
curl -X POST 'http://localhost:8000/api/material/search?query=city&top_k=5'

# 运行 Pipeline（MaterialAgent 会自动搜素材）
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"persona_id":"zam_knowledge_critical","category_plugin_id":"knowledge_longform","topic":"测试"}'
```

---

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
