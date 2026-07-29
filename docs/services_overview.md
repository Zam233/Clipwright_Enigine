# 服务层概览

## 概述

服务层（Services Layer）位于 API 路由与 Agent/Tool 之间，提供业务逻辑编排、数据持久化、AI 服务集成和可观测性能力。共约 29 个服务模块。

## 服务分类

### Pipeline 执行

| 服务文件 | 说明 |
|---------|------|
| `pipeline.py` | 管线 v1（固定序列执行） |
| `pipeline_v2.py` | 管线 v2（DAG 并行 + 熔断 + 自愈） |
| `agent_bus.py` | Agent 上下文总线（消息发布/订阅、路由决策） |

### 渲染

| 服务文件 | 说明 |
|---------|------|
| `render.py` | 渲染引擎（多轨/缓存/降级） |

### AI 服务

| 服务文件 | 说明 |
|---------|------|
| `llm.py` | LLM 服务（Anthropic/OpenAI/Ollama 适配） |
| `stt.py` | 语音转文字（Whisper） |
| `voice.py` | 声音克隆与 TTS |

### 数据持久化

| 服务文件 | 说明 |
|---------|------|
| `mongodb_service.py` | MongoDB 连接与操作封装 |
| `project_manager.py` | 项目 CRUD 与文件管理 |

### 领域服务

| 服务文件 | 说明 |
|---------|------|
| `requirements_service.py` | 需求分析与创作方案生成 |
| `style_interpreter_service.py` | Persona 视觉参数 → 动画风格映射 |
| `chat_forge_service.py` | 对话式内容生成 |

### 可观测性

| 服务文件 | 说明 |
|---------|------|
| `trace.py` | 执行追踪（TTL/内存保护） |
| `tracing_service.py` | SpanTracer（MongoDB 持久化全调用树） |
| `log_stream.py` | 实时日志流 |
| `llm_tracker.py` | LLM token 用量追踪 |

### 媒体处理

| 服务文件 | 说明 |
|---------|------|
| `waveform_service.py` | 音频波形可视化数据生成 |
| `vision_service.py` | 视觉分析服务 |
| `edl_service.py` | EDL 导入导出 |
| `fontconfig_service.py` | 字体管理与配置 |

### 工具与服务

| 服务文件 | 说明 |
|---------|------|
| `task_queue.py` | 并发任务队列（信号量+超时） |
| `proxy.py` | 网络代理配置 |
| `async_util.py` | 异步工具函数 |
| `material_preprocessor.py` | 素材预处理（转码/验证） |
| `predictor.py` | 时间预测 |

## 核心交互

```
API 路由 → Service → Agent/Tool
                ↓
          MongoDB / LLM / FFmpeg
```

## 相关文档

- [架构总览](structure.md) — 五层架构中的服务层位置
- [开发指南](development.md) — 新增服务模块
- [API 参考](api_reference.md) — API 端点与服务的对应关系
