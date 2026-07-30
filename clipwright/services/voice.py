"""声音克隆与语音合成（TTS 推理）服务。

能力（仿 STTService 范式，分三段构建）：
1. 数据层：VoiceRecord / VoiceResult / VoiceStorage(JSON) / split_text / 音频转码助手
2. Provider 层：Qwen-TTS / CosyVoice / MiniMax 三模型抽象 + 可配置公网上传
3. 编排层：VoiceService —— clone / synthesize / dub_script（切分文案逐段配音）

所有云调用走阿里云百炼（DashScope）；CosyVoice 合成走 dashscope SDK WebSocket。
克隆音色元数据存 JSON 文件（默认 PluginData/voices/voices.json）。
"""

from __future__ import annotations

import abc
import base64
import json
import re
import subprocess
import threading
import uuid
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
# 数据层：文本切分 / 音频助手
# ──────────────────────────────────────────────


def split_text(text: str, mode: str = "sentence") -> list[str]:
    """按句末标点或段落切分文案（逐字对齐参考工程 _split_text）。

    sentence: 去除直/弯双引号、压平换行，按 。！？ 切分并保留标点。
    paragraph: 按空行（段落）切分。
    """
    text = text.strip()
    # 去除直双引号与弯双引号
    text = re.sub(r'["\u201c\u201d]', "", text)

    if mode == "paragraph":
        raw = re.split(r"\n\s*\n+", text)
        return [s.strip() for s in raw if s.strip()]

    text = re.sub(r"\n", "", text)  # 压平换行
    parts = re.split(r"(?<=[。！？])", text)
    segments = [s.strip() for s in parts if s.strip()]
    return segments if segments else [text]


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
        """If URL is local, upload to public; otherwise return as-is."""
        if not _is_local_url(url):
            return url
        local_path = urlparse(url).path
        # Resolve relative to project root
        file_path = Path(__file__).resolve().parent.parent.parent / local_path.lstrip("/")
        if not file_path.exists():
            raise FileNotFoundError(f"Local file not found: {file_path}")
        return await self.upload(file_path)


# ──────────────────────────────────────────────
# Provider 层：抽象基类 + 三模型实现
# ──────────────────────────────────────────────


class BaseVoiceProvider(abc.ABC):
    """声音服务提供者的抽象基类（仿 STTService）。"""

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
    ) -> bytes:
        """合成语音，返回音频字节流。"""


class QwenTTSProvider(BaseVoiceProvider):
    """Qwen-TTS：clone 支持 base64 data URI（无需公网 URL），synth 走 HTTP。"""

    async def clone(
        self,
        *,
        audio_ref: str,
        voice_name: str,
        target_model: str = "",
        audition_text: str = "",
    ) -> tuple[str, str]:
        payload = {
            "model": "qwen-voice-enrollment",
            "input": {
                "action": "create",
                "target_model": target_model or "qwen3-tts-vc-2026-01-22",
                "preferred_name": _sanitize_name(voice_name, f"voice_{uuid.uuid4().hex[:6]}"),
                "audio": {"data": audio_ref},
            },
        }
        url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        logger.info("Qwen-TTS clone -> POST %s", url)
        headers = {
            "Authorization": f"Bearer {settings.tts_dashscope_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            detail = resp.text[:500]
            raise RuntimeError(f"Qwen-TTS clone failed [{resp.status_code}]: {detail}")
        body = resp.json()
        voice_id = body.get("output", {}).get("voice", "")
        if not voice_id:
            raise RuntimeError(f"Qwen-TTS returned no voice_id: {body}")
        return voice_id, payload["input"]["preferred_name"]

    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> bytes:
        if "realtime" in model.lower():
            raise ValueError(
                f"音色绑定的是实时模型 '{model}'，不支持 HTTP 合成。"
                "请用 qwen3-tts-vc-2026-01-22 重新克隆音色。"
            )
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        inp: dict = {"text": text, "voice": voice_api_id}
        instructions = kwargs.get("instructions")
        if instructions:
            inp["instructions"] = instructions
        payload = {"model": model, "input": inp}
        headers = {
            "Authorization": f"Bearer {settings.tts_dashscope_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"Qwen-TTS 合成失败 [{resp.status_code}]: {resp.text[:500]}")
        body = resp.json()
        audio_info = body.get("output", {}).get("audio", {})
        if audio_info.get("data"):
            return base64.b64decode(audio_info["data"])
        if audio_info.get("url"):
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.get(audio_info["url"])
            if r.status_code == 200:
                return r.content
        raise RuntimeError(f"Qwen-TTS 响应中没有音频: {str(body)[:300]}")


class CosyVoiceProvider(BaseVoiceProvider):
    """CosyVoice：clone 需公网 URL（PublicUpload），synth 走 dashscope SDK WebSocket。"""

    def __init__(self, uploader: Optional[PublicUploadManager] = None) -> None:
        self._uploader = uploader

    async def clone(
        self,
        *,
        audio_ref: str,
        voice_name: str,
        target_model: str = "",
        audition_text: str = "",
    ) -> tuple[str, str]:
        # CosyVoice needs a public URL; auto-upload local URLs
        url = audio_ref
        if _is_local_url(url) and self._uploader:
            url = await self._uploader.maybe_upload(url)

        payload = {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": target_model or "cosyvoice-v3.5-plus",
                "prefix": _sanitize_name(voice_name, f"myvoice{uuid.uuid4().hex[:6]}", allow_extra=""),
                "url": url,
                "enable_preprocess": True,
                "max_prompt_audio_length": 30.0,
            },
        }
        api_url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        logger.info("CosyVoice clone -> POST %s", api_url)
        headers = {
            "Authorization": f"Bearer {settings.tts_dashscope_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(api_url, json=payload, headers=headers)

        if resp.status_code != 200:
            detail = resp.text[:500]
            if "detect audio failed" in detail or "Audio.DecoderError" in detail:
                raise RuntimeError(
                    "CosyVoice 未能从音频中检测到有效人声。请确保音频：\n"
                    "1. 纯人声，无背景音乐/噪音\n"
                    "2. 至少 5 秒连续说话\n"
                    "3. 单人，正常语速\n"
                    "4. 时长 10-60 秒，文件 ≤ 10MB"
                )
            raise RuntimeError(f"CosyVoice clone failed [{resp.status_code}]: {detail}")

        body = resp.json()
        voice_id = body.get("voice_id") or body.get("output", {}).get("voice_id", "")
        if not voice_id:
            raise RuntimeError(f"CosyVoice returned no voice_id: {body}")
        return voice_id, payload["input"]["prefix"]

    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> bytes:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.base_websocket_api_url = (
            f"wss://{settings.tts_workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        )
        # SDK 仅支持全局 api_key：用锁串行化「改 key → 合成 → 还原」，避免并发请求相互覆盖凭据
        with _DASHSCOPE_KEY_LOCK:
            old_key = dashscope.api_key
            dashscope.api_key = settings.tts_dashscope_api_key
            try:
                synth_kwargs: dict = dict(model=model, voice=voice_api_id)
                for key in ("instructions", "volume", "speech_rate", "pitch_rate", "seed"):
                    val = kwargs.get(key)
                    if val is not None:
                        synth_kwargs[key] = val
                synthesizer = SpeechSynthesizer(**synth_kwargs)
                audio = synthesizer.call(text)
                if audio is None:
                    raise RuntimeError("CosyVoice synthesis returned None")
                return audio if isinstance(audio, bytes) else audio.encode()
            finally:
                dashscope.api_key = old_key


class MiniMaxProvider(BaseVoiceProvider):
    """MiniMax：clone 需公网 URL + audition text，synth 走 HTTP（hex/base64/url 响应）。"""

    def __init__(self, uploader: Optional[PublicUploadManager] = None) -> None:
        self._uploader = uploader

    async def clone(
        self,
        *,
        audio_ref: str,
        voice_name: str,
        target_model: str = "",
        audition_text: str = "",
    ) -> tuple[str, str]:
        url = audio_ref
        if _is_local_url(url) and self._uploader:
            url = await self._uploader.maybe_upload(url)

        voice_id_str = voice_name or f"mini_{uuid.uuid4().hex[:8]}"
        payload = {
            "model": target_model or "MiniMax/speech-2.8-turbo",
            "input": {
                "action": "voice_clone",
                "voice_id": voice_id_str,
                "audio_url": url,
                "text": audition_text or "你好，欢迎使用音色克隆。",
            },
        }
        api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        logger.info("MiniMax clone -> POST %s", api_url)
        headers = {
            "Authorization": f"Bearer {settings.tts_dashscope_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(api_url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"MiniMax clone failed [{resp.status_code}]: {resp.text[:500]}")
        return voice_id_str, voice_id_str

    async def synthesize(
        self,
        *,
        voice_api_id: str,
        model: str,
        text: str,
        **kwargs: Any,
    ) -> bytes:
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        vs = {"voice_id": voice_api_id, "speed": 1, "vol": 1, "pitch": 0}
        emotion = kwargs.get("emotion")
        if emotion:
            vs["emotion"] = emotion
        inp: dict = {
            "text": text,
            "voice_setting": vs,
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        payload = {"model": model or "MiniMax/speech-2.8-turbo", "input": inp}
        headers = {
            "Authorization": f"Bearer {settings.tts_dashscope_api_key}",
            "Content-Type": "application/json",
        }
        logger.info("MiniMax synthesize -> POST %s", url)
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(f"MiniMax synthesis failed [{resp.status_code}]: {resp.text[:500]}")
        body = resp.json()
        audio_data = body.get("output", {}).get("data", {})
        if audio_data.get("audio"):
            return bytes.fromhex(audio_data["audio"])
        audio_info = body.get("output", {}).get("audio", {})
        if audio_info.get("data"):
            return base64.b64decode(audio_info["data"])
        if audio_info.get("url"):
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.get(audio_info["url"])
            if r.status_code == 200:
                return r.content
        ct = resp.headers.get("content-type", "")
        if "audio" in ct or "octet" in ct:
            return resp.content
        raise RuntimeError(f"MiniMax 响应中没有音频: {str(body)[:300]}")


# ──────────────────────────────────────────────
# 编排层：VoiceService
# ──────────────────────────────────────────────


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

    def _get_provider(self, name: str) -> BaseVoiceProvider:
        if name not in self._providers:
            if name == "qwen-tts":
                self._providers[name] = QwenTTSProvider()
            elif name == "cosyvoice":
                self._providers[name] = CosyVoiceProvider(self._uploader)
            elif name == "minimax":
                self._providers[name] = MiniMaxProvider(self._uploader)
            else:
                raise ValueError(f"Unknown provider '{name}'. Choose from: qwen-tts, cosyvoice, minimax")
        return self._providers[name]

    def _model_for(self, provider_name: str) -> str:
        return {
            "qwen-tts": settings.tts_qwen_model,
            "cosyvoice": settings.tts_cosyvoice_model,
            "minimax": settings.tts_minimax_model,
        }.get(provider_name, "")

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
        if not settings.tts_dashscope_api_key:
            return VoiceResult(success=False, error="未配置 TTS DashScope API Key，请在 .env 中设置 CLIPWRIGHT_TTS_DASHSCOPE_API_KEY")

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
            if provider_name == "qwen-tts":
                raw = p.read_bytes()
                mime = _guess_mime(p.suffix)
                b64 = base64.b64encode(raw).decode()
                audio_ref = f"data:{mime};base64,{b64}"
            else:
                # CosyVoice / MiniMax 需要公网 URL
                try:
                    audio_ref = await self._uploader.upload(p)
                except Exception as e:
                    return VoiceResult(success=False, error=f"公网上传失败: {e}")
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
            return VoiceResult(success=False, error=f"Voice '{voice_id}' not found")

        prov_name = provider or record.get("provider", settings.tts_default_provider)
        model = target_model or record.get("target_model", "")
        voice_api_id = record.get("voice_id", "")
        prov = self._get_provider(prov_name)

        try:
            audio_bytes = await prov.synthesize(
                voice_api_id=voice_api_id,
                model=model,
                text=text,
                instructions=instructions or None,
                **params,
            )
        except Exception as e:
            return VoiceResult(success=False, error=str(e)[:500])

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

    # ── dub_script ──

    async def dub_script(
        self,
        *,
        voice_id: str,
        text: str,
        split_mode: str = "sentence",
        provider: str = "",
        target_model: str = "",
        instructions: str = "",
        **params: Any,
    ) -> VoiceResult:
        """切分文案并逐段配音。"""
        if not text.strip():
            return VoiceResult(success=False, error="文本为空，无可配音内容")
        segments_text = split_text(text, split_mode)
        if not segments_text or all(not s.strip() for s in segments_text):
            return VoiceResult(success=False, error="文本为空，无可配音内容")

        results: list[dict[str, Any]] = []
        total_duration = 0.0

        for idx, seg in enumerate(segments_text):
            seed = hash(seg) % 2147483647
            try:
                synth_params: dict[str, Any] = {**params, "seed": seed}
                sr = await self.synthesize(
                    voice_id=voice_id,
                    text=seg,
                    provider=provider,
                    target_model=target_model,
                    instructions=instructions,
                    **synth_params,
                )
                if sr.success:
                    d = sr.data
                    d["index"] = idx
                    d["seed"] = seed
                    total_duration += d.get("duration_sec", 0)
                    results.append(d)
                else:
                    results.append({"index": idx, "text": seg, "error": sr.error})
            except Exception as e:
                results.append({"index": idx, "text": seg, "error": str(e)[:200]})

        return VoiceResult(data={
            "segments": results,
            "total": len(results),
            "total_duration_sec": round(total_duration, 2),
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
