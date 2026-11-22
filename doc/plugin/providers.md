# 模型与 Provider 插件

Provider 插件把“厂商差异”关在插件里：核心只持有协议，重试/退避/超时/工具循环/计量仍归引擎。
协议定义见 `clipwright/providers/base.py`；厂商实现示例：

```
plugins/llm_openai_compatible/   plugins/llm_anthropic/
plugins/tts_qwen/                plugins/tts_minimax/       plugins/tts_cosyvoice/
plugins/stt_providers/           plugins/whisper_stt/
plugins/image_generate/         plugins/image_gpt_images/
plugins/embedding_providers/     plugins/rerank_providers/
plugins/text_motion_core/        plugins/text_motion_llm/
```

## 1. 可贡献的 Provider kind

| kind | 协议 | 关键方法 |
|---|---|---|
| `llm_provider` | `LLMProvider` | `capabilities(model)`、`build_client(...)`、`default_model()` |
| `tts_provider` | `TTSProvider` | `clone(...)`、`synthesize(...)`、`default_model()` |
| `stt_provider` | `STTProvider` | `transcribe`、`align`、`is_available` |
| `image_provider` | 图像生成 | `default_model()`、`capabilities`、`generate(...)`（可选 `probe`） |
| `embedding_provider` | `EmbeddingProvider` | `create_embedder(...)`、`default_model()` |
| `rerank_provider` | `RerankProvider` | `create_reranker(...)`、`default_model()` |
| `renderer_backend` | `RendererBackend` | `capabilities()`、`probe()`、`render(...)` |
| `text_motion` | `TextMotionProvider` | 见 `clipwright/animation/text_motion/` |

Provider **只做传输与格式转换**；不要在插件里写全局重试、JSON 修复、工具循环，这些在核心
（`services/llm.py`、`services/voice.py`）里，重复实现会与核心策略打架。

## 2. LLM 能力协商

`capabilities(model)` 返回 `ProviderCapabilities`（`clipwright/providers/base.py`）：

```python
ProviderCapabilities(
    streaming=True, tool_calls=True, json_mode=True, vision=False,
    max_context_tokens=128000, arbitrary_models=False,
    reasoning_effort=True, reasoning_content=True, reasoning_streaming=True,
    generation_params=("temperature", "top_p", "max_tokens"),
)
```

- 不支持的能力要**如实声明**：核心会报可读错误/跳过，不会假装支持；
- `arbitrary_models=True` 只给“没有固定模型清单”的网关/本地 provider；
- 显式路由校验（`provider/model`）会据此决定放行还是拒绝。

## 3. 贡献声明与配置

```yaml
contributions:
  - kind: llm_provider
    id: my-llm
    config_schema:
      base_url: {type: string, label: "Base URL", default: "https://api.example.com/v1"}
      api_key: {type: string, label: "API Key", secret: true}
      model: {type: string, label: "默认模型", default: "my-model"}
    capabilities:
      models: ["my-model"]
      stream: true
      tools: true
```

`config_schema` 由前端 `ProvidersPage` 渲染；`secret: true` 走服务端加密/掩码。未知控件或
未知能力键必须显式提示，不能静默丢弃。

## 4. 渲染后端

```python
class MyProducerBackend:
    name = "producer"

    def capabilities(self) -> dict:
        return {"name": self.name, "supports_gpu": True, "sidecar": "..."}

    async def probe(self) -> bool:
        return True

    async def render(self, timeline, output_path, settings, on_progress=None, cancel_token=None):
        # settings: width/height/fps/bitrate/audio_* 等已收口
        return {"success": True, "output_path": output_path, "duration_sec": 12.3, "warnings": []}
```

管理员把 `renderer_backend` 设成该 id；`GET /api/render/backends` 列出所有后端与可用性，
探测失败时渲染入口自动回退本机并记录原因。`cancel_token` 是协议一等公民，必须响应取消。

## 5. 常见错误

- 插件加载了但设置页没有该 Provider：检查贡献声明与 `api_version: "2"`；
- 选了模型却报“不支持”：`capabilities` 声明少了对应能力，或 `models` 清单未包含；
- 密钥显示明文/被覆盖：确认字段 `secret: true`，并且回写时用掩码而不是新值；
- TTS 上传/克隆失败：先看 `/api/plugin/errors`，再看 provider 的 `needs_uploader` 区分。
