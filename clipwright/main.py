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

    # 5. 初始化第三方插件系统
    _plugin_loader = PluginLoader(plugin_dir=Path("plugins"))
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "clipwright-engine"}
