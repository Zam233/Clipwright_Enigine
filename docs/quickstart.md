# 帧艺 ClipWright 内容视频编排引擎 — 快速开始

## 环境要求

- Python >= 3.12
- 可选：Anthropic API Key 或 OpenAI API Key（用于 LLM 功能）

## 安装

```bash
# 克隆仓库
git clone <repo-url>
cd clipwright

# 安装依赖
pip install -e ".[dev]"
```

## 配置

通过环境变量或 `.env` 文件配置：

```bash
# .env 文件示例

# LLM 配置（PersonaForge / StructureAgent 需要）
CLIPWRIGHT_LLM_PROVIDER=anthropic
CLIPWRIGHT_LLM_API_KEY=sk-ant-...
CLIPWRIGHT_LLM_MODEL=claude-sonnet-4-6

# 或使用 OpenAI 兼容 API（如 OpenAI、Together AI、vLLM 等）
# CLIPWRIGHT_LLM_PROVIDER=openai
# CLIPWRIGHT_LLM_API_KEY=sk-...
# CLIPWRIGHT_LLM_BASE_URL=https://api.openai.com/v1
# CLIPWRIGHT_LLM_MODEL=gpt-4o

# 或本地 Ollama
# CLIPWRIGHT_LLM_PROVIDER=ollama
# CLIPWRIGHT_LLM_BASE_URL=http://localhost:11434/v1
# CLIPWRIGHT_LLM_MODEL=llama3

# 服务器
CLIPWRIGHT_HOST=0.0.0.0
CLIPWRIGHT_PORT=8000
```

### 模型选择说明

| Provider | 环境变量 | 模型名填什么 | 示例 |
|----------|---------|-------------|------|
| anthropic | `CLIPWRIGHT_LLM_PROVIDER=anthropic` | Anthropic Messages API 模型名 | `claude-sonnet-4-6`, `claude-opus-4-6` |
| openai | `CLIPWRIGHT_LLM_PROVIDER=openai` | OpenAI Chat Completions 模型名 | `gpt-4o`, `gpt-5.4-mini` |
| openai（第三方） | `+CLIPWRIGHT_LLM_BASE_URL=<endpoint>` | 该服务支持的模型名 | `gpt-4o`(Together), `Qwen/Qwen2-72B`(vLLM) |
| ollama | `CLIPWRIGHT_LLM_PROVIDER=ollama` | Ollama 本地模型名 | `llama3`, `qwen2`, `mistral` |

`CLIPWRIGHT_LLM_MODEL` 的值会直接透传给 IsoBase → 底层 SDK 的 `model` 参数，不经过任何映射。你的 API 端点期望什么模型名就填什么。

> **无需 API key 也可运行**：无 key 时所有功能以降级模式工作，使用内置规则和统计方法。

## 启动

```bash
uvicorn clipwright.main:app --reload
```

访问 `http://localhost:8000/docs` 查看交互式 API 文档。

## 快速验证

```bash
# 健康检查
curl http://localhost:8000/health
# → {"status":"ok","service":"clipwright-engine"}

# 列出 Persona
curl http://localhost:8000/api/persona/list
# → ["zam_knowledge_critical"]

# 运行完整管线
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"persona_id":"zam_knowledge_critical","category_plugin_id":"knowledge_longform","topic":"测试选题"}'
```

## 项目结构

```
clipwright/
├── clipwright/
│   ├── main.py            # FastAPI 入口
│   ├── config.py          # 全局配置
│   ├── schema/            # 核心数据模型（Pydantic）
│   ├── persona/           # Persona 系统（加载/验证/存储）
│   ├── category/          # 视频类型插件（4 内置）
│   ├── agents/            # Agent 编排（6 个 Agent）
│   ├── plugins/           # 第三方插件系统
│   ├── services/          # 业务服务（Pipeline/LLM/PersonaForge）
│   ├── tool/              # 原子能力层
│   └── api/               # FastAPI 路由
├── docs/                  # 文档
├── personas/              # Persona YAML 文件
└── tests/                 # 测试
```
