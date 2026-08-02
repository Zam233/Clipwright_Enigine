"""帧艺 ClipWright 内容视频编排引擎 — FastAPI 入口。

启动: uvicorn clipwright.main:app --reload
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from clipwright.config import settings, logger
from clipwright.services.async_util import cached_probe


def _ensure_ffmpeg_on_path() -> None:
    """若 ffmpeg 不在 PATH 上但可探测到（如 WinGet 安装），将其目录加入 PATH。

    这样所有以 ``["ffmpeg", ...]`` / ``["ffprobe", ...]`` 形式调用 ffmpeg 的代码
    （渲染服务、各处理工具等）都能找到可执行文件，无需逐一修改调用点。
    """
    import os
    try:
        from clipwright.tool.video import resolve_ffmpeg
        path = resolve_ffmpeg()
        if path and os.path.isabs(path) and os.path.exists(path):
            bin_dir = os.path.dirname(path)
            cur = os.environ.get("PATH", "")
            if bin_dir not in cur.split(os.pathsep):
                os.environ["PATH"] = bin_dir + os.pathsep + cur
                logger.info("已将 ffmpeg 目录加入 PATH: %s", bin_dir)
    except Exception as e:
        logger.debug("ffmpeg PATH 注入失败: %s", e)


_ensure_ffmpeg_on_path()


def _probe_ffmpeg() -> bool:
    """同步 ffmpeg 探测（后台线程执行，不进事件循环线程）。"""
    try:
        import subprocess
        from clipwright.tool.video import resolve_ffmpeg
        r = subprocess.run([resolve_ffmpeg(), "-version"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


# /health 用的外部工具探针：缓存 + 后台刷新，await 永不阻塞事件循环。
_ffmpeg_available = cached_probe("ffmpeg", _probe_ffmpeg, ttl=600.0, default=False)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from clipwright.api import pipeline as pipeline_api
from clipwright.api import persona as persona_api
from clipwright.api import persona_forge as persona_forge_api
from clipwright.api import chat_forge as chat_forge_api
from clipwright.api import model_test as model_test_api
from clipwright.api import rag as rag_api
from clipwright.api import render as render_api
from clipwright.api import tool as tool_api
from clipwright.api import plugin as plugin_api
from clipwright.api import animation as animation_api
from clipwright.api import material as material_api
from clipwright.api import asset as asset_api
from clipwright.api import edl as edl_api
from clipwright.api import project as project_api
from clipwright.api import proxy as proxy_api
from clipwright.api import subtitle as subtitle_api
from clipwright.api import stt as stt_api
from clipwright.api import vision as vision_api
from clipwright.api import voice as voice_api
from clipwright.api import waveform as waveform_api
from clipwright.api import skill as skill_api
from clipwright.api import type_maker as type_maker_api
from clipwright.api import template as template_api
from clipwright.api import webhook as webhook_api
from clipwright.api import video_editor as video_editor_api
from clipwright.api import learning as learning_api
from clipwright.api import preprocess as preprocess_api
from clipwright.api import font as font_api
from clipwright.api import requirements as requirements_api
from clipwright.category import (
    CategoryRegistry,
    DigitalReviewPlugin,
    KichikuFastcutPlugin,
    KnowledgeLongformPlugin,
    VlogDailyPlugin,
)
from clipwright.plugins import PluginLoader
from clipwright.animation import register_builtin_animations, AnimationRegistry as AnimRegistry
from clipwright.material import MaterialRegistry
from clipwright.skill import register_builtin_skills, SkillRegistry
from clipwright.tool import ToolRegistry, register_builtin_tools

# 全局实例
_plugin_loader: PluginLoader | None = None


def get_plugin_loader() -> PluginLoader:
    """获取全局 PluginLoader 实例。"""
    global _plugin_loader
    assert _plugin_loader is not None, "PluginLoader not initialized (lifespan not started)"
    return _plugin_loader


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 — 初始化注册表等。"""
    global _plugin_loader

    # 1. 注册内置视频类型插件
    _register_builtin_plugins()

    # 2. 注册内置原子能力工具
    register_builtin_tools()

    # 3. 注册内置动画定义（22 个：12 onscreen + 10 transition）
    register_builtin_animations()

    # 3.5 注册内置 llm_mg 引擎的 Agent 提示词（系统核心能力，非插件）
    from clipwright.animation.mg import register_agent_prompts as _register_mg_prompts
    _register_mg_prompts()

    # 4. 注册内置技能（可组合的高级能力，编排多个 Tool）
    register_builtin_skills()

    # 4.5 加载用户自定义视频类型
    from clipwright.category.dynamic import register_user_types
    user_type_ids = register_user_types()
    if user_type_ids:
        logger.info("Loaded %d user-defined types: %s", len(user_type_ids), ", ".join(user_type_ids))

    # 4.75 初始化 MongoDB 连接
    from clipwright.context import mongo as mongo_ctx
    mongo_ctx.connect()

    # 4.85 确保 PluginData 目录结构存在
    from clipwright.config import settings as _cfg
    _pd = _cfg.plugin_data_dir
    for _sub in ("tmp", "assets", "thumbs", "cache", "plugins"):
        (_pd / _sub).mkdir(parents=True, exist_ok=True)

    # 5. 初始化第三方插件系统（数据统一写入 PluginData/）
    _plugin_loader = PluginLoader(
        plugin_dir=Path("plugins"),
        data_dir=_cfg.plugin_data_dir,
    )
    loaded = _plugin_loader.load_all()
    if loaded:
        logger.info("Loaded %d third-party plugins: %s", len(loaded), ", ".join(loaded))

    # 输出能力概览
    tool_count = len(ToolRegistry.list())
    skill_count = len(SkillRegistry.list())
    material_count = len(MaterialRegistry.list())
    anim_count = len(AnimRegistry.list())
    logger.info("Capabilities: %d tools, %d skills, %d material sources, %d animations",
                tool_count, skill_count, material_count, anim_count)

    # 5.5 安装日志流 Handler — INFO 级别以上自动推送到 SSE
    try:
        from clipwright.services.log_stream import install_log_stream
        install_log_stream()
        logger.info("日志流 Handler 已安装")
    except Exception as e:
        logger.warning("日志流 Handler 安装失败: %s", e)

    # 5.6 启动预处理后台工作线程
    try:
        from clipwright.services.material_preprocessor import preprocess_worker
        from clipwright.services.async_util import spawn_background
        spawn_background(preprocess_worker(), name="preprocess-worker")
        logger.info("素材预处理后台线程已启动")
    except Exception as e:
        logger.warning("预处理线程启动失败: %s", e)

    # 6. 注入 PluginLoader 到 API 模块
    plugin_api.set_loader(_plugin_loader)

    yield

    # 清理
    if _plugin_loader:
        _plugin_loader.clear()
    AnimRegistry.clear()
    CategoryRegistry.clear()
    MaterialRegistry.clear()
    ToolRegistry.clear()
    SkillRegistry.clear()


def _register_builtin_plugins() -> None:
    """注册所有内置视频类型插件。"""
    CategoryRegistry.register(KnowledgeLongformPlugin())
    CategoryRegistry.register(KichikuFastcutPlugin())
    CategoryRegistry.register(DigitalReviewPlugin())
    CategoryRegistry.register(VlogDailyPlugin())


app = FastAPI(
    title="帧艺 ClipWright 内容视频编排引擎",
    description="ClipWright Content Video Orchestration Engine — "
    "Persona 驱动的 AI 视频内容编排后端",
    version="0.1.0",
    lifespan=lifespan,
)

# 安全：API 令牌认证（settings.api_token 设置后启用；必须在 CORS 之前注册，使 CORS 处于最外层处理预检）
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from clipwright.security import SecurityViolation


@app.middleware("http")
async def api_token_auth(request: Request, call_next):
    if not settings.api_token or request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    is_api = path.startswith("/api/") and not path.startswith("/api/health")
    # 渲染成片与克隆语音属敏感内容，令牌模式下同样需要鉴权
    is_media = path.startswith(("/renders/", "/voice_audio/"))
    if not (is_api or is_media):
        return await call_next(request)
    expected = f"Bearer {settings.api_token}"
    ok = hmac.compare_digest(request.headers.get("Authorization", ""), expected)
    if not ok and is_media:
        # <video>/<audio> 标签无法携带 Authorization 头，允许 query token 校验
        ok = hmac.compare_digest(request.query_params.get("token", ""), settings.api_token)
        if ok:
            # 校验通过后从 query string 中抹除 token，避免泄露到访问日志 / Referer
            from urllib.parse import urlencode
            remaining = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            request.scope["query_string"] = urlencode(remaining).encode()
    if not ok:
        return JSONResponse(
            status_code=401,
            content={"detail": "未授权：缺少或错误的 API 令牌"},
        )
    return await call_next(request)


@app.exception_handler(SecurityViolation)
async def security_violation_handler(request: Request, exc: SecurityViolation):
    """安全校验失败（非法 ID / 路径遍历）统一返回 400。"""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# CORS — 设置令牌后限制来源；开发模式（无令牌）允许全部
_cors_origins = (
    [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if settings.api_token
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=(False if _cors_origins == ["*"] else True),
    allow_methods=["*"],
    allow_headers=["*"],
)

if not settings.api_token:
    logger.warning("安全提示: 未设置 CLIPWRIGHT_API_TOKEN，API 处于开放开发模式；生产部署请设置令牌。")

# 挂载测试前端（可选，用于开发调试）
test_fe_path = Path(__file__).resolve().parent.parent / "test-frontend"
if test_fe_path.exists():
    app.mount("/test", StaticFiles(directory=str(test_fe_path), html=True), name="test-frontend")

# 挂载渲染输出目录（用于预览）
renders_path = Path("renders")
renders_path.mkdir(parents=True, exist_ok=True)
app.mount("/renders", StaticFiles(directory=str(renders_path)), name="renders")

# 挂载 TTS 输出目录
tts_output_path = Path(settings.tts_output_dir)
tts_output_path.mkdir(parents=True, exist_ok=True)
app.mount("/voice_audio", StaticFiles(directory=str(tts_output_path)), name="voice_audio")

# 注册路由
app.include_router(pipeline_api.router)
app.include_router(persona_api.router)
app.include_router(persona_forge_api.router)
app.include_router(chat_forge_api.router)
app.include_router(rag_api.router)
app.include_router(model_test_api.router)
app.include_router(render_api.router)
app.include_router(tool_api.router)
app.include_router(plugin_api.router)
app.include_router(skill_api.router)
app.include_router(material_api.router)
app.include_router(animation_api.router)
app.include_router(stt_api.router)
app.include_router(asset_api.router)
app.include_router(edl_api.router)
app.include_router(proxy_api.router)
app.include_router(project_api.router)
app.include_router(subtitle_api.router)
app.include_router(voice_api.router)
app.include_router(waveform_api.router)
app.include_router(vision_api.router)
app.include_router(type_maker_api.router)
app.include_router(template_api.router)
app.include_router(webhook_api.router)
app.include_router(video_editor_api.router)
app.include_router(preprocess_api.router)
app.include_router(font_api.router)
app.include_router(learning_api.router)
app.include_router(requirements_api.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """增强健康检查 — 检测所有服务组件的状态。"""
    components = {"service": "clipwright-engine"}

    # MongoDB（is_connected 仅读标志，廉价；ping 已在启动期完成）
    try:
        from clipwright.context import mongo as mongo_ctx
        components["mongodb"] = "ok" if mongo_ctx.is_connected else "disconnected"
    except Exception:
        components["mongodb"] = "error"

    # LLM
    try:
        from clipwright.config import settings
        components["llm"] = "ok" if settings.llm_api_key else "no_key"
    except Exception:
        components["llm"] = "error"

    # FFmpeg / Hyperframes —— 读缓存探针，绝不在此同步 spawn 进程冻住事件循环。
    # 缓存为空（冷启动）时给 1.5s 宽限等后台探测，超时则返回 "checking"，仍不阻塞。
    try:
        components["ffmpeg"] = "ok" if await asyncio.wait_for(_ffmpeg_available(), 1.5) else "not_found"
    except asyncio.TimeoutError:
        components["ffmpeg"] = "checking"
    except Exception:
        components["ffmpeg"] = "not_found"

    try:
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        components["hyperframes"] = (
            "ok" if await asyncio.wait_for(HyperframesRenderer.ais_available(), 1.5) else "not_found"
        )
    except asyncio.TimeoutError:
        components["hyperframes"] = "checking"
    except Exception:
        components["hyperframes"] = "not_found"

    # Task Queue
    try:
        from clipwright.services.task_queue import get_task_queue
        tq = get_task_queue()
        components["queue_running"] = str(tq.running_count)
        components["queue_pending"] = str(tq.pending_count)
    except Exception:
        components["queue"] = "error"

    all_ok = all(v == "ok" for v in components.values() if v not in ("no_key", "0", "disconnected"))
    components["status"] = "ok" if all_ok else "degraded"
    return components


@app.get("/metrics")
async def metrics() -> str:
    """Prometheus 指标端点。"""
    lines = [
        "# HELP clipwright_info ClipWright engine info",
        "# TYPE clipwright_info gauge",
        'clipwright_info{version="0.1.0"} 1',
    ]
    try:
        from clipwright.context import mongo as mongo_ctx
        if mongo_ctx.is_connected:
            from clipwright.models.pipeline_model import PipelineModel, LLMCallModel

            # 同步 Mongo 统计查询整体 offload 到线程，避免冻住事件循环。
            def _query():
                _total = PipelineModel.count({})
                _completed = PipelineModel.count({"status": "completed"})
                _failed = PipelineModel.count({"status": "failed"})
                _llm_total = LLMCallModel.count({})
                _in_tokens = _out_tokens = 0
                try:
                    _pipeline = [{"$group": {"_id": None, "input": {"$sum": "$input_tokens"}, "output": {"$sum": "$output_tokens"}}}]
                    _res = list(LLMCallModel.aggregate(_pipeline))
                    if _res:
                        _in_tokens = _res[0].get("input", 0)
                        _out_tokens = _res[0].get("output", 0)
                except Exception:
                    pass
                return _total, _completed, _failed, _llm_total, _in_tokens, _out_tokens

            total, completed, failed, llm_total, in_tokens, out_tokens = await asyncio.to_thread(_query)

            lines.append("# HELP clipwright_pipelines_total Pipeline count by status")
            lines.append("# TYPE clipwright_pipelines_total gauge")
            lines.append(f'clipwright_pipelines_total{{status="total"}} {total}')
            lines.append(f'clipwright_pipelines_total{{status="completed"}} {completed}')
            lines.append(f'clipwright_pipelines_total{{status="failed"}} {failed}')

            lines.append("# HELP clipwright_llm_calls_total Total LLM calls")
            lines.append("# TYPE clipwright_llm_calls_total counter")
            lines.append(f"clipwright_llm_calls_total {llm_total}")

            lines.append("# HELP clipwright_llm_tokens_total Total LLM tokens")
            lines.append("# TYPE clipwright_llm_tokens_total counter")
            lines.append(f'clipwright_llm_tokens_total{{type="input"}} {in_tokens}')
            lines.append(f'clipwright_llm_tokens_total{{type="output"}} {out_tokens}')
    except Exception:
        pass

    # 队列统计
    try:
        from clipwright.services.task_queue import get_task_queue
        tq = get_task_queue()
        lines.append("# HELP clipwright_queue_tasks Task queue depth")
        lines.append("# TYPE clipwright_queue_tasks gauge")
        lines.append(f'clipwright_queue_tasks{{state="running"}} {tq.running_count}')
        lines.append(f'clipwright_queue_tasks{{state="pending"}} {tq.pending_count}')
    except Exception:
        pass

    return "\n".join(lines) + "\n"


def _main() -> None:
    """入口：按 env 配置启动 uvicorn（CLIPWRIGHT_HOST / CLIPWRIGHT_PORT /
    CLIPWRIGHT_DEBUG，默认 0.0.0.0:8000）。用法：``python -m clipwright.main``。
    """
    import uvicorn

    uvicorn.run(
        "clipwright.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )


if __name__ == "__main__":
    _main()
