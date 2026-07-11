"""帧艺 ClipWright 内容视频编排引擎 — FastAPI 入口。

启动: uvicorn clipwright.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
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
from clipwright.api import material as material_api
from clipwright.api import skill as skill_api
from clipwright.category import (
    CategoryRegistry,
    DigitalReviewPlugin,
    KichikuFastcutPlugin,
    KnowledgeLongformPlugin,
    VlogDailyPlugin,
)
from clipwright.plugins import PluginLoader
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

    # 3. 注册内置技能（可组合的高级能力，编排多个 Tool）
    register_builtin_skills()

    # 4. 初始化第三方插件系统
    _plugin_loader = PluginLoader(plugin_dir=Path("plugins"))
    loaded = _plugin_loader.load_all()
    if loaded:
        print(f"[Clipwright] Loaded {len(loaded)} third-party plugins: {', '.join(loaded)}")

    # 输出能力概览
    tool_count = len(ToolRegistry.list())
    skill_count = len(SkillRegistry.list())
    material_count = len(MaterialRegistry.list())
    print(f"[Clipwright] Capabilities: {tool_count} tools, {skill_count} skills, {material_count} material sources")

    # 5. 注入 PluginLoader 到 API 模块
    plugin_api.set_loader(_plugin_loader)

    yield

    # 清理
    if _plugin_loader:
        _plugin_loader.clear()
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "clipwright-engine"}
