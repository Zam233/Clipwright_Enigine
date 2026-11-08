"""声音克隆与语音合成（TTS 推理）服务。

能力（仿 STTService 范式，分三段构建）：
1. 数据层：VoiceRecord / VoiceResult / VoiceStorage(JSON) / 音频转码助手
2. Provider 层：Qwen-TTS / CosyVoice / MiniMax 三模型抽象 + 可配置公网上传
3. 协议调度：VoiceService —— clone / synthesize
   （切句与逐段配音编排已按裁定 3 迁至 `plugins/tts_feature/`，engine 不再有 `dub_script`）

所有云调用走阿里云百炼（DashScope）；CosyVoice 合成走 dashscope SDK WebSocket。
克隆音色元数据存 JSON 文件（默认 PluginData/voices/voices.json）。
"""

from __future__ import annotations

import abc
import base64
import hashlib
import json
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from clipwright.config import settings, logger

# DashScope 全局 api_key 串行化锁（SDK 不支持请求级凭据）
_DASHSCOPE_KEY_LOCK = threading.Lock()

# ──────────────────────────────────────────────
# 数据层：结果对象 / 音色记录 / JSON 存储
# ──────────────────────────────────────────────


class VoiceRecord(BaseModel):
    """克隆音色的元数据记录（持久化到 voices.json）。"""

    id: str = Field(description="本地短 ID（uuid hex[:12]）")
    provider: str = Field(description="qwen-tts | cosyvoice | minimax")
    voice_id: str = Field(description="云端返回的音色 API ID")
    voice_name: str = Field(description="音色名称")
    target_model: str = Field(description="绑定的目标模型")
    created_at: str = Field(description="创建时间 ISO 字符串")


class VoiceResult:
    """声音服务的统一结果对象（仿 STTResult）。"""

    def __init__(
        self,
        success: bool = True,
        data: Optional[dict[str, Any]] = None,
        error: str = "",
    ) -> None:
        self.success = success
        self.data = data or {}
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "data": self.data, "error": self.error}


class VoiceStorage:
    """音色元数据的 JSON 文件存储（仿参考工程 voices.json）。

    只接受显式 db_path，导入期不读 settings，便于测试与并行构建。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        # Serialize read-modify-write ops so concurrent clone/delete don't lose updates
        self._lock = threading.Lock()

    def load(self) -> list[dict]:
        """读取全部音色记录；文件不存在或 JSON 损坏时返回 []。"""
        if self.db_path.exists():
            try:
                data = json.loads(self.db_path.read_text("utf-8"))
                return data if isinstance(data, list) else []
            except Exception:
                return []
        return []

    def save(self, voices: list[dict]) -> None:
        """原子写入全部音色记录（临时文件 + rename 防崩溃丢失数据）。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.db_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(voices, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.db_path)

    def add(self, record: dict) -> None:
        with self._lock:
            voices = self.load()
            voices.append(record)
            self.save(voices)

    def get(self, db_id: str) -> Optional[dict]:
        for v in self.load():
            if v.get("id") == db_id:
                return v
        return None

    def delete(self, db_id: str) -> bool:
        with self._lock:
            voices = self.load()
            new_voices = [v for v in voices if v.get("id") != db_id]
            if len(new_voices) == len(voices):
                return False
            self.save(new_voices)
            return True


# ──────────────────────────────────────────────
# 数据层：音频助手
# ──────────────────────────────────────────────


def _guess_mime(suffix: str) -> str:
    """按扩展名推断音频 MIME 类型。"""
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(suffix.lower(), "audio/wav")


def _convert_to_wav_16k_mono(src: Path) -> Path:
    """用 ffmpeg 将音频转为 16kHz 单声道 WAV PCM（CosyVoice 友好）。

    失败（如未装 ffmpeg）时回退原文件。
    """
    src = Path(src)
    dst = src.with_stem(src.stem + "_converted").with_suffix(".wav")
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(dst),
    ]
    logger.info("转码 %s → %s (16kHz mono WAV)", src.name, dst.name)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("音频转码失败（ffmpeg 未安装？）: %s", result.stderr[:200])
        return src
    return dst


def _get_duration(path: str) -> float:
    """用 ffprobe 获取音频时长（秒），失败返回 0。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception as e:
        logger.debug("获取音频时长失败: %s", e)
        return 0


def _sanitize_name(name: str, fallback: str, allow_extra: str = "_-") -> str:
    """Keep only letters, digits and optional extra chars (ref: main.py _sanitize_name)."""
    pattern = f"[^a-zA-Z0-9{re.escape(allow_extra)}]"
    sanitized = re.sub(pattern, "", name.strip())
    return sanitized[:64] if sanitized else fallback


def _is_local_url(url: str) -> bool:
    """Check if a URL points to localhost / 127.0.0.1."""
    return url.lower().startswith((
        "http://localhost", "http://127.0.0.1", "http://0.0.0.0",
        "https://localhost", "https://127.0.0.1",
    ))


# 允许本地上传的媒体扩展名（大小写不敏感）
_MEDIA_FILE_EXTENSIONS = {".wav", ".mp3", ".m4a", ".mp4"}


def _is_absolute_http_url(s: str) -> bool:
    """Check if a string is an absolute http(s) URL (vs a bare filesystem path)."""
    return s.lower().startswith(("http://", "https://"))


def _resolve_local_file(local_path: str) -> Path:
    """Resolve a localhost URL path or bare filesystem path relative to project root."""
    return Path(__file__).resolve().parent.parent.parent / local_path.lstrip("/")


# ──────────────────────────────────────────────
# Provider 层：公网上传
# ──────────────────────────────────────────────


class PublicUploadManager:
    """可配置公网上传服务（uguu.se / catbox），环境变量覆盖默认列表。"""

    _SERVICE_MAP: dict[str, dict[str, Any]] = {
        "uguu": {
            "url": "https://uguu.se/upload",
            "field": "files[]",
            "response": "json",
            "url_path": ["files", 0, "url"],
        },
        "catbox": {
            "url": "https://litterbox.catbox.moe/resources/internals/api.php",
            "field": "fileToUpload",
            "data": {"reqtype": "fileupload", "time": "1h"},
            "response": "text",
        },
    }
    DEFAULT_SERVICES: list[dict[str, Any]] = [
        _SERVICE_MAP["uguu"],
        _SERVICE_MAP["catbox"],
    ]

    def __init__(self) -> None:
        raw = settings.tts_public_upload_services
        names = [n.strip().lower() for n in raw.split(",") if n.strip()] if raw else []
        self._services: list[dict] = (
            [self._SERVICE_MAP[n] for n in names if n in self._SERVICE_MAP]
            if names
            else self.DEFAULT_SERVICES
        )

    async def upload(self, file_path: Path) -> str:
        """Convert audio to 16kHz mono WAV, then upload; returns public URL."""
        converted = _convert_to_wav_16k_mono(file_path)
        raw_bytes = converted.read_bytes()
        for svc in self._services:
            logger.info("公网上传 %s → %s ...", converted.name, svc["url"])
            try:
                files = {svc["field"]: (converted.name, raw_bytes)}
                data = svc.get("data")
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(svc["url"], files=files, data=data)
                if resp.status_code not in (200, 201):
                    logger.warning("  %s returned %s", svc["url"], resp.status_code)
                    continue
                body_text = resp.text.strip()
                if svc.get("response") == "json":
                    obj = resp.json()
                    for key in svc.get("url_path", []):
                        if isinstance(obj, dict):
                            obj = obj.get(key, "")
                        elif isinstance(obj, list) and isinstance(key, int) and key < len(obj):
                            obj = obj[key]
                        else:
                            obj = ""
                        if not obj:
                            break
                    else:
                        if obj:
                            logger.info("  ✓ %s", obj)
                            return obj
                elif body_text:
                    logger.info("  ✓ %s", body_text)
                    return body_text
            except Exception as e:
                logger.warning("  %s error: %s", svc["url"], e)
                continue
        raise RuntimeError("所有公网上传服务均不可用，请手动提供公网音频 URL。")

    async def maybe_upload(self, url: str) -> str:
        """If URL is local, upload to public; otherwise return as-is.

        本地输入（localhost URL 或裸文件路径）先校验媒体扩展名与文件存在性，
        非法路径报错不静默；公网 http(s) URL 原样透传。
        """
        if _is_absolute_http_url(url) and not _is_local_url(url):
            return url
        local_path = urlparse(url).path if _is_absolute_http_url(url) else url
        if Path(local_path).suffix.lower() not in _MEDIA_FILE_EXTENSIONS:
            raise ValueError(
                f"非法素材路径: {url!r} 不是音频文件（仅支持 .wav/.mp3/.m4a/.mp4）"
            )
        file_path = _resolve_local_file(local_path)
        if not file_path.exists():
            raise FileNotFoundError(f"素材文件不存在: {file_path}")
        return await self.upload(file_path)


# ──────────────────────────────────────────────
# Provider 层：抽象基类 + 三模型实现
# ──────────────────────────────────────────────


@dataclass
class TtsCredentials:
    """TTS 源凭据（B4）——Provider 源绑定「语音 TTS」角色时注入 provider。

    字段为空的项回退既有 settings 行为；`timeout_sec`/`proxy`/`headers`
    直接落到 httpx 传输层（源里配了就生效，不是只存不用）。
    """

    api_key: str = ""
    base_url: str = ""
    timeout_sec: float = 0.0
    proxy: str = ""
    headers: dict = field(default_factory=dict)

    def merge_headers(self, headers: dict) -> dict:
        merged = dict(headers)
        merged.update({str(k): str(v) for k, v in (self.headers or {}).items()})
        return merged

    def client_kwargs(self, timeout: float = 120.0) -> dict:
        kwargs: dict = {"timeout": float(self.timeout_sec or timeout)}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs


class BaseVoiceProvider(abc.ABC):
    """声音服务提供者的抽象基类（仿 STTService）。

    B4：Provider 源绑定「语音 TTS」角色后，凭据经 `TtsCredentials` 注入——
    源为真源，未绑定/未填字段时回退既有 settings 键（行为不变）。
    """

    def __init__(self, creds: Optional["TtsCredentials"] = None) -> None:
        self._cred = creds or TtsCredentials()

    def _api_key(self, default: str) -> str:
        return self._cred.api_key or default

    def _headers(self, headers: dict) -> dict:
        return self._cred.merge_headers(headers)

    def _http(self, timeout: float = 120.0) -> Any:
        """构造带超时/代理的异步客户端（源未配置时退化为默认传输）。"""
        return httpx.AsyncClient(**self._cred.client_kwargs(timeout))

    @abc.abstractmethod
    async def clone(
        self,
        *,
        audio_ref: str,
        voice_name: str,
        target_model: str = "",
        audition_text: str = "",
    ) -> tuple[str, str]:
        """克隆音色，返回 (voice_api_id, voice_name)。"""

    @abc.abstractmethod
    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> tuple[bytes, list[dict]]:
        """合成语音，返回音频字节流。"""

    async def build_audio_ref(self, path: Path) -> str:
        """把本地参考音频转成 provider 能用的引用（**能力位**，取代按 provider 名分支）。

        默认实现：需要公网 URL → 走注入的上传器（`PublicUploadManager`）。
        Qwen 覆盖为 base64 data URI（无需公网 URL）。
        """
        uploader = getattr(self, "_uploader", None)
        if uploader is None:
            raise RuntimeError(
                "该 TTS provider 需要公网 URL，但未注入上传器（PublicUploadManager）"
            )
        return await uploader.upload(path)


# ── 兼容别名已删除（B7-T7.5）──────────────────────────────────────
# 三个 TTS Provider 的实现与名字都只在 `plugins/tts_*/provider.py`；
# 既有导入/打桩路径已改到插件模块。engine 里不再有任何 TTS/LLM 实现类名。

class VoiceService:
    """声音克隆 + TTS 编排服务（仿 STTService 单例范式）。"""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        upload_dir: Optional[Path] = None,
    ) -> None:
        self._db_path = db_path or settings.tts_voice_db
        self._output_dir = output_dir or settings.tts_output_dir
        self._upload_dir = upload_dir or settings.tts_upload_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        self._storage = VoiceStorage(self._db_path)
        self._uploader = PublicUploadManager()
        self._providers: dict[str, BaseVoiceProvider] = {}

    # ── Provider 工厂 ──

    @staticmethod
    def _tts_credentials() -> TtsCredentials:
        """B4：解析「语音 TTS」角色绑定的源凭据（未绑定 → 空凭据回退 settings）。"""
        try:
            from clipwright.services import provider_sources as ps
            bound = ps.resolve("tts")
        except Exception:  # noqa: BLE001 — 存储层异常不阻断（回退旧配置）
            return TtsCredentials()
        if not bound:
            return TtsCredentials()
        return TtsCredentials(
            api_key=str(bound.get("api_key") or ""),
            base_url=str(bound.get("base_url") or ""),
            timeout_sec=float(bound.get("timeout_sec") or 0.0),
            proxy=str(bound.get("proxy") or ""),
            headers=dict(bound.get("headers") or {}),
        )

    def _get_provider(self, name: str) -> BaseVoiceProvider:
        creds = self._tts_credentials()
        # B-2：provider 名唯一来源 = contribution.id（别名归一集中在
        # providers/tts.normalize_tts_name——P1-25 的 qwen_tts 兼容在此收口）。
        # 先归一再查缓存，别名与规范名必落同一实例。
        from clipwright.providers.tts import (
            normalize_tts_name,
            resolve_tts_entry,
            tts_provider_names,
        )

        canonical = normalize_tts_name(name)
        # 未绑定源（无任何凭据字段）时保持裸名缓存键——与既有注入点约定
        # （`svc._providers["qwen-tts"]`）兼容；绑定了源则按凭据指纹分桶，
        # 保证换绑/改密钥后不会复用旧 provider 实例。
        if not (creds.api_key or creds.base_url or creds.proxy or creds.timeout_sec
                or creds.headers):
            cache_key = canonical
        else:
            cache_key = (f"{canonical}|{creds.api_key[:6]}|{creds.proxy}|"
                         f"{creds.timeout_sec}|{creds.base_url}")
        if cache_key not in self._providers:
            entry = resolve_tts_entry(canonical)
            if entry is None:
                available = tts_provider_names()
                raise ValueError(
                    f"Unknown provider '{name}'. "
                    + (f"Choose from: {', '.join(available)}" if available
                       else "未安装任何 TTS provider 插件（能力缺失）")
                )
            self._providers[cache_key] = entry.factory(self._uploader, creds)
        if name != canonical:  # 别名 → 同一实例
            self._providers[name] = self._providers[cache_key]
        return self._providers[cache_key]

    def _model_for(self, provider_name: str) -> str:
        """默认模型由**插件条目**声明（`entry.default_model()`），engine 不再硬编码映射。"""
        from clipwright.providers.tts import resolve_tts_entry

        entry = resolve_tts_entry(provider_name)
        return entry.default_model() if entry is not None else ""

    # ── clone ──

    async def clone(
        self,
        *,
        provider: str = "",
        voice_name: str = "",
        audio_path: str = "",
        audio_url: str = "",
        data_uri: str = "",
        target_model: str = "",
        audition_text: str = "",
    ) -> VoiceResult:
        """克隆音色并持久化元数据。"""
        if not (settings.tts_dashscope_api_key or self._tts_credentials().api_key):
            return VoiceResult(success=False, error="未配置 TTS API Key：请在设置→模型提供商绑定语音源，或在 .env 中设置 CLIPWRIGHT_TTS_DASHSCOPE_API_KEY")

        provider_name = provider or settings.tts_default_provider
        model = target_model or self._model_for(provider_name)
        prov = self._get_provider(provider_name)

        # 解析 audio_ref
        audio_ref = ""
        if data_uri:
            audio_ref = data_uri
        elif audio_path:
            p = Path(audio_path)
            if not p.exists():
                return VoiceResult(success=False, error=f"音频文件不存在: {audio_path}")
            # 安全：仅允许白名单目录内的音频，防止任意文件读取（base64 外泄通道）
            from clipwright.security import SecurityViolation, assert_allowed_path
            try:
                assert_allowed_path(p)
            except SecurityViolation as e:
                return VoiceResult(success=False, error=str(e))
            # B7-T7.1：引用形态由 provider 声明（`build_audio_ref`），不再按 provider 名分支
            try:
                audio_ref = await prov.build_audio_ref(p)
            except Exception as e:
                return VoiceResult(success=False, error=f"参考音频处理失败: {e}")
        elif audio_url:
            audio_ref = audio_url
        else:
            return VoiceResult(success=False, error="请提供 audio_path、audio_url 或 data_uri")

        try:
            voice_api_id, resolved_name = await prov.clone(
                audio_ref=audio_ref,
                voice_name=voice_name,
                target_model=model,
                audition_text=audition_text,
            )
        except Exception as e:
            return VoiceResult(success=False, error=str(e)[:500])

        record = VoiceRecord(
            id=uuid.uuid4().hex[:12],
            provider=provider_name,
            voice_id=voice_api_id,
            voice_name=resolved_name,
            target_model=model,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._storage.add(record.model_dump())
        logger.info("Voice cloned: %s (provider=%s, voice_id=%s)", resolved_name, provider_name, voice_api_id)
        return VoiceResult(data=record.model_dump())

    # ── synthesize ──

    async def synthesize(
        self,
        *,
        voice_id: str,
        text: str,
        provider: str = "",
        target_model: str = "",
        instructions: str = "",
        output_path: str = "",
        **params: Any,
    ) -> VoiceResult:
        """用已克隆音色合成语音。"""
        record = self._storage.get(voice_id)
        if not record:
            from clipwright.services.voice_errors import VoiceErrorCode, VoiceErrorStage

            return VoiceResult(
                success=False,
                error=f"音色不存在：{voice_id}（请先在「语音克隆」页创建）",
                data={"error": {
                    "error_code": VoiceErrorCode.VOICE_NOT_FOUND,
                    "stage": VoiceErrorStage.CONFIG,
                    "provider_id": "",
                    "retryable": False,
                    "message": f"音色不存在：{voice_id}",
                }},
            )

        prov_name = provider or record.get("provider", settings.tts_default_provider)
        model = target_model or record.get("target_model", "")
        voice_api_id = record.get("voice_id", "")
        try:
            prov = self._get_provider(prov_name)
        except Exception as e:  # noqa: BLE001 — provider 解析失败也是配置错误（PRD §6.3）
            from clipwright.services.voice_errors import VoiceErrorCode, VoiceErrorStage

            return VoiceResult(
                success=False,
                error=f"provider 不可用：{prov_name}（{str(e)[:80]}）",
                data={"error": {
                    "error_code": VoiceErrorCode.PROVIDER_NOT_FOUND,
                    "stage": VoiceErrorStage.CONFIG,
                    "provider_id": prov_name,
                    "retryable": False,
                    "message": f"provider 不可用：{prov_name}",
                }},
            )

        try:
            audio_bytes = await prov.synthesize(
                voice_api_id=voice_api_id,
                model=model,
                text=text,
                instructions=instructions or None,
                **params,
            )
        except Exception as e:  # noqa: BLE001 — provider 异常统一在调度层分类（PRD §6.3）
            from clipwright.services.voice_errors import classify_error

            info = classify_error(e, provider_id=prov_name)
            logger.warning(
                "TTS 合成失败 provider=%s code=%s stage=%s retryable=%s: %s",
                prov_name, info["error_code"], info["stage"], info["retryable"], info["message"],
            )
            # 结构化错误随 VoiceResult 上抛：调用方（agent / API / 前端）不必再猜字符串
            return VoiceResult(success=False, error=info["message"], data={"error": info})

        # 写文件（output_path 必须落在 TTS 输出目录内，防任意路径写入）
        if output_path:
            from clipwright.security import is_within
            out = Path(output_path)
            if not is_within(self._output_dir, out):
                return VoiceResult(success=False, error="output_path 必须位于 TTS 输出目录内")
        else:
            out = self._output_dir / f"tts_{uuid.uuid4().hex[:12]}.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio_bytes)

        duration = _get_duration(str(out))
        audio_url = f"/voice_audio/{out.name}"
        logger.info("Synthesized %d bytes → %s", len(audio_bytes), out.name)
        return VoiceResult(data={
            "audio_path": str(out),
            "audio_url": audio_url,
            "duration_sec": duration,
            "voice_id": voice_id,
            "provider": prov_name,
            "text": text,
        })

    # ── CRUD 委托 ──

    def list_voices(self) -> list[dict]:
        return self._storage.load()

    def get_voice(self, db_id: str) -> Optional[dict]:
        return self._storage.get(db_id)

    def delete_voice(self, db_id: str) -> bool:
        return self._storage.delete(db_id)


# ── 单例 ──

_voice_service: Optional[VoiceService] = None
_voice_lock = __import__('threading').Lock()


def get_voice_service() -> VoiceService:
    global _voice_service
    if _voice_service is None:
        with _voice_lock:
            if _voice_service is None:
                _voice_service = VoiceService()
    return _voice_service

