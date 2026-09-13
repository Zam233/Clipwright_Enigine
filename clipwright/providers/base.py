"""Provider 协议 —— 模型 / 语音 / 渲染的可插拔契约（方案 B / M1 契约冻结）。

设计原则（依据 `docs/pluginization-plan-2026-09-10.md` §5 B-0 与 §13.3）：
- Provider **只做传输与格式转换**；重试、退避、超时、JSON 解析、工具循环、
  计量**留在核心**（`services/llm.py` / `services/voice.py` 的策略层）。
- 协议小、脏参数进不来；能力经 `capabilities()` 显式协商，不支持时核心
  **报可读错误**而非静默降级（B-4）。
- `cancel_token` 是渲染协议的一等公民（审计 P1-19 的协议级落点）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderCapabilities:
    """能力协商：核心据此决定可用策略，而不是静默降级。"""
    streaming: bool = False
    tool_calls: bool = False
    json_mode: bool = False
    vision: bool = False
    max_context_tokens: int = 0
    max_concurrency: int = 0


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 提供者 —— 负责「构建客户端 + 声明能力」，不含重试/解析策略。

    ``build_client`` 的返回对象需具备现有 IsoBase 客户端的调用形状
    （``.generate(...)`` 等，由 ``services/llm.py`` 的策略层消费）。
    """

    name: str

    def capabilities(self, model: str) -> ProviderCapabilities: ...

    def build_client(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        model: str,
        instructions: str,
        max_tokens: int,
        flash: bool,
        timeout_sec: float = 0.0,
        proxy: str = "",
        headers: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def default_model(self) -> str: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量化提供者（B4）—— 工厂形态：按源凭据产出 `BaseEmbedder` 同形对象。

    产出对象需具备 `embed(texts)` / `embed_query(text)` / `dim`（属性或方法）：
    向量库按维度建集合，换模型必须能读到维度。
    """

    name: str

    def create_embedder(
        self, *, model: str, api_key: str | None, base_url: str | None,
        timeout_sec: float = 0.0, proxy: str = "", headers: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def default_model(self) -> str: ...


@runtime_checkable
class RerankProvider(Protocol):
    """重排序提供者（B4）—— 工厂形态；产出对象供 `rag/reranker.py` 调用。

    产出对象的 `rerank(query, documents, model, top_n)` 返回按相关度降序的
    `[(原文索引, 分数)]`；兼容各家响应格式的解析留在 core。
    """

    name: str

    def create_reranker(
        self, *, model: str, api_key: str | None, base_url: str | None,
        timeout_sec: float = 0.0, proxy: str = "", headers: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def default_model(self) -> str: ...


@runtime_checkable
class TTSProvider(Protocol):
    """TTS 提供者 —— 与 ``services/voice.py`` 的 BaseVoiceProvider 同形。"""

    name: str

    async def clone(
        self, *, audio_ref: str, voice_name: str,
        target_model: str = "", audition_text: str = "",
    ) -> tuple[str, str]: ...

    async def synthesize(
        self, *, voice_api_id: str, model: str, text: str, **kwargs: Any,
    ) -> tuple[bytes, list[dict]]: ...

    def default_model(self) -> str: ...


@runtime_checkable
class STTProvider(Protocol):
    """STT 提供者 —— 转写与强制对齐（whisper / faster-whisper / 云 STT）。"""

    name: str

    async def transcribe(self, audio_path: str, **kwargs: Any) -> Any: ...

    async def align(self, audio_path: str, text: str) -> Any: ...

    def is_available(self) -> bool: ...


@runtime_checkable
class RendererBackend(Protocol):
    """渲染后端 —— 本机 ffmpeg 与远程渲染各是一个实现（B-3）。

    ``cancel_token`` 是协议的一部分：取消语义必须在此统一（审计 P1-19）。
    """

    name: str

    def capabilities(self) -> dict[str, Any]:
        """编码器 / pix_fmt / 最大时长 / 是否支持 GPU 等。"""
        ...

    async def probe(self) -> bool:
        """后端是否可用。"""
        ...

    async def render(
        self,
        timeline: Any,
        output_path: str,
        settings: dict[str, Any],
        on_progress: Callable[[float, str], None] | None = None,
        cancel_token: Any | None = None,
    ) -> Any: ...
