"""帧艺 ClipWright 内容视频编排引擎 — FastAPI 入口。

启动: uvicorn clipwright.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from clipwright.config import logger
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
        import asyncio
        asyncio.create_task(preprocess_worker())
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

# CORS — 允许测试前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载测试前端（可选，用于开发调试）
test_fe_path = Path(__file__).resolve().parent.parent / "test-frontend"
if test_fe_path.exists():
    app.mount("/test", StaticFiles(directory=str(test_fe_path), html=True), name="test-frontend")

# 挂载渲染输出目录（用于预览）
renders_path = Path("renders")
renders_path.mkdir(parents=True, exist_ok=True)
app.mount("/renders", StaticFiles(directory=str(renders_path)), name="renders")

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

    # MongoDB
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

    # FFmpeg
    try:
        import subprocess
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        components["ffmpeg"] = "ok" if r.returncode == 0 else "not_found"
    except Exception:
        components["ffmpeg"] = "not_found"

    # Hyperframes
    try:
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        components["hyperframes"] = "ok" if HyperframesRenderer.is_available() else "not_found"
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
            # Pipeline 统计
            total = PipelineModel.count({})
            completed = PipelineModel.count({"status": "completed"})
            failed = PipelineModel.count({"status": "failed"})
            lines.append("# HELP clipwright_pipelines_total Pipeline count by status")
            lines.append("# TYPE clipwright_pipelines_total gauge")
            lines.append(f'clipwright_pipelines_total{{status="total"}} {total}')
            lines.append(f'clipwright_pipelines_total{{status="completed"}} {completed}')
            lines.append(f'clipwright_pipelines_total{{status="failed"}} {failed}')

            # LLM 统计
            llm_total = LLMCallModel.count({})
            lines.append("# HELP clipwright_llm_calls_total Total LLM calls")
            lines.append("# TYPE clipwright_llm_calls_total counter")
            lines.append(f"clipwright_llm_calls_total {llm_total}")

            # LLM token 聚合
            try:
                pipeline = [{"$group": {"_id": None, "input": {"$sum": "$input_tokens"}, "output": {"$sum": "$output_tokens"}}}]
                results = list(LLMCallModel.aggregate(pipeline))
                if results:
                    r = results[0]
                    lines.append("# HELP clipwright_llm_tokens_total Total LLM tokens")
                    lines.append("# TYPE clipwright_llm_tokens_total counter")
                    lines.append(f'clipwright_llm_tokens_total{{type="input"}} {r.get("input", 0)}')
                    lines.append(f'clipwright_llm_tokens_total{{type="output"}} {r.get("output", 0)}')
            except Exception:
                pass
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
