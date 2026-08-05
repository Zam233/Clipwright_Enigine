"""统一错误码规范 — 所有 API 错误使用标准格式。

Layer 4: 错误码规范。

用法:
    raise HTTPException(status_code=400, detail=ErrorCode.params_invalid("persona_id 不能为空"))
"""

from __future__ import annotations

from typing import Any


def _err(code: str, message: str, detail: Any = None) -> dict:
    """构建标准错误响应体。"""
    err = {
        "error": {"code": code, "message": message},
        "status": "error",
    }
    if detail:
        err["error"]["detail"] = str(detail)
    return err


class ErrorCode:
    """标准错误码集合。"""

    # ── 通用 (GEN-xxx) ──
    @staticmethod
    def unknown(err: Any = None) -> dict:
        return _err("GEN-001", "未知错误", err)

    @staticmethod
    def not_found(name: str = "资源") -> dict:
        return _err("GEN-002", f"{name}不存在")

    @staticmethod
    def params_invalid(msg: str = "参数错误") -> dict:
        return _err("GEN-003", msg)

    @staticmethod
    def rate_limited(retry_after: int = 60) -> dict:
        return _err("GEN-004", f"请求频率过高，请在 {retry_after}s 后重试")

    # ── 管线 (PIP-xxx) ──
    @staticmethod
    def pipeline_not_found(pipeline_id: str = "") -> dict:
        return _err("PIP-001", f"管线不存在: {pipeline_id}")

    @staticmethod
    def pipeline_timeout(duration: int = 900) -> dict:
        return _err("PIP-002", f"管线执行超时（>{duration}s）")

    @staticmethod
    def pipeline_queue_full() -> dict:
        return _err("PIP-003", "管线队列已满，请稍后重试")

    @staticmethod
    def agent_failed(agent: str = "", reason: str = "") -> dict:
        return _err("PIP-010", f"Agent[{agent}] 执行失败", reason)

    # ── Persona (PER-xxx) ──
    @staticmethod
    def persona_not_found(pid: str = "") -> dict:
        return _err("PER-001", f"Persona 不存在: {pid}")

    @staticmethod
    def persona_invalid(msg: str = "") -> dict:
        return _err("PER-002", f"Persona 配置错误", msg)

    # ── 素材 (MAT-xxx) ──
    @staticmethod
    def material_not_found(path: str = "") -> dict:
        return _err("MAT-001", f"素材不存在: {path}")

    # ── LLM (LLM-xxx) ──
    @staticmethod
    def llm_unavailable(msg: str = "") -> dict:
        return _err("LLM-001", "LLM 服务不可用", msg)

    @staticmethod
    def llm_timeout(msg: str = "") -> dict:
        return _err("LLM-002", "LLM 请求超时", msg)

    # ── 渲染 (RND-xxx) ──
    @staticmethod
    def render_failed(msg: str = "") -> dict:
        return _err("RND-001", "渲染失败", msg)

    @staticmethod
    def ffmpeg_not_found() -> dict:
        return _err("RND-002", "FFmpeg 未安装")

    # ── 插件 (PLG-xxx) ──
    @staticmethod
    def plugin_not_found(pid: str = "") -> dict:
        return _err("PLG-001", f"插件不存在: {pid}")
