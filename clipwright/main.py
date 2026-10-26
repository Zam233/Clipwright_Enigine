"""帧艺 ClipWright 内容视频编排引擎 — FastAPI 入口。

启动: uvicorn clipwright.main:app --reload
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError

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
from clipwright.api import supervisor as supervisor_api
from clipwright.api import persona as persona_api
from clipwright.api import persona_forge as persona_forge_api
from clipwright.api import chat_forge as chat_forge_api
from clipwright.api import model_probe as model_probe_api
from clipwright.api import settings as settings_api
from clipwright.api import providers as providers_api
from clipwright.api import rag as rag_api
from clipwright.api import render as render_api
from clipwright.api import tool as tool_api
from clipwright.api import tool_surface as tool_surface_api
from clipwright.api import task as task_api
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
from clipwright.api import market as market_api
from clipwright.api import stats as stats_api
from clipwright.api import preprocess as preprocess_api
from clipwright.api import font as font_api
from clipwright.api import requirements as requirements_api
from clipwright.api import versions as versions_api
from clipwright.api import scheduler as scheduler_api
from clipwright.api import workspace as workspace_api
from clipwright.category import (
    CategoryRegistry,
    DigitalReviewPlugin,
    KichikuFastcutPlugin,
    KnowledgeLongformPlugin,
    LyricMvPlugin,
    VlogDailyPlugin,
)
from clipwright.plugins import PluginLoader
from clipwright.animation import register_builtin_animations, AnimationRegistry as AnimRegistry
from clipwright.material import MaterialRegistry
from clipwright.skill import register_builtin_skills, SkillRegistry
from clipwright.tool import ToolRegistry, register_builtin_tools

# 运行时配置覆盖：settings.json 的 store 值必须在 app/加载器/静态挂载
# 构造之前应用到 settings 单例（CORS/uvicorn/Mongo 等只在启动时读一次）
from clipwright.services import settings_store as _settings_store
_settings_store.apply_all(settings)

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

    # 4.1 A-5：内置贡献镜像入账门面（去重基准 + 管理可见；须在插件加载之前）
    try:
        from clipwright.plugins.builtin_contributions import ingest_builtin_contributions
        ingest_builtin_contributions()
    except Exception as _ingest_err:
        logger.warning("内置贡献入账失败（不影响启动）: %s", _ingest_err)

    # 4.2 B-1：内置 LLM provider 注册（anthropic/openai/ollama；幂等）
    try:
        from clipwright.providers.llm import ensure_llm_providers_registered
        ensure_llm_providers_registered()
    except Exception as _prov_err:
        logger.warning("LLM provider 注册失败（不影响启动）: %s", _prov_err)

    # 4.3 B-2：内置 TTS provider 注册（qwen-tts/cosyvoice/minimax；幂等）
    try:
        from clipwright.providers.tts import ensure_tts_providers_registered
        ensure_tts_providers_registered()
    except Exception as _tts_err:
        logger.warning("TTS provider 注册失败（不影响启动）: %s", _tts_err)

    # 4.4 B-2：内置 STT provider 注册（whisper/faster-whisper；幂等）
    try:
        from clipwright.providers.stt import ensure_stt_providers_registered
        ensure_stt_providers_registered()
    except Exception as _stt_err:
        logger.warning("STT provider 注册失败（不影响启动）: %s", _stt_err)

    # 4.5 B-3：内置渲染后端注册（local/remote；入口切换在下一批接线）
    try:
        from clipwright.providers.render import ensure_render_backends_registered
        ensure_render_backends_registered()
    except Exception as _rb_err:
        logger.warning("渲染后端注册失败（不影响启动）: %s", _rb_err)

    # 4.6 B4：内置嵌入 provider 注册（openai/ollama/local；幂等）
    try:
        from clipwright.providers.embedding import ensure_embedding_providers_registered
        ensure_embedding_providers_registered()
    except Exception as _emb_err:
        logger.warning("嵌入 provider 注册失败（不影响启动）: %s", _emb_err)

    # 4.7 B4：内置重排序 provider 注册（http/local；幂等）
    try:
        from clipwright.providers.rerank import ensure_rerank_providers_registered
        ensure_rerank_providers_registered()
    except Exception as _rr_err:
        logger.warning("重排序 provider 注册失败（不影响启动）: %s", _rr_err)

    # 4.8 B4：把既有平铺配置物化为一条 Provider 源（老用户升级后新页面不是空的）
    #     幂等：已有任何源则跳过；未配过凭据则跳过（不产生噪音配置）
    try:
        from clipwright.services.provider_sources import ensure_migrated
        migrated_id = ensure_migrated()
        if migrated_id:
            logger.info("已把既有 LLM 配置迁移为 Provider 源: %s", migrated_id)
    except Exception as _mig_err:
        logger.warning("Provider 源迁移失败（不影响启动）: %s", _mig_err)

    # 4.5 加载用户自定义视频类型
    from clipwright.category.dynamic import register_user_types
    user_type_ids = register_user_types()
    if user_type_ids:
        logger.info("Loaded %d user-defined types: %s", len(user_type_ids), ", ".join(user_type_ids))

    # 4.75 初始化 MongoDB 连接
    from clipwright.context import mongo as mongo_ctx
    mongo_ctx.connect()

    # 4.78 生产加固 1.2: 管线运行态启动恢复（遗留 running 标 interrupted + 重建映射）
    try:
        from clipwright.api.pipeline import recover_pipeline_runtime
        recover_pipeline_runtime()
    except Exception as e:
        logger.warning("管线运行态启动恢复跳过: %s", e)

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

    # 5.7 预热 Hyperframes 冷启动探针（非阻塞后台任务）
    # npx 冷启动可达 ~90s；抢先刷新缓存，避免首条管线把逻辑动画误降级为 drawtext。
    # timeout 120s > npx 冷启动 ~90s。
    # （B16 合并注记：5.6 的素材预处理 worker 已在本地线移除——合并后模块无此
    # 函数，不再启动；5.7 探针预热为远程线有效特性，保留。）
    try:
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        from clipwright.services.async_util import spawn_background
        spawn_background(
            HyperframesRenderer.await_available(120.0),
            name="hyperframes-warmup",
        )
        logger.info("Hyperframes 预热任务已启动")
    except Exception as e:
        logger.warning("Hyperframes 预热任务启动失败: %s", e)

    # 6. 注入 PluginLoader 到 API 模块
    plugin_api.set_loader(_plugin_loader)

    # 6.5 P8: 启动轻量定时调度器（后台循环；Mongo 未连接时扫描空转不报错）
    try:
        from clipwright.services import scheduler as _sched
        from clipwright.api import pipeline as _pipeline_api

        def _sched_handler(payload: dict) -> None:
            """定时任务动作：payload 含 topic 时启动管线（轮 86 出片闭环），
            否则退化为写 trace 事件。

            定时出片用法：payload = {"topic": "...", "persona_id": "...",
            "category_plugin_id": "...", "extra_params": {...}}——字段即
            PipelineRequest；extra_params.auto_render=true 可连渲染一起自动跑。
            """
            if not payload.get("topic"):
                from clipwright.services.trace import add_event as _te
                _te(payload.get("schedule_id", "sched"), "system", "info",
                    f"定时任务触发: {payload.get('name', '')}")
                return
            import asyncio as _asyncio
            from types import SimpleNamespace as _NS

            from clipwright.schema.pipeline import PipelineRequest as _PR

            owner = payload.get("owner_id", "")
            # 桩 Request：runner 只用 state.user_id / headers.get
            _stub_req = _NS(state=_NS(user_id=owner or None, user_role=None),
                            headers={})
            _extra = payload.get("extra_params") or {}
            _known = {k: v for k, v in payload.items() if k in _PR.model_fields}
            _known.setdefault("persona_id", _extra.get("persona_id", "default"))
            _known.setdefault("category_plugin_id",
                              _extra.get("category_plugin_id", "knowledge_longform"))
            try:
                _req_model = _PR(**_known)
            except Exception as e:
                logger.error("定时管线 payload 非法（%s）: %s", payload.get("name", ""), e)
                return

            async def _run():
                try:
                    from clipwright.api import pipeline as _papi
                    await _papi.run_pipeline_async(_req_model, _stub_req)
                except Exception:
                    logger.exception("定时管线启动失败: %s", payload.get("name", ""))

            _asyncio.get_running_loop().create_task(_run())

        _sched.start(_sched_handler, interval_sec=2.0)
    except Exception as e:
        logger.warning("定时调度器启动失败: %s", e)

    # 工作区素材轮询（PRD workspace-prd-2026-10-24 §5.1）：发现 materials/ 新素材并索引
    try:
        from clipwright.services import workspace_watcher as _ws_watcher
        _ws_watcher.start()
    except Exception as e:
        logger.warning("工作区扫描器启动失败: %s", e)

    yield

    # 清理
    try:
        from clipwright.services import workspace_watcher as _ws_watcher
        _ws_watcher.stop()
    except Exception:
        pass
    try:
        from clipwright.services import scheduler as _sched
        _sched.stop()
    except Exception:
        pass
    # 轮70（D15）：取消仍存活的后台任务（webhook 投递/清理协程等）
    try:
        from clipwright.services.async_util import cancel_all_background
        await cancel_all_background()
    except Exception as e:
        logger.warning("后台任务清理失败: %s", e)
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
    # MV/PV 线的主类型；plan/PRD 一直在用它，但此前没有注册（V35 因此无从判起）
    CategoryRegistry.register(LyricMvPlugin())


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

# 请求体大小上限（防超大 JSON/上传 DoS；分块上传端点自行处理流式上限）
_MAX_BODY_BYTES = 20 * 1024 * 1024
# multipart 文件上传端点（asset/voice 等）自行流式校验大小（2GB/100MB），
# 全局 content-length 限制若对其生效会误拦大文件（如音频/视频素材）。
_MULTIPART_CT = "multipart/form-data"


# 轮 80（红线）：请求 ID + 结构化访问日志——排障时把日志行与请求关联起来。
# 客户端可带 X-Request-ID（如前端生成），否则生成 12 位短 id；响应头回传。
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    import time as _time
    import uuid as _uuid

    rid = request.headers.get("X-Request-ID") or _uuid.uuid4().hex[:12]
    request.state.request_id = rid
    start = _time.perf_counter()
    response = await call_next(request)
    duration_ms = (_time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    logger.info(
        "%s %s -> %s %.1fms rid=%s",
        request.method, request.url.path, response.status_code, duration_ms, rid,
    )
    return response


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        # multipart 文件上传跳过全局上限（由端点流式校验，避免 413 误拦）
        ct = (request.headers.get("content-type") or "").lower()
        is_multipart = ct.startswith(_MULTIPART_CT)
        cl = request.headers.get("content-length")
        if (not is_multipart) and cl and cl.isdigit() and int(cl) > _MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "请求体过大"})
    return await call_next(request)


# 速率限制（注册在鉴权之前 → 执行在鉴权之后，可用 user_id 做键）
from clipwright.services.rate_limit import RateLimiter as _RateLimiter

_rate_limiter = _RateLimiter(settings.rate_limit_window_sec, settings.rate_limit_max_requests)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if not settings.rate_limit_enabled or request.method == "OPTIONS":
        return await call_next(request)
    uid = getattr(request.state, "user_id", None) or ""
    ip = request.client.host if request.client else ""
    key = f"{uid or ip}:{request.method}:{request.url.path[:80]}"
    # 参数随配置热更新（避免实例构造时快照导致开关调整不生效）
    _rate_limiter.window = settings.rate_limit_window_sec
    _rate_limiter.max = settings.rate_limit_max_requests
    if not _rate_limiter.allow(key):
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
    return await call_next(request)


# SSE 短期一次性 token 存储（内存为主 + Mongo 镜像，TTL 120s，单次使用）。
# 轮 80（红线）：内存字典在多 worker / 重启后失效——签发时同步写 Mongo
# `sse_tokens`，消费方内存未命中时回退 Mongo（原子删除保证单次语义）。
_sse_tokens: dict[str, float] = {}
_sse_ttl = 120.0


def _issue_sse_token() -> str:
    import secrets as _secrets
    import time as _time

    tok = _secrets.token_urlsafe(24)
    exp = _time.time() + _sse_ttl
    _sse_tokens[tok] = exp
    try:
        from datetime import datetime, timezone as _tz

        from clipwright.context import mongo
        if mongo.is_connected:
            mongo.db["sse_tokens"].update_one(
                {"token": tok},
                {"$set": {"token": tok, "expires_at": datetime.fromtimestamp(exp, tz=_tz.utc)}},
                upsert=True,
            )
    except Exception:
        logger.warning("SSE token Mongo 镜像写入失败（仅内存生效）")
    return tok


def _consume_sse_token(tok: str) -> bool:
    import time as _time

    if not tok:
        return False
    exp = _sse_tokens.pop(tok, None)
    if exp is not None:
        if _time.time() > exp:
            return False
        return True
    # 内存未命中 → Mongo（多 worker / 进程重启场景），原子删除保证单次使用
    try:
        from clipwright.context import mongo
        if mongo.is_connected:
            doc = mongo.db["sse_tokens"].find_one_and_delete({"token": tok})
            if doc is not None:
                return True
    except Exception:
        logger.warning("SSE token Mongo 消费失败")
    return False


@app.post("/api/auth/sse-token")
async def issue_sse_token(request: Request) -> dict:
    """P0-9: 签发 EventSource 使用的短期一次性 token（需先通过 Bearer 鉴权）。

    开放模式返回空 token——前端可直接建 EventSource；jwt 模式要求有效 JWT 或运维令牌。
    """
    auth_header = request.headers.get("Authorization", "") or ""
    if settings.account_verify_mode == "jwt":
        if settings.account_jwt_secret and _verify_access_jwt(auth_header[7:] if auth_header.startswith("Bearer ") else ""):
            return {"token": _issue_sse_token(), "expires_in": int(_sse_ttl)}
        if settings.api_token and hmac.compare_digest(auth_header, f"Bearer {settings.api_token}"):
            return {"token": _issue_sse_token(), "expires_in": int(_sse_ttl)}
        return JSONResponse(status_code=401, content={"detail": "未授权"})
    if not settings.api_token:
        return {"token": "", "expires_in": 0}
    expected = f"Bearer {settings.api_token}"
    if not hmac.compare_digest(auth_header, expected):
        return JSONResponse(status_code=401, content={"detail": "未授权"})
    return {"token": _issue_sse_token(), "expires_in": int(_sse_ttl)}


def _verify_access_jwt(token: str) -> dict | None:
    """P3-3B: 用共享密钥本地校验 Server 签发的 access JWT。"""
    import jwt as _pyjwt

    if not settings.account_jwt_secret or not token:
        return None
    try:
        payload = _pyjwt.decode(
            token, settings.account_jwt_secret, algorithms=[settings.account_jwt_algorithm]
        )
    except _pyjwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


@app.middleware("http")
async def api_token_auth(request: Request, call_next):
    # 无论鉴权成败，先抹除 query 中的 token（防失败路径 token 进访问日志/Referer）
    if "token" in request.query_params:
        from urllib.parse import urlencode

        qtok = request.query_params.get("token", "")
        remaining = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
        request.scope["query_string"] = urlencode(remaining).encode()
    else:
        qtok = ""

    # 每请求重置身份（无鉴权/off/token 模式保持 None=管理员语义）
    request.state.user_id = None
    request.state.user_role = None

    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    is_api = path.startswith("/api/") and not path.startswith("/api/health")
    # 渲染成片与克隆语音属敏感内容，令牌模式下同样需要鉴权
    is_media = path.startswith(("/renders/", "/voice_audio/"))
    # 运维/调试端点同样受令牌保护（/health 保持开放）
    is_admin = path.startswith("/metrics") or path.startswith("/test")
    if not (is_api or is_media or is_admin):
        return await call_next(request)

    mode = settings.account_verify_mode
    auth_header = request.headers.get("Authorization", "") or ""
    bearer = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    # <video>/<img> 无法携带 Authorization 头——媒体代理端点允许 query token
    # （P1-08：/api/asset/{id}/file 与 by-path 同待遇，素材面板刷新后的预览依赖它）
    is_by_path_media = is_api and request.method == "GET" and (
        path == "/api/asset/by-path"
        or (path.startswith("/api/asset/") and path.endswith("/file"))
    )

    if mode == "jwt":
        # 运维令牌（可选）或 Server JWT 本地验签
        ok = False
        if settings.api_token and hmac.compare_digest(bearer, settings.api_token):
            ok = True
        else:
            payload = _verify_access_jwt(bearer)
            if payload:
                request.state.user_id = payload.get("sub")
                request.state.user_role = payload.get("role")
                ok = True
        if not ok and is_media:
            ok = bool(settings.api_token) and hmac.compare_digest(qtok, settings.api_token)
        if not ok and is_by_path_media:
            ok = (bool(settings.api_token) and hmac.compare_digest(qtok, settings.api_token)) or bool(_verify_access_jwt(qtok))
        if not ok and "/stream" in path:
            ok = _consume_sse_token(qtok)
        if not ok:
            return JSONResponse(
                status_code=401,
                content={"detail": "未授权：缺少或错误的访问令牌"},
            )
        return await call_next(request)

    # off / token 模式：沿用 api_token 语义（未设置 token 时开放）
    if not settings.api_token:
        return await call_next(request)
    expected = f"Bearer {settings.api_token}"
    ok = hmac.compare_digest(bearer, settings.api_token)
    if not ok and is_media:
        # <video>/<audio> 标签无法携带 Authorization 头，允许 query token 校验
        ok = hmac.compare_digest(qtok, settings.api_token)
    if not ok and is_by_path_media:
        # 预览媒体代理同样允许 query token（中间件已在日志前抹除 token）
        ok = hmac.compare_digest(qtok, settings.api_token)
    if not ok and "/stream" in path:
        # SSE 端点（EventSource 无法带请求头）允许短期一次性 token
        ok = _consume_sse_token(qtok)
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


# ── 请求参数校验错误（422）中文化 ──
# FastAPI 默认的 422 detail 是英文的 Pydantic 错误列表，对前端用户不友好。
# 这里取第一个错误映射为中文短语，原始错误列表放在 errors 字段供调试。

_VALIDATION_ERROR_TEMPLATES: dict[str, str] = {
    "int_parsing": "参数格式错误：{loc} 需要是数字",
    "float_parsing": "参数格式错误：{loc} 需要是数字",
    "number_parsing": "参数格式错误：{loc} 需要是数字",
    "missing": "缺少必填参数：{loc}",
    "string_too_short": "参数 {loc} 格式不正确",
    "string_type": "参数 {loc} 格式不正确",
}

# loc 前缀中无意义的位置段（跳过，只保留业务参数名）
_LOC_PREFIXES = ("body", "query", "path", "headers", "cookie")


def _friendly_validation_message(errors: list[dict]) -> str:
    """把第一条 Pydantic 校验错误映射为中文提示。"""
    first = errors[0] if errors else {}
    loc_parts = [
        str(p) for p in first.get("loc", ()) if str(p) not in _LOC_PREFIXES
    ]
    loc = ".".join(loc_parts) or "请求"
    err_type = str(first.get("type", ""))
    template = _VALIDATION_ERROR_TEMPLATES.get(err_type, "参数 {loc} 不合法：{msg}")
    return template.format(loc=loc, msg=first.get("msg", ""))


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """422 校验错误统一返回中文 detail + 原始 errors。"""
    errors = exc.errors()
    return JSONResponse(
        status_code=422,
        content={
            "detail": _friendly_validation_message(errors),
            "errors": jsonable_encoder(errors),
        },
    )


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

# 挂载渲染输出目录（用于预览）；render_output_dir 可配置，默认 renders，
# 锚定到仓库根以与渲染服务的输出目录一致（旧实现用 CWD 相对路径，非仓库根
# CWD 启动时挂载点会指向空目录）
from clipwright.paths import anchor as _anchor_path
renders_path = _anchor_path(settings.render_output_dir)
renders_path.mkdir(parents=True, exist_ok=True)
app.mount("/renders", StaticFiles(directory=str(renders_path)), name="renders")

# 挂载 TTS 输出目录
tts_output_path = Path(settings.tts_output_dir)
tts_output_path.mkdir(parents=True, exist_ok=True)
app.mount("/voice_audio", StaticFiles(directory=str(tts_output_path)), name="voice_audio")

# 挂载 PV 引擎产物目录（抽帧预览 / 成片）：pv_engine 插件按
# PluginData/pv_engine/<job_id>/... 落盘，这里给出可直接访问的 URL
# （否则 Agent 只能回绝对路径，前端无法显示）。
_pv_engine_path = _anchor_path(settings.plugin_data_dir) / "pv_engine"
_pv_engine_path.mkdir(parents=True, exist_ok=True)
app.mount("/pv-engine", StaticFiles(directory=str(_pv_engine_path)), name="pv-engine")

# 注册路由
app.include_router(pipeline_api.router)
app.include_router(supervisor_api.router)
app.include_router(supervisor_api.admin_router)
app.include_router(persona_api.router)
app.include_router(persona_forge_api.router)
app.include_router(chat_forge_api.router)
app.include_router(rag_api.router)
app.include_router(model_probe_api.router)
app.include_router(settings_api.router)
app.include_router(providers_api.router)
app.include_router(render_api.router)
app.include_router(tool_api.router)
app.include_router(tool_surface_api.router)
app.include_router(task_api.router)
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
app.include_router(market_api.router)
app.include_router(stats_api.router)
app.include_router(requirements_api.router)
app.include_router(versions_api.router)
app.include_router(scheduler_api.router)
app.include_router(workspace_api.router)


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

    # Mongo 断连必须计入不健康（作为就绪探针时不能假绿）。
    #
    # 判定只允许"健康状态字段"参与：
    #  · `service` 是**名字**（"clipwright-engine"）、`queue_running`/`queue_pending`
    #    是**计数**——它们恒不等于 "ok"，混进来会让 all_ok 永远为 False。
    #    实测：/health **永远返回 degraded**，即使 Mongo/LLM/ffmpeg/Hyperframes
    #    全部为 ok。而 docker-compose.yml 的健康检查正是判 `status == 'ok'`
    #    → **容器永远 unhealthy**，真故障时也分辨不出来。
    #  · `"disconnected"` 原先也在豁免名单里，与上一行注释**自相矛盾**
    #    （注释说断连必须计入、代码却把它排除）。那正是"Mongo 掉了但探针仍绿"
    #    的来源，属于假绿，不能留。豁免只保留 `no_key`（LLM 未配置是**功能取舍**，
    #    不影响服务能用）与 `checking`（冷启动探测中，不是结论）。
    _non_status_keys = ("service", "queue_running", "queue_pending",
                        "queue", "status")
    all_ok = all(
        v == "ok" for k, v in components.items()
        if k not in _non_status_keys and v not in ("no_key", "checking")
    )
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


def _enforce_security_gate() -> None:
    """发布红线（fail-closed）：绑定非回环地址且未配置任何鉴权时拒绝启动。

    满足其一即放行：回环地址 / 已配置 api_token 或 account_verify_mode != off /
    显式 CLIPWRIGHT_ALLOW_INSECURE=true（自认风险的受控本地/内网环境）。
    """
    host = (settings.host or "").strip().lower()
    loopback = host in ("127.0.0.1", "localhost", "::1", "")
    authed = bool(settings.api_token) or settings.account_verify_mode in ("token", "jwt")
    if loopback or authed or settings.allow_insecure:
        return
    raise SystemExit(
        "\n[Clipwright] 拒绝启动：绑定非回环地址 %r 且未配置任何鉴权"
        "（无 CLIPWRIGHT_API_TOKEN 且 CLIPWRIGHT_ACCOUNT_VERIFY_MODE=off），"
        "全部 API 将对外裸奔。\n"
        "  1) 生产：设置 CLIPWRIGHT_API_TOKEN（或 CLIPWRIGHT_ACCOUNT_VERIFY_MODE=token|jwt）\n"
        "  2) 受控本地/内网：显式设置 CLIPWRIGHT_ALLOW_INSECURE=true 自认风险\n" % settings.host
    )


def _main() -> None:
    """入口：按 env 配置启动 uvicorn（CLIPWRIGHT_HOST / CLIPWRIGHT_PORT /
    CLIPWRIGHT_DEBUG，默认 0.0.0.0:8080）。用法：``python -m clipwright.main``。
    """
    import uvicorn

    _enforce_security_gate()
    uvicorn.run(
        "clipwright.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )


if __name__ == "__main__":
    _main()
