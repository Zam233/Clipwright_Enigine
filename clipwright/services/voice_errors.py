"""TTS 协议错误分类（PRD §6.3 / 台账 H-028）。

**为什么需要它**：现在 provider 抛什么异常，上层只拿到一句 `str(e)[:500]`。用户看到的是
「401 Unauthorized」或者「'NoneType' object has no attribute 'x'」，既不知道是哪家 provider、
也不知道该去配凭据还是该重试。PRD §6.3 要求错误必须保留：

- **provider id**（谁的错）
- **阶段**（鉴权 / 请求 / 流式 / 解码 / 上传器）
- **可重试性**（retryable：网络抖动可以重试，401 重试一万次也没用）
- **可读错误码**（`auth_failed` / `rate_limited` / `provider_timeout` / …）

**边界**：分类只发生在 engine 的**调度层**（`VoiceService.synthesize` 的 except 分支）。
协议插件只管抛异常；把异常映射成人话是调度层的职责——这样换插件不用重写分类逻辑，
也不会为了友好错误提示在每个 provider 里复制一份字符串拼接。

**安全**：错误文本会进日志与 API 响应，因此分类时**剥掉可能的凭据片段**
（`sk-xxx`、`Bearer xxx`），密钥不进日志。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

__all__ = [
    "VoiceErrorCode",
    "VoiceErrorStage",
    "classify_error",
    "sanitize_message",
]

#: 可读错误码（前端/日志共用；只增不改，避免旧前端失配）
class VoiceErrorCode:
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_NOT_FOUND = "provider_not_found"
    VOICE_NOT_FOUND = "voice_not_found"
    UPLOADER_MISSING = "uploader_missing"
    QUOTA_EXCEEDED = "quota_exceeded"
    INVALID_REQUEST = "invalid_request"
    STREAM_BROKEN = "stream_broken"
    DECODE_FAILED = "decode_failed"
    UNKNOWN = "unknown"


#: 阶段（对应 PRD §6.3 的「鉴权/请求/流式/解码」）
class VoiceErrorStage:
    CONFIG = "config"        # provider / 音色 / 凭据配置阶段
    AUTH = "auth"
    REQUEST = "request"
    STREAM = "stream"
    DECODE = "decode"
    UNKNOWN = "unknown"


_KEY_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{8,}"),
    re.compile(r"(?i)api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}"),
)


def sanitize_message(text: str, limit: int = 300) -> str:
    """剥掉可能的凭据片段并截断（错误文本会进日志与 API 响应）。"""
    out = str(text or "")
    for pat in _KEY_PATTERNS:
        out = pat.sub("<redacted>", out)
    return out[:limit]


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "timedout" in name


def classify_error(
    exc: BaseException,
    *,
    provider_id: str = "",
    stage: str = "",
) -> dict[str, Any]:
    """把一个异常映射成结构化错误（code / stage / provider_id / retryable / message）。

    映射规则（宁可粗，不猜细节）：
    - 超时 → `provider_timeout` + retryable
    - 401/403/unauthorized/invalid api key → `auth_failed` + 不可重试
    - 429/rate limit/too many requests → `rate_limited` + retryable
    - quota/insufficient balance → `quota_exceeded` + 不可重试（要充值）
    - connection/reset/broken pipe/websocket → `stream_broken` + retryable
    - upload/公网 url/uploader → `uploader_missing` + 不可重试（要配上传器）
    - 其余 → `unknown` + 不可重试（避免把编程错误当网络抖动无限重试）
    """
    message = sanitize_message(str(exc))
    lowered = message.lower()
    name = type(exc).__name__

    code = VoiceErrorCode.UNKNOWN
    err_stage = stage or VoiceErrorStage.UNKNOWN
    retryable = False

    if _is_timeout(exc):
        code, err_stage, retryable = VoiceErrorCode.PROVIDER_TIMEOUT, VoiceErrorStage.REQUEST, True
    elif any(k in lowered for k in ("uploader", "公网", "public url", "upload")):
        code, err_stage = VoiceErrorCode.UPLOADER_MISSING, VoiceErrorStage.CONFIG
    elif any(k in lowered for k in ("insufficient", "quota", "balance", "余额", "配额")):
        code = VoiceErrorCode.QUOTA_EXCEEDED
    elif any(k in lowered for k in ("401", "403", "unauthorized", "invalid api key",
                                    "authentication", "鉴权", "apikey", "api key")):
        code, err_stage = VoiceErrorCode.AUTH_FAILED, VoiceErrorStage.AUTH
    elif any(k in lowered for k in ("429", "rate limit", "too many requests", "限流")):
        code, err_stage, retryable = VoiceErrorCode.RATE_LIMITED, VoiceErrorStage.REQUEST, True
    elif any(k in lowered for k in ("connection", "reset", "broken pipe", "websocket",
                                    "closed", "eof", "断")):
        code, err_stage, retryable = VoiceErrorCode.STREAM_BROKEN, VoiceErrorStage.STREAM, True
    elif any(k in lowered for k in ("decode", "codec", "ffmpeg", "音频格式")):
        code, err_stage = VoiceErrorCode.DECODE_FAILED, VoiceErrorStage.DECODE
    elif isinstance(exc, (ValueError, TypeError, KeyError)):
        code, err_stage = VoiceErrorCode.INVALID_REQUEST, VoiceErrorStage.REQUEST

    readable = {
        VoiceErrorCode.AUTH_FAILED: "provider 鉴权失败（请检查 API Key / 权限）",
        VoiceErrorCode.RATE_LIMITED: "provider 限流（可稍后重试）",
        VoiceErrorCode.VOICE_NOT_FOUND: "音色不存在（请先在「语音克隆」页创建）",
        VoiceErrorCode.PROVIDER_NOT_FOUND: "provider 未安装或不可用（请检查插件）",
        VoiceErrorCode.PROVIDER_TIMEOUT: "provider 请求超时（可重试）",
        VoiceErrorCode.QUOTA_EXCEEDED: "provider 额度/余额不足",
        VoiceErrorCode.UPLOADER_MISSING: "provider 需要公网 URL，但上传器不可用",
        VoiceErrorCode.STREAM_BROKEN: "provider 流式连接中断（可重试）",
        VoiceErrorCode.DECODE_FAILED: "音频解码失败",
        VoiceErrorCode.INVALID_REQUEST: "请求参数不合法",
        VoiceErrorCode.UNKNOWN: "合成失败",
    }[code]

    return {
        "error_code": code,
        "stage": err_stage,
        "provider_id": str(provider_id or ""),
        "retryable": retryable,
        "exception": name,
        "message": f"{readable}：{message}" if message else readable,
    }

