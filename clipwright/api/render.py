"""渲染 API — 提交、查询渲染任务。"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from clipwright.config import TIME_ZONE, logger, settings
from clipwright.paths import anchor
from clipwright.schema.timeline import Timeline
from clipwright.security import is_safe_download_name
from clipwright.services.async_util import spawn_background
from clipwright.services.remote_render import RemoteRenderService
from clipwright.services.render import RenderService
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/render", tags=["render"])

# 渲染队列
_render_queue: dict[str, dict] = {}
# 渲染幂等键（Idempotency-Key → task_id）
_idempotency_render: dict[str, str] = {}


# ── P5-B4: 渲染队列持久化（Mongo render_tasks；不可用静默降级为纯内存）──

def _persist_render_task(doc: dict) -> None:
    try:
        from clipwright.context import mongo
        if mongo.is_connected:
            mongo.db["render_tasks"].update_one(
                {"task_id": doc["task_id"]}, {"$set": doc}, upsert=True
            )
    except Exception:
        pass


def _drop_render_task(task_id: str) -> None:
    try:
        from clipwright.context import mongo
        if mongo.is_connected:
            mongo.db["render_tasks"].delete_one({"task_id": task_id})
    except Exception:
        pass


def _load_recovered_render_tasks() -> list[dict]:
    """重启恢复：Mongo 中 queued/rendering 且内存已无的任务。"""
    try:
        from clipwright.context import mongo
        if not mongo.is_connected:
            return []
        docs = list(mongo.db["render_tasks"].find(
            {"status": {"$in": ["queued", "rendering"]}}
        ))
        for d in docs:
            d.pop("_id", None)
        return docs
    except Exception:
        return []


def _renders_dir() -> Path:
    """渲染输出目录（锚定到包父目录，避免 CWD 依赖）。"""
    return anchor("renders")


@router.delete("/artifacts")
async def purge_render_artifacts(request: Request, days: int = 30) -> dict:
    """批8a：清理过期渲染产物——删除 renders/ 中 mtime 超过 days 天的文件。

    旧实现渲染产物无限堆积。jwt 模式仅管理员可调用；off 模式放行（本地开发）。
    """
    from clipwright.authz import current_user_id, is_admin

    uid = current_user_id(request)
    if uid is not None and not is_admin(request):
        from clipwright.config import settings as _settings
        if _settings.account_verify_mode != "off":
            raise HTTPException(status_code=403, detail="仅管理员可清理渲染产物")
    days = max(1, min(int(days), 365))
    rd = _renders_dir()
    removed = 0
    import time as _time
    now = _time.time()
    if rd.exists():
        for f in rd.iterdir():
            try:
                if f.is_file() and (now - f.stat().st_mtime) > days * 86400:
                    f.unlink()
                    removed += 1
            except Exception:
                continue
    from clipwright import audit
    audit.record("render_artifacts_purge", uid or "", {"days": days, "removed": removed})
    return {"status": "ok", "removed": removed, "older_than_days": days}


# 错误信息脱敏 — 抹除服务器绝对路径
import re as _re

_PATH_LEAK_RE = _re.compile(r"[A-Za-z]:[\\/][^\s\"']*|/(?:home|usr|app|var|etc|tmp)[^\s\"']*")


def _sanitize_detail(msg: str) -> str:
    """移除错误信息中的服务器路径（Windows 盘符路径与常见 Unix 绝对路径）。"""
    if not msg:
        return ""
    return _PATH_LEAK_RE.sub("<path>", str(msg))[:1000]


def _validate_render_inputs(tl: Timeline, audio_file_path: str, bgm_file_path: str) -> None:
    """P0-2: 渲染入参白名单校验 — timeline 内所有本地媒体路径与音频/BGM 路径必须位于白名单目录。

    未通过抛 HTTPException(400)。URL 源（http/https）跳过；相对路径且文件不存在时跳过
    （不构成文件访问，交由渲染层报错）。
    """
    from clipwright.security import SecurityViolation, assert_allowed_path

    def _check(label: str, value: str) -> None:
        if not value:
            return
        if value.startswith(("http://", "https://")):
            return
        p = Path(value)
        if not (p.is_absolute() or p.exists()):
            return
        # 相对路径（如 _cache/tmp/textvid_*.mp4）按 cwd 解析为绝对路径后再校验
        if not p.is_absolute():
            p = p.resolve()
        try:
            assert_allowed_path(p)
        except SecurityViolation as e:
            raise HTTPException(status_code=400, detail=f"{label} 不在白名单目录: {e}") from e

    _check("audio_file_path", audio_file_path)
    _check("bgm_file_path", bgm_file_path)
    for track in tl.tracks or []:
        for clip in track.clips or []:
            aid = getattr(clip, "asset_id", "") or ""
            _check("clip.asset_id", str(aid))
            meta = getattr(clip, "metadata", {}) or {}
            if isinstance(meta, dict):
                lp = meta.get("local_path")
                if lp:
                    _check("clip.metadata.local_path", str(lp))


def _new_render_service():
    """R2: 每任务新建服务实例——本地 RenderService 携带唯一 work_dir 与可变状态
    （_final_ffmpeg_log/_fallback_count/固定中间文件名），单例 + 并发渲染互踩
    （aud.mp4/ov.mp4 等固定名互写、finally rmtree 删掉对方工作目录）。
    远程服务无本地状态，保持单例复用。

    B-3 / P2-2（渲染后端选择，2026-09-13 接线完成）：
    `settings.renderer_backend` 走**统一贡献注册表**（`providers/render/`）解析，
    因此除了内建 local/remote，**插件贡献的渲染后端**（如 `
    @hyperframes/producer` 常驻 sidecar）也能被选中；未注册/未知名称回退本机。
    历史配置 `remote_render_url` 非空时仍按 remote 处理（缺省行为不变）。
    """
    from clipwright.providers.render import render_backend_names, resolve_render_backend

    requested = str(getattr(settings, "renderer_backend", "") or "local").strip().lower()
    if requested not in ("", "local"):
        backend = resolve_render_backend(requested)
        if backend is None:
            logger.warning("renderer_backend=%s 未注册（可用: %s），回退本机渲染",
                           requested, ", ".join(render_backend_names()) or "无")
        else:
            logger.info("渲染服务选择: 贡献注册表后端 %s", requested)
            return _backend_as_service(backend)
    if (settings.remote_render_url or "").strip():
        logger.info("渲染服务选择: RemoteRenderService (remote=%s)", settings.remote_render_url)
        return RemoteRenderService()
    return RenderService()


def _backend_as_service(backend: object):
    """把渲染后端适配成**既有调用方认得的服务对象**。

    内建后端本身包装了服务实例（`LocalFFmpegBackend._svc()`），直接取出即可；
    插件贡献的后端可能只实现 `RendererBackend.render(timeline, output, settings, ...)`
    协议（与 `RenderService.render` 的关键字参数不同），这时用 `_BackendServiceAdapter`
    把 `render(**kwargs)` 调用翻译成后端协议——调用方（`/api/render/start` 等）无需感知。
    """
    get_service = getattr(backend, "_svc", None)
    if callable(get_service):
        try:
            return get_service()
        except Exception:  # noqa: BLE001 - 取不到就按协议适配
            pass
    if hasattr(backend, "render"):
        return _BackendServiceAdapter(backend)
    logger.warning("渲染后端 %r 既无服务实例也不实现 render()，回退本机渲染", backend)
    return RenderService()


class _BackendServiceAdapter:
    """把 `RendererBackend` 协议适配成 `RenderService` 风格（关键字参数收口）。

    只做两件事：① 把 `render(timeline, output, **kwargs)` 的 kwargs 收进 `settings`
    字典（协议约定），② 把 `progress_callback` 映射成 `on_progress`。
    后端返回的对象原样透传（调用方只看 `.success/.output_path/.warnings/.duration_sec`）。
    """

    def __init__(self, backend: object) -> None:
        self._backend = backend
        self.name = getattr(backend, "name", "plugin")

    async def render(self, timeline, output_path, **kwargs):
        settings_dict = dict(kwargs)
        on_progress = settings_dict.pop("progress_callback", None)
        return await self._backend.render(  # type: ignore[attr-defined]
            timeline, str(output_path), settings_dict,
            on_progress=on_progress,
        )


def _ensure_disk_space(min_free_gb: float = 1.0) -> None:
    """轮 86（出片闭环）：渲染前磁盘预检——空间不足快速失败，不再让
    ffmpeg 写满磁盘后以晦涩的 stderr 尾巴暴露。探测失败不阻断。"""
    import shutil as _shutil

    try:
        usage = _shutil.disk_usage(str(_renders_dir()))
        free_gb = usage.free / 1e9
        if free_gb < min_free_gb:
            raise HTTPException(
                status_code=507,
                detail=f"磁盘剩余空间不足（{free_gb:.1f}GB < {min_free_gb}GB），请清理后再渲染",
            )
    except HTTPException:
        raise
    except Exception:
        pass


async def enqueue_auto_render(
    pipeline_id: str,
    timeline,
    owner_id: str = "",
    preset: str = "",
    output_name: str = "",
    audio_file_path: str = "",
    bgm_file_path: str = "",
    max_retries: int = 1,
) -> dict:
    """轮 86（出片闭环）：管线完成后自动入队渲染（extra_params.auto_render=true）。

    复用 queue 端点的任务语义（内存队列 + Mongo 持久化 + 后台执行），
    失败自动重试 max_retries 次。返回 {task_id, status, output_path}。
    """
    from clipwright import audit

    _ensure_disk_space()
    task_id = f"render_{uuid.uuid4().hex[:12]}"
    _render_queue[task_id] = {
        "status": "queued", "progress": 0, "result": None,
        "owner_id": owner_id or "", "priority": 2,
        "auto": True, "pipeline_id": pipeline_id,
    }
    audit.record("render_auto_queue", owner_id, {"pipeline_id": pipeline_id, "task_id": task_id})

    body_settings = RenderSettings(preset=preset) if preset else RenderSettings()
    params = _resolve_settings(body_settings)
    tl = timeline if isinstance(timeline, Timeline) else Timeline(**(timeline or {}))
    _validate_render_inputs(tl, audio_file_path, bgm_file_path)
    out_ext = str(params.get("container_ext") or "mp4")
    if output_name and is_safe_download_name(output_name):
        out = f"renders/{Path(output_name).name}"
    else:
        out = f"renders/auto_{pipeline_id}.{out_ext}" if pipeline_id else f"renders/{task_id}.{out_ext}"

    try:
        _persist_render_task({
            "task_id": task_id, "status": "queued", "progress": 0,
            "output_path": out, "owner_id": owner_id or "", "priority": 2,
            "timeline": tl.model_dump(mode="json"),
            "audio_file_path": audio_file_path, "bgm_file_path": bgm_file_path,
            "settings": params,
            "created_at": datetime.now(tz=TIME_ZONE),
            "updated_at": datetime.now(tz=TIME_ZONE),
        })
    except Exception as e:
        logger.warning("自动渲染任务持久化失败（降级内存）: %s", e)

    async def _run():
        attempt = 0
        while True:
            _render_queue[task_id]["status"] = "rendering"
            _render_queue[task_id]["progress"] = 0
            try:
                async def on_progress(phase: str, pct: float, detail: str = ""):
                    _render_queue[task_id]["progress"] = min(int(pct), 99)
                    _render_queue[task_id]["phase"] = phase
                    _render_queue[task_id]["detail"] = detail

                result = await _new_render_service().render(
                    tl, out,
                    width=params["width"], height=params["height"],
                    fps=params["fps"], bitrate=params["bitrate"],
                    audio_bitrate=params["audio_bitrate"],
                    audio_file_path=audio_file_path,
                    bgm_file_path=bgm_file_path,
                    progress_callback=on_progress,
                    cancel_id=task_id,
                    encoder_override=params.get("encoder", ""),
                    pix_fmt_override=params.get("pix_fmt", ""),
                    loudnorm_enabled=bool(params.get("loudnorm", True)),
                )
                if not result.success:
                    raise RuntimeError(result.error or "render failed")
                _render_queue[task_id]["result"] = result.to_dict()
                _render_queue[task_id]["status"] = "completed"
                _render_queue[task_id]["progress"] = 100
                _render_queue[task_id]["output_path"] = str(out)
                try:
                    from clipwright.api.webhook import dispatch_event_bg
                    dispatch_event_bg("render.completed", {
                        "task_id": task_id, "output_path": str(out),
                        "success": True, "pipeline_id": pipeline_id,
                    })
                except Exception as we:
                    logger.warning("render webhook dispatch failed: %s", we)
                break
            except Exception as e:
                from clipwright.services.render import RenderCancelledError
                if isinstance(e, RenderCancelledError):
                    _render_queue[task_id]["status"] = "cancelled"
                    _render_queue[task_id]["result"] = {"error": "渲染已取消"}
                    break
                attempt += 1
                if attempt > max_retries:
                    _render_queue[task_id]["status"] = "failed"
                    _render_queue[task_id]["result"] = {"error": _sanitize_detail(str(e))}
                    try:
                        from clipwright.api.webhook import dispatch_event_bg
                        dispatch_event_bg("render.failed", {
                            "task_id": task_id, "pipeline_id": pipeline_id,
                            "error": _sanitize_detail(str(e))[:300],
                        })
                    except Exception as we:
                        logger.warning("render webhook dispatch failed: %s", we)
                    break
                logger.warning("自动渲染失败（重试 %d/%d）: %s",
                               attempt, max_retries, str(e)[:200])
                await asyncio.sleep(3.0)
        # 终态持久化 + 清理（与 queue 端点一致）
        try:
            _persist_render_task({
                "task_id": task_id,
                "status": _render_queue[task_id]["status"],
                "progress": _render_queue[task_id].get("progress", 0),
                "result": _render_queue[task_id].get("result"),
                "output_path": _render_queue[task_id].get("output_path", ""),
                "owner_id": _render_queue[task_id].get("owner_id", ""),
                "updated_at": datetime.now(tz=TIME_ZONE),
            })
        except Exception:
            pass

        async def _cleanup():
            await asyncio.sleep(60)
            _render_queue.pop(task_id, None)
            _drop_render_task(task_id)
        spawn_background(_cleanup(), name=f"render-auto-cleanup-{task_id}")

    spawn_background(_run(), name=f"render-auto-{task_id}")
    return {"task_id": task_id, "status": "queued", "output_path": out, "auto": True}


@router.post("/queue")
async def queue_render(body: RenderRequest, request: Request) -> dict:
    """将渲染任务加入队列，立即返回任务 ID，后台异步执行。"""
    from clipwright import audit
    from clipwright.authz import current_user_id

    uid = current_user_id(request)
    # 轮 86（出片闭环）：渲染前磁盘预检（空间不足 507 快速失败）
    _ensure_disk_space()
    # 幂等键去重
    idem_key = request.headers.get("Idempotency-Key", "")
    if idem_key:
        existing = _idempotency_render.get(idem_key)
        if existing and existing in _render_queue:
            return {"task_id": existing, "status": "deduplicated"}

    task_id = f"render_{uuid.uuid4().hex[:12]}"
    # 优先级（X-Priority: 1-5，默认 3）
    try:
        priority = max(1, min(5, int(request.headers.get("X-Priority", "3") or "3")))
    except (TypeError, ValueError):
        priority = 3
    _render_queue[task_id] = {
        "status": "queued",
        "progress": 0,
        "result": None,
        "owner_id": uid or "",  # P3-3B
        "priority": priority,
    }
    if idem_key:
        _idempotency_render[idem_key] = task_id
    audit.record("render_queue", uid, {"task_id": task_id, "output_path": body.output_path or ""})

    try:
        params = _resolve_settings(body.settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    tl = body.timeline
    # 入参白名单（timeline 媒体路径 + 音频/BGM）
    _validate_render_inputs(tl, body.audio_file_path or "", body.bgm_file_path or "")
    # 使用请求 output_path 的 basename（防御：仅取 basename 拼到 renders/，白名单校验）。
    # 先对原始串做 is_safe_download_name 校验（拒绝路径分隔符 / \\、.. 与 Windows 非法字符），
    # 再取 basename 拼接到 renders/ 目录，杜绝路径穿越。
    raw_output = body.output_path or ""
    if raw_output and not is_safe_download_name(raw_output):
        raise HTTPException(status_code=400, detail="非法输出文件名")
    requested_name = Path(raw_output).name if raw_output else ""
    # 预设携带容器扩展名（prores→mov）；用户显式指定文件名时尊重用户
    _ext = str(params.get("container_ext") or "mp4")
    out = f"renders/{requested_name}" if requested_name else f"renders/{task_id}.{_ext}"

    # 软字幕——从时间线字幕轨生成 SRT sidecar（供 render 封装 mov_text 轨）
    _srt_path = ""
    if params.get("soft_subtitle"):
        try:
            from pathlib import Path as _P
            from clipwright.services.subtitle import timeline_clips_to_segments, to_srt
            _cap_clips = []
            for _t in tl.tracks or []:
                if str(_t.kind) == "caption":
                    _cap_clips.extend(c.model_dump(mode="json") for c in (_t.clips or []))
            if _cap_clips:
                _srt_path = str(_P(tempfile.gettempdir()) / f"{task_id}_subs.srt")
                _P(_srt_path).write_text(
                    to_srt(timeline_clips_to_segments(_cap_clips)), encoding="utf-8")
        except Exception as e:
            logger.warning("软字幕 SRT 生成失败（回退烧入）: %s", e)
            _srt_path = ""

    # 持久化任务（含 timeline，供重启恢复/中断重试）
    try:
        _persist_render_task({
            "task_id": task_id,
            "status": "queued",
            "progress": 0,
            "output_path": out,
            "owner_id": uid or "",
            "priority": priority,
            "timeline": tl.model_dump(mode="json"),
            "audio_file_path": body.audio_file_path or "",
            "bgm_file_path": body.bgm_file_path or "",
            "settings": params,
            "created_at": datetime.now(tz=TIME_ZONE),
            "updated_at": datetime.now(tz=TIME_ZONE),
        })
    except Exception as e:
        logger.warning("渲染任务持久化失败（降级内存）: %s", e)

    async def _run():
        # 优先级生效——低优先级任务在高优任务排队时让位（原实现 spawn 全
        # 并发，priority 仅用于列表排序）。等待有界（30 分钟）防饥饿。
        import time as _time
        _wait_started = _time.monotonic()
        while priority < 5:
            _has_higher_queued = any(
                t.get("status") == "queued" and int(t.get("priority", 3)) > priority
                for t in list(_render_queue.values())
            )
            _has_running = any(
                t.get("status") == "rendering" for t in list(_render_queue.values())
            )
            if not (_has_higher_queued and _has_running):
                break
            if _time.monotonic() - _wait_started > 1800:
                break
            await asyncio.sleep(2.0)
        # 取消的排队任务不进入渲染（状态保持 cancelled）
        if _render_queue[task_id].get("status") == "cancelled":
            return
        _render_queue[task_id]["status"] = "rendering"
        _render_queue[task_id]["progress"] = 0
        # clip_count 改为片段总数（此前用轨道数，语义错误）
        _render_queue[task_id]["clip_count"] = sum(len(t.clips or []) for t in tl.tracks or [])
        # 插件 hook — 渲染前置
        try:
            from clipwright.plugins.hooks import HookPoint
            from clipwright.plugins.hooks_async import execute_async
            await execute_async(HookPoint.PRE_RENDER, {"task_id": task_id})
        except Exception as e:
            logger.warning("pre_render hook 执行失败: %s", e)
        try:
            # 分阶段进度更新回调
            async def on_progress(phase: str, pct: float, detail: str = ""):
                _render_queue[task_id]["progress"] = min(int(pct), 99)
                _render_queue[task_id]["phase"] = phase
                _render_queue[task_id]["detail"] = detail

            result = await _new_render_service().render(
                tl, out,
                width=params["width"], height=params["height"],
                fps=params["fps"], bitrate=params["bitrate"],
                audio_bitrate=params["audio_bitrate"],
                audio_file_path=body.audio_file_path or "",
                bgm_file_path=body.bgm_file_path or "",
                progress_callback=on_progress,
                cancel_id=task_id,
                encoder_override=params.get("encoder", ""),
                pix_fmt_override=params.get("pix_fmt", ""),
                soft_subtitle_srt=_srt_path,  # D4
                loudnorm_enabled=bool(params.get("loudnorm", True)),
            )
            # SRT sidecar 用后即删（此前永久遗留 temp 目录）
            if _srt_path:
                try:
                    Path(_srt_path).unlink(missing_ok=True)
                except Exception:
                    pass
            _render_queue[task_id]["result"] = result.to_dict()
            _render_queue[task_id]["status"] = "completed" if result.success else "failed"
            _render_queue[task_id]["progress"] = 100
            _render_queue[task_id]["output_path"] = str(out)
            # 插件 hook — 渲染后置
            try:
                from clipwright.plugins.hooks import HookPoint
                from clipwright.plugins.hooks_async import execute_async
                await execute_async(HookPoint.POST_RENDER, {
                    "task_id": task_id, "output_path": str(out),
                    "success": result.success,
                })
            except Exception as e:
                logger.warning("post_render hook 执行失败: %s", e)
            # 6（轮 81 修复）：平台封面提取真实现——旧代码引用未定义符号
            # PLATFORM_EXPORT_COUNTS / _extract_platform_covers，NameError 被
            # except 吞掉，cover_paths 永为空。
            try:
                if result.success and Path(out).exists():
                    preset_name = str(body.settings.preset) if (body.settings and body.settings.preset) else ""
                    cover_count = _PLATFORM_EXPORT_COUNTS.get(preset_name)
                    if cover_count:
                        covers = await asyncio.to_thread(
                            _extract_platform_covers, str(out), cover_count)
                        if covers:
                            _render_queue[task_id]["cover_paths"] = covers
                            logger.info("平台封面已提取 %d 张: %s", len(covers), covers)
            except Exception as e:
                logger.exception("平台封面提取失败（不影响成片）: %s", e)
            # webhook 事件接线 — 渲染完成/失败通知
            try:
                from clipwright.api.webhook import dispatch_event_bg
                dispatch_event_bg(
                    "render.completed" if result.success else "render.failed",
                    {
                        "task_id": task_id,
                        "output_path": str(out),
                        "success": result.success,
                        "warnings": result.warnings or [],
                    },
                )
            except Exception as we:
                logger.warning("render webhook dispatch failed: %s", we)
        except Exception as e:
            from clipwright.services.render import RenderCancelledError
            if isinstance(e, RenderCancelledError):
                # 审计 P0 修复：用户取消 → 独立终态 cancelled
                _render_queue[task_id]["status"] = "cancelled"
                _render_queue[task_id]["result"] = {"error": "渲染已取消"}
            else:
                _render_queue[task_id]["status"] = "failed"
                _render_queue[task_id]["result"] = {"error": _sanitize_detail(str(e))}
            try:
                from clipwright.api.webhook import dispatch_event_bg
                dispatch_event_bg("render.failed", {
                    "task_id": task_id,
                    "error": _sanitize_detail(str(e))[:300],
                })
            except Exception as we:
                logger.warning("render webhook dispatch failed: %s", we)
        finally:
            # 终态同步 Mongo（含中断标记供重启恢复）
            try:
                _persist_render_task({
                    "task_id": task_id,
                    "status": _render_queue[task_id]["status"],
                    "progress": _render_queue[task_id].get("progress", 0),
                    "result": _render_queue[task_id].get("result"),
                    "output_path": _render_queue[task_id].get("output_path", ""),
                    "owner_id": _render_queue[task_id].get("owner_id", ""),
                    "updated_at": datetime.now(tz=TIME_ZONE),
                })
            except Exception:
                pass
            # 60s 后清理
            async def _cleanup():
                await asyncio.sleep(60)
                _render_queue.pop(task_id, None)
                _drop_render_task(task_id)  # P5-B4: 终态已同步，清理落库记录
                for k, v in list(_idempotency_render.items()):
                    if v == task_id:
                        _idempotency_render.pop(k, None)
            spawn_background(_cleanup(), name=f"render-cleanup-{task_id}")

    spawn_background(_run(), name=f"render-{task_id}")
    return {"task_id": task_id, "status": "queued", "output": out}


@router.get("/queue/{task_id}")
async def get_queue_status(task_id: str, request: Request) -> dict:
    """查询渲染队列任务状态（P3-3B: 校验所有权；P5-B4: 内存缺失时查 Mongo 恢复）。"""
    import asyncio as _asyncio

    from clipwright.authz import enforce_owner

    task = _render_queue.get(task_id)
    if task is None:
        def _load():
            try:
                from clipwright.context import mongo
                if mongo.is_connected:
                    d = mongo.db["render_tasks"].find_one({"task_id": task_id})
                    if d:
                        d.pop("_id", None)
                    return d
            except Exception:
                pass
            return None
        recovered = await _asyncio.to_thread(_load)
        if recovered is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        enforce_owner(request, recovered.get("owner_id"), "渲染任务")
        return {"task_id": task_id, **recovered, "recovered": True}
    enforce_owner(request, task.get("owner_id"), "渲染任务")
    return {"task_id": task_id, **task}


@router.delete("/queue/{task_id}")
async def cancel_queue_render(task_id: str, request: Request) -> dict:
    """审计 P0 修复：取消渲染任务 — terminate 活跃 ffmpeg 子进程并置 cancelled 终态。"""
    from clipwright.authz import enforce_owner
    from clipwright.services.render import cancel_render

    task = _render_queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    enforce_owner(request, task.get("owner_id"), "渲染任务")
    if task.get("status") in ("completed", "failed", "cancelled"):
        return {"task_id": task_id, "status": task.get("status"), "cancelled": False}
    cancel_render(task_id)
    task["status"] = "cancelled"
    task["result"] = {"error": "渲染已取消"}
    try:
        _persist_render_task({
            "task_id": task_id,
            "status": "cancelled",
            "progress": task.get("progress", 0),
            "result": task.get("result"),
            "output_path": task.get("output_path", ""),
            "owner_id": task.get("owner_id", ""),
            "updated_at": datetime.now(tz=TIME_ZONE),
        })
    except Exception:
        pass
    return {"task_id": task_id, "status": "cancelled", "cancelled": True}


@router.get("/queue/stream/{task_id}")
async def stream_render_progress(task_id: str, request: Request):
    """SSE 流：实时推送渲染进度。

    批4：补 owner 校验（旧实现任何用户可订阅任意任务）；内存队列缺失时
    回退 Mongo 终态（对齐 GET /queue/{task_id}）；事件循环内检测断连。
    """
    from fastapi.responses import StreamingResponse
    import asyncio

    from clipwright.authz import enforce_owner

    def _load_mongo():
        try:
            from clipwright.context import mongo
            if mongo.is_connected:
                d = mongo.db["render_tasks"].find_one({"task_id": task_id})
                if d:
                    d.pop("_id", None)
                return d
        except Exception:
            pass
        return None

    task = _render_queue.get(task_id)
    recovered = None
    if task is None:
        recovered = await asyncio.to_thread(_load_mongo)
    owner = (task or recovered or {}).get("owner_id", "")
    if owner:
        enforce_owner(request, owner, "渲染任务")

    async def event_stream():
        last_status = ""
        last_detail = ""
        last_progress = -1
        # 生产加固 1.5: 终态驱动 + 2h 墙钟上限；批4：断连检测 + 变化检测 +
        # Mongo 终态回退（旧实现 0.5s 盲推 20 小时且清理后立即断流）。
        import time as _time
        _start = _time.time()
        while _time.time() - _start < 72000:
            if await request.is_disconnected():
                return
            task = _render_queue.get(task_id)
            if task is None:
                rec = await asyncio.to_thread(_load_mongo)
                if rec and rec.get("status") in ("completed", "failed", "cancelled"):
                    payload = json.dumps({
                        'type': rec["status"], 'task_id': task_id,
                        'result': rec.get("result"),
                        'output_path': rec.get("output_path", ''),
                    })
                    yield f"data: {payload}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Task not found'})}\n\n"
                return
            status = task["status"]
            progress = task.get("progress", 0)
            detail = task.get("detail", "")
            if status != last_status or progress != last_progress or detail != last_detail:
                last_status = status
                last_progress = progress
                last_detail = detail
                yield f"data: {json.dumps({
                    'type': 'progress',
                    'task_id': task_id,
                    'status': status,
                    'progress': progress,
                    'phase': task.get('phase', ''),
                    'detail': detail,
                    'clip_count': task.get('clip_count', 0),
                    'current_clip': task.get('current_clip', 0),
                })}\n\n"
            if status in ("completed", "failed", "cancelled"):
                payload = json.dumps({
                    'type': status, 'task_id': task_id,
                    'result': task.get('result'),
                    'output_path': task.get('output_path', ''),
                })
                yield f"data: {payload}\n\n"
                return
            await asyncio.sleep(0.5)
        yield f"data: {json.dumps({'type': 'timeout'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


@router.get("/queue")
async def list_queue(request: Request) -> dict:
    """列出所有队列任务（P3-3B: 按 owner 过滤；P5-B4: 合并 Mongo 恢复项）。"""
    from clipwright.authz import filter_by_owner

    tasks = []
    for tid, info in _render_queue.items():
        tasks.append({"task_id": tid, "status": info["status"],
                      "progress": info.get("progress", 0), "owner_id": info.get("owner_id", ""),
                      "priority": info.get("priority", 3)})
    memory_ids = {t["task_id"] for t in tasks}
    # 重启恢复：Mongo 中 queued/rendering 且内存已无 → 标记 recovered（前端可提示中断重试）
    recovered = await asyncio.to_thread(_load_recovered_render_tasks)
    for d in recovered:
        if d.get("task_id") not in memory_ids:
            tasks.append({
                "task_id": d["task_id"],
                "status": d.get("status", "queued"),
                "progress": d.get("progress", 0),
                "owner_id": d.get("owner_id", ""),
                "recovered": True,
            })
    tasks = filter_by_owner(request, tasks)
    tasks.sort(key=lambda t: (-t.get("priority", 3), t["task_id"]), reverse=True)
    return {"tasks": tasks}


def _enforce_media_path_owner(request: Request, src: Path) -> None:
    """轮 80（红线）：jwt 模式下校验媒体路径归属。

    - `projects/{project_id}/...` → 项目 owner；
    - `renders/...` → 渲染任务 owner；
    - 其余白名单目录（library/PluginData 等，本就服务全局共享）放行。
    off/token 模式与 admin 放行。
    """
    from clipwright.authz import current_user_id, is_admin

    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return
    try:
        parts = src.resolve().parts
    except OSError:
        return
    renders_root = _renders_dir().resolve()
    if src.resolve().is_relative_to(renders_root):
        _enforce_render_file_owner(request, src.name)
        return
    projects_root = anchor("projects").resolve()
    if src.resolve().is_relative_to(projects_root):
        rel = src.resolve().relative_to(projects_root)
        project_id = rel.parts[0] if rel.parts else ""
        from clipwright.services.project_manager import ProjectManager
        try:
            data = ProjectManager().load(project_id)
        except ValueError:
            data = None
        owner = (data or {}).get("owner_id") or ""
        if owner != uid:
            raise HTTPException(status_code=403, detail="无权访问该项目下的媒体文件")


@router.get("/video")
async def serve_video(path: str, request: Request):
    """代理视频文件供前端预览（浏览器不能直接加载 file:// 路径）。

    安全：仅允许访问白名单目录内的文件，防止任意文件读取；jwt 模式下
    额外校验 projects/renders 归属。
    """
    from clipwright.security import assert_allowed_path

    src = Path(path)
    assert_allowed_path(src)
    _enforce_media_path_owner(request, src)
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(src), media_type=_media_type_for(src.name),
                        headers={"Accept-Ranges": "bytes"})


def _media_type_for(name: str) -> str:
    """按扩展名返回媒体类型（ProRes .mov 等交付物此前被误标 video/mp4）。"""
    return {".mov": "video/quicktime", ".webm": "video/webm",
            ".mkv": "video/x-matroska"}.get(Path(name).suffix.lower(), "video/mp4")


# 6（轮 81）：平台预设 → 封面抽取张数（成片完成后自动抽帧，供发布选封面）
_PLATFORM_EXPORT_COUNTS: dict[str, int] = {
    "tiktok": 3, "douyin": 3, "weibo": 3,
    "bilibili": 4, "youtube": 4,
}


def _extract_platform_covers(video_path: str, count: int) -> list[str]:
    """从成片均匀抽取 count 张封面帧到 renders/covers/，返回可用路径列表。

    轮 81 真实现（此前是未定义符号，调用即 NameError 被吞）。
    """
    import subprocess as _sp

    src = Path(video_path)
    try:
        probe = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0.0
    except Exception:
        duration = 0.0
    if duration <= 0 or count <= 0:
        return []
    covers_dir = _renders_dir() / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[str] = []
    offsets = [duration * (i + 1) / (count + 1) for i in range(count)]
    for i, off in enumerate(offsets):
        out = covers_dir / f"{src.stem}_cover_{i}.jpg"
        try:
            r = _sp.run(
                ["ffmpeg", "-y", "-ss", f"{off:.2f}", "-i", str(src),
                 "-vframes", "1", "-q:v", "3", str(out)],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
                out_paths.append(str(out))
        except Exception:
            continue
    return out_paths


def _resolve_render_task_for_file(filename: str) -> str | None:
    """按文件名反查渲染任务：优先 `render_{task_id}` 命名，其次 output_path 后缀匹配（含 Mongo 终态）。"""
    stem = Path(filename).stem
    if stem in _render_queue:
        return stem
    for tid, t in _render_queue.items():
        out = t.get("output_path") or ""
        if out and Path(out).name == filename:
            return tid
    try:
        from clipwright.context import mongo
        if mongo.is_connected:
            doc = mongo.db["render_tasks"].find_one({"$or": [
                {"task_id": stem},
                {"output_path": {"$regex": re.escape(filename) + "$"}},
            ]})
            if doc:
                return doc.get("task_id")
    except Exception:
        pass
    return None


def _enforce_render_file_owner(request: Request, filename: str) -> None:
    """轮 80（红线）：jwt 模式下按任务归属校验渲染产物访问；off/token 与 admin 放行。

    查不到归属任务的文件对 jwt 普通用户按 fail-closed 拒绝（遗留文件由
    管理员/off 模式处理）。
    """
    from clipwright.authz import current_user_id, is_admin

    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return
    task_id = _resolve_render_task_for_file(filename)
    if task_id is None:
        raise HTTPException(status_code=403, detail="无法确认该文件的归属，拒绝访问")
    task = _render_queue.get(task_id) or {}
    owner = task.get("owner_id") or ""
    if not owner:
        try:
            from clipwright.context import mongo
            if mongo.is_connected:
                doc = mongo.db["render_tasks"].find_one({"task_id": task_id})
                owner = (doc or {}).get("owner_id") or ""
        except Exception:
            pass
    if owner != uid:
        raise HTTPException(status_code=403, detail="无权访问该渲染产物")


@router.get("/download/{filename}")
async def download_render(filename: str, request: Request):
    """下载渲染输出的 MP4 文件。

    允许 CJK/unicode 文件名（如 "发布会.mp4"），仅拒路径分隔与 Windows 非法字符。
    """
    from urllib.parse import quote

    from clipwright.security import is_safe_download_name
    if not is_safe_download_name(filename):
        raise HTTPException(status_code=400, detail="非法文件名")
    _enforce_render_file_owner(request, filename)
    file_path = _renders_dir() / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
    # RFC 5987 编码文件名，支持中文（latin-1 无法直接承载 CJK）
    ascii_name = quote(filename)
    disposition = f"attachment; filename*=UTF-8''{ascii_name}"
    return FileResponse(str(file_path), media_type=_media_type_for(filename),
                        headers={"Content-Disposition": disposition})



_EXPORT_PRESETS = {
    "bilibili": {"width": 1920, "height": 1080, "fps": 30, "bitrate": "6M", "audio_bitrate": "320k", "note": "B站推荐 1080p 高码率"},
    "youtube": {"width": 1920, "height": 1080, "fps": 30, "bitrate": "8M", "audio_bitrate": "192k", "note": "YouTube 推荐"},
    "tiktok": {"width": 1080, "height": 1920, "fps": 30, "bitrate": "4M", "audio_bitrate": "192k", "note": "抖音/快手 竖屏"},
    "weibo": {"width": 720, "height": 1280, "fps": 24, "bitrate": "3M", "audio_bitrate": "192k", "note": "微博 竖屏"},
    "1080p": {"width": 1920, "height": 1080, "fps": 30, "bitrate": "5M", "audio_bitrate": "192k", "note": "标准 1080p"},
    "720p": {"width": 1280, "height": 720, "fps": 30, "bitrate": "3M", "audio_bitrate": "192k", "note": "标准 720p"},
    "480p": {"width": 854, "height": 480, "fps": 24, "bitrate": "1.5M", "audio_bitrate": "128k", "note": "标准 480p"},
    # Phase 3.3: 交付级封装（广播/母带场景）
    "prores422hq": {"width": 1920, "height": 1080, "fps": 30, "encoder": "prores_ks", "pix_fmt": "yuv422p10le",
                    "container_ext": "mov",  # D8: ProRes 容器是 MOV（原输出仍命名 .mp4）
                    "note": "ProRes 422 HQ（10bit，母带/广播交付）"},
    "h265_10bit": {"width": 1920, "height": 1080, "fps": 30, "encoder": "libx265", "pix_fmt": "yuv420p10le", "bitrate": "12M",
                   "note": "H.265 HEVC 10bit 高码率（HDR/高保真交付）"},
}


class RenderSettings(BaseModel):
    """渲染参数。"""
    preset: str = Field(default="", description="导出预设名（tiktok/720p/prores422hq 等）")
    soft_subtitle: bool = Field(default=False, description="字幕软轨封装（mov_text）而非烧入画面")
    container_ext: str = Field(default="", description="容器扩展名（覆盖，如 mov）")
    width: int = Field(default=1920, ge=64, le=7680, description="输出宽度 (px)")
    height: int = Field(default=1080, ge=64, le=4320, description="输出高度 (px)")
    fps: float = Field(default=30.0, gt=0, le=120, description="输出帧率")
    bitrate: str = "5M"
    audio_bitrate: str = "192k"
    preset: str = ""  # 预设名称，如 "bilibili"、"tiktok"，会覆盖其他参数
    # Phase 3.3: 编码器/像素格式覆盖（交付级；空 = 自动探测/默认）
    encoder: str = Field(default="", description="编码器覆盖（libx264/h264_nvenc/prores_ks/libx265；空=自动）")
    pix_fmt: str = Field(default="", description="像素格式覆盖（yuv420p/yuv420p10le/yuv422p10le；空=默认）")
    # 软字幕导出——字幕以 mov_text 轨封装而非烧入画面
    soft_subtitle: bool = Field(default=False, description="软字幕导出（mov_text 字幕轨，非烧入）")
    # 轮 95：响度归一开关（默认开；false = 混音不做 loudnorm，保持原始电平）
    loudnorm: bool = Field(default=True, description="响度归一（loudnorm LUFS）；false = 关闭")


class RenderRequest(BaseModel):
    """渲染请求体。"""
    timeline: Timeline
    output_path: Optional[str] = None
    settings: Optional[RenderSettings] = None
    audio_file_path: Optional[str] = None  # 用户上传的配音文件路径
    bgm_file_path: Optional[str] = None  # 背景音乐文件路径


@router.get("/presets")
async def list_presets() -> dict:
    """列出所有导出预设。"""
    return {"presets": _EXPORT_PRESETS}


@router.get("/backends")
async def list_render_backends() -> dict:
    """列出可用渲染后端（B-3 / P2-2）。

    返回**内建后端 + 插件贡献的后端**（贡献类型 `renderer_backend`），
    每个带能力声明与可用性探测结果。前端据此展示"本机 / 云端 / 插件 sidecar"选择，
    排障时也能一眼看出为什么回退到了本机。
    """
    from clipwright.providers.render import (
        render_backend_names, resolve_preferred_render_backend, resolve_render_backend,
    )

    names = render_backend_names()
    items: list[dict] = []
    for name in names:
        backend = resolve_render_backend(name)
        capabilities: dict = {}
        available: bool | None = None
        if backend is not None:
            caps = getattr(backend, "capabilities", None)
            if callable(caps):
                try:
                    capabilities = dict(caps() or {})
                except Exception:  # noqa: BLE001
                    capabilities = {}
            probe = getattr(backend, "probe", None)
            if callable(probe):
                try:
                    available = bool(await probe())
                except Exception:  # noqa: BLE001
                    available = False
        items.append({"name": name, "available": available, "capabilities": capabilities})

    requested = str(getattr(settings, "renderer_backend", "") or "local").strip().lower() or "local"
    try:
        preferred = resolve_preferred_render_backend()
        preferred_name = getattr(preferred, "name", "") or requested
    except Exception:  # noqa: BLE001
        preferred_name = requested
    return {
        "backends": items,
        "requested": requested,
        "effective": preferred_name,
        "remote_render_url": bool((settings.remote_render_url or "").strip()),
    }


def _resolve_settings(s: RenderSettings | None) -> dict:
    """根据设置和预设解析最终渲染参数。

    批6：model_dump(exclude_unset=True)——旧实现全量 dump 让 width/height/fps
    默认值恒定覆盖预设（tiktok 等竖屏预设形同虚设）；未知 preset 显式 400。
    """
    base = s.model_dump(exclude_unset=True) if s else {}
    preset_name = str(base.get("preset", "") or "")
    if preset_name and preset_name not in _EXPORT_PRESETS:
        raise ValueError(f"未知导出预设: {preset_name}（可用: {', '.join(sorted(_EXPORT_PRESETS))}）")
    if preset_name:
        preset = _EXPORT_PRESETS[preset_name].copy()
        preset.pop("note", None)
        # 预设为基底，显式传入的设置项可覆盖（空串不覆盖——避免 encoder/pix_fmt
        # 默认空值顶掉预设）
        preset.update({k: v for k, v in base.items() if v is not None and v != "" and k != "preset"})
        return preset
    return {
        "width": base.get("width", 1920),
        "height": base.get("height", 1080),
        "fps": base.get("fps", 30.0),
        "bitrate": base.get("bitrate", "5M"),
        "audio_bitrate": base.get("audio_bitrate", "192k"),
        "encoder": base.get("encoder", ""),
        "pix_fmt": base.get("pix_fmt", ""),
        "soft_subtitle": bool(base.get("soft_subtitle", False)),
        "container_ext": str(base.get("container_ext", "") or ""),
        # 轮 95：响度归一开关（显式 false 会经上面 update 保留）
        "loudnorm": bool(base.get("loudnorm", True)),
    }


@router.post("/start")
async def start_render(
    body: RenderRequest,
) -> dict:
    """提交渲染任务：将 Timeline JSON 渲染为 MP4 视频。"""
    tl = body.timeline
    # 入参白名单（timeline 媒体路径 + 音频/BGM）
    _validate_render_inputs(tl, body.audio_file_path or "", body.bgm_file_path or "")
    # 输出文件名校验 + 强制落入 renders/（safe 名称，杜绝 renders/../../ 穿越）
    raw_output = body.output_path or ""
    if raw_output and not is_safe_download_name(raw_output):
        raise HTTPException(status_code=400, detail="非法输出文件名")
    s = body.settings
    try:
        params = _resolve_settings(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    requested_name = Path(raw_output).name if raw_output else ""
    _ext = str(params.get("container_ext") or "mp4")
    if requested_name:
        stem = Path(requested_name).stem
        requested_name = f"{stem}.{_ext}"
    else:
        requested_name = f"render_{uuid.uuid4().hex[:8]}.{_ext}"
    out = str(_renders_dir() / requested_name)
    logger.info("渲染请求: tracks=%d, output=%s, params=%s",
                len(tl.tracks or []), out, params)
    try:
        result = await _new_render_service().render(
            tl, out,
            width=params["width"],
            height=params["height"],
            fps=params["fps"],
            bitrate=params["bitrate"],
            audio_bitrate=params["audio_bitrate"],
            audio_file_path=body.audio_file_path or "",
            bgm_file_path=body.bgm_file_path or "",
            encoder_override=params.get("encoder", ""),
            pix_fmt_override=params.get("pix_fmt", ""),
            loudnorm_enabled=bool(params.get("loudnorm", True)),
        )
    except Exception as e:
        logger.exception("start_render failed: %s", e)
        raise
    if not result.success:
        raise HTTPException(status_code=400, detail=_sanitize_detail(result.error or "渲染失败"))
    return result.to_dict()


@router.get("/status/{render_id}")
async def get_render_status(render_id: str, request: Request) -> dict:
    """查询渲染进度。优先查询队列，若无则返回 404。"""
    # 轮 80（红线）：按任务归属校验（内存队列缺失时回退 Mongo；全查不到则 jwt 普通用户 fail-closed）
    _enforce_render_file_owner(request, f"{render_id}.mp4")
    # 先检查队列
    queue_task = _render_queue.get(render_id)
    if queue_task is not None:
        return {
            "render_id": render_id,
            "status": queue_task["status"],
            "progress": queue_task.get("progress", 0),
            "phase": queue_task.get("phase", ""),
            "detail": queue_task.get("detail", ""),
            "output_path": queue_task.get("output_path", ""),
            "result": queue_task.get("result"),
        }
    # 回退：检查 renders/ 目录中是否有同名文件
    from clipwright.security import is_safe_id
    if not is_safe_id(render_id):
        raise HTTPException(status_code=400, detail="无效的 render_id")
    file_path = anchor(f"renders/{render_id}.mp4")
    if not file_path.exists():
        # ProRes 等交付预设产出 .mov——状态回退一并探测
        alt = anchor(f"renders/{render_id}.mov")
        if alt.exists():
            file_path = alt
    if file_path.exists():
        return {
            "render_id": render_id,
            "status": "completed",
            "progress": 100,
            "output_path": str(file_path),
        }
    raise HTTPException(status_code=404, detail=f"渲染任务不存在: {render_id}")


@router.get("/thumbnail")
async def get_video_thumbnail(path: str, time_sec: float = 0.5, request: Request = None):
    """从视频文件提取一帧作为缩略图。"""
    import shutil
    import subprocess

    from starlette.background import BackgroundTask

    from clipwright.security import assert_allowed_path

    src = Path(path)
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    # 安全：仅允许白名单目录内的素材；jwt 模式下校验归属
    assert_allowed_path(src)
    if request is not None:
        _enforce_media_path_owner(request, src)

    ext = src.suffix.lower()
    if ext not in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wav", ".mp3", ".m4a"):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    # 对纯音频文件，返回默认缩略图
    if ext in (".wav", ".mp3", ".m4a"):
        raise HTTPException(status_code=400, detail="音频文件无缩略图")

    thumb = Path(tempfile.mkdtemp(prefix="thumb_")) / "thumb.jpg"

    def _generate() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ss", str(time_sec),
             "-vframes", "1", "-vf", "scale=160:-1", "-q:v", "5",
             str(thumb)],
            capture_output=True, text=False, timeout=15,
        )

    try:
        # 在后台线程执行，避免阻塞事件循环
        result = await asyncio.to_thread(_generate)
        if result.returncode != 0 or not thumb.exists() or thumb.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="缩略图生成失败")
        return FileResponse(str(thumb), media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=3600"},
                            background=BackgroundTask(shutil.rmtree, str(thumb.parent), True))
    except HTTPException:
        shutil.rmtree(thumb.parent, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(thumb.parent, ignore_errors=True)
        logger.error("缩略图异常: %s", e)
        raise HTTPException(status_code=500, detail="缩略图生成失败")
