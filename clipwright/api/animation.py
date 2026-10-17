"""动画 API — 查询动画定义、编排序列与单镜头预览。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clipwright.animation import AnimationRegistry
from clipwright.schema.animation import AnimationDef, AnimationType

router = APIRouter(prefix="/api/animation", tags=["animation"])


class MgPreviewRequest(BaseModel):
    """Phase 2.6: 单镜头 MG 动画预览请求。"""

    animation_id: str = Field(default="", description="内置模板动画 ID（与 mg_json 二选一）")
    mg_json: dict | None = Field(default=None, description="直接传入 MG 定义 JSON（LLM 生成预览）")
    params: dict[str, str] = Field(default_factory=dict, description="模板参数（占位符替换）")
    width: int = Field(default=1280, ge=320, le=3840, description="预览宽度")
    height: int = Field(default=720, ge=320, le=2160, description="预览高度")
    fps: float = Field(default=30.0, ge=10, le=60, description="预览帧率")


@router.get("/list", response_model=list[AnimationDef])
async def list_animations(anim_type: str = "") -> list[AnimationDef]:
    """列出所有或指定类型的动画定义。"""
    if anim_type:
        try:
            t = AnimationType(anim_type)
            return AnimationRegistry.list(t)
        except ValueError:
            pass
    return AnimationRegistry.list()


@router.get("/onscreen", response_model=list[AnimationDef])
async def list_onscreen() -> list[AnimationDef]:
    """列出所有屏幕动画。"""
    return AnimationRegistry.list(AnimationType.ONSCREEN)


@router.get("/transitions", response_model=list[AnimationDef])
async def list_transitions() -> list[AnimationDef]:
    """列出所有转场动画。"""
    return AnimationRegistry.list(AnimationType.TRANSITION)


@router.get("/get/{animation_id}", response_model=AnimationDef)
async def get_animation(animation_id: str) -> AnimationDef:
    """获取单个动画定义的详细信息。"""
    defn = AnimationRegistry.get(animation_id)
    if defn is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Animation '{animation_id}' not found")
    return defn


@router.get("/mg/list")
async def list_mg_templates() -> list[dict]:
    """Phase 2.6: 列出内置 MG 模板（id/name/description/params），供预览工坊使用。"""
    from clipwright.animation.mg_renderer import MGRenderer

    items: list[dict] = []
    for defn in MGRenderer.list_animations():
        if not isinstance(defn, dict):
            continue
        aid = defn.get("id") or defn.get("animation_id", "")
        if not aid:
            continue
        try:
            mg_def = MGRenderer.load_animation(aid)
            params = (mg_def or {}).get("params", {}) if isinstance(mg_def, dict) else {}
        except Exception:
            params = {}
        items.append({
            "animation_id": aid,
            "name": defn.get("name", aid),
            "description": defn.get("description", ""),
            "duration_sec": defn.get("duration_sec", 3.0),
            "params": params if isinstance(params, dict) else {},
            "shot_type": (mg_def or {}).get("shot_type", "") if isinstance(mg_def, dict) else "",
        })
    return items


@router.get("/mg/generations")
async def list_mg_generations(limit: int = 50) -> list[dict]:
    """M6: 列出最近 MG 生成记录摘要（新→旧），供按 generation_id 预览回看。"""
    from clipwright.animation.mg.storage import MGStorage
    return MGStorage().list_generations(limit=limit)


@router.get("/mg/generations/{generation_id}")
async def get_mg_generation(generation_id: str) -> dict:
    """M6: 按 generation_id 取完整生成记录（mg_def）——可送 /preview 渲染回看。"""
    import re

    from clipwright.animation.mg.storage import MGStorage

    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,80}", generation_id):
        raise HTTPException(status_code=400, detail="非法的 generation_id")
    record = MGStorage().load_generation(generation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"生成记录不存在: {generation_id}")
    return record


def _mg_variables(mg_def: dict, params: dict[str, str]) -> list[dict]:
    """复用 MGRenderer 的变量声明逻辑（预览端点回显用）。"""
    try:
        from clipwright.animation.mg_renderer import MGRenderer

        return MGRenderer.build_variables(mg_def, params or {})
    except Exception:
        return []


def _resolve_preview_mg(body: MgPreviewRequest) -> tuple[dict, float, str]:
    """解析预览请求 → (mg_def, duration_sec, html)。

    ``animation_id``（内置模板）或 ``mg_json``（LLM 生成定义）二选一；
    背景守卫与主管线一致（默认透明叠加）。渲染型与 HTML 即时预览共用本函数。
    """
    from clipwright.animation.mg.generator import MGGenerator
    from clipwright.animation.mg_renderer import MGRenderer

    mg_def = body.mg_json if isinstance(body.mg_json, dict) and body.mg_json else None
    if mg_def is None:
        mg_def = MGRenderer.load_animation(body.animation_id)
        if mg_def is None:
            raise HTTPException(status_code=404, detail=f"动画模板不存在: {body.animation_id}")
    if not isinstance(mg_def, dict) or not mg_def.get("elements"):
        raise HTTPException(status_code=400, detail="无效的 MG 定义（缺少 elements）")

    mg_def = MGGenerator._ensure_no_background(mg_def, "")
    dur = min(float(mg_def.get("duration_sec", 3.0) or 3.0), 8.0)
    try:
        html = MGRenderer.render(
            mg_def, body.params or {},
            width=body.width, height=body.height, fps=body.fps,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MG 渲染失败: {e}")
    return mg_def, dur, html


@router.post("/mg/html")
async def preview_mg_html(body: MgPreviewRequest) -> dict:
    """HTML 即时预览（P2-3）：返回合成 HTML 本身，**不渲染**、不起 Chromium。

    前端把它塞进 sandbox iframe，用 `window.__timelines['main'].seek(t)` 驱动播放
    （与主管线同源的 hf_timeline 运行时），因此是秒级的"真效果"预览；
    需要落地成片时再走 `POST /api/animation/preview`（Hyperframes 渲染 MP4）。
    """
    mg_def, dur, html = _resolve_preview_mg(body)
    variables = {k: str(v) for k, v in (body.params or {}).items()}
    return {
        "html": html,
        "duration_sec": round(dur, 2),
        "width": body.width,
        "height": body.height,
        "fps": body.fps,
        "animation_id": mg_def.get("animation_id", ""),
        # P1-5：声明过的变量与本次取值，前端可在 iframe 内驱动/展示
        "variables": {str(v["id"]): v.get("default", "") for v in _mg_variables(mg_def, variables)},
    }


@router.post("/preview")
async def preview_mg(body: MgPreviewRequest) -> dict:
    """Phase 2.6: 单镜头 MG 动画预览 — 直接经 Hyperframes 渲染短视频，不入主渲染队列。

    ``animation_id``（内置模板）或 ``mg_json``（LLM 生成定义）二选一；
    返回 MP4 播放 URL（走 /api/render/download 静态服务）。
    只要 HTML 级预览时用 `POST /api/animation/mg/html`（不渲染、秒级）。
    """
    import uuid
    from pathlib import Path

    from clipwright.animation.hyperframes_renderer import HyperframesRenderer
    from clipwright.config import logger
    from clipwright.services.render import run_tracked_ff

    mg_def, dur, html = _resolve_preview_mg(body)

    if not await HyperframesRenderer.ais_available():
        raise HTTPException(status_code=503, detail="Hyperframes 渲染器不可用，无法预览")

    name = f"preview_{uuid.uuid4().hex[:12]}"
    renders_dir = Path("renders")
    renders_dir.mkdir(parents=True, exist_ok=True)
    # P2-37: 预览产物 TTL 清理——预览是随时可重生成的一次性产物，
    # 旧实现只删临时 mov、mp4 永久累积（每次预览 ~数 MB）。清理 2 小时前的旧预览。
    try:
        import time as _time
        _now = _time.time()
        for old in renders_dir.glob("preview_*.mp4"):
            if _now - old.stat().st_mtime > 2 * 3600:
                old.unlink(missing_ok=True)
    except Exception:
        pass
    mov_path = Path("renders") / f"{name}.mov"
    mp4_path = renders_dir / f"{name}.mp4"
    try:
        # P1-01: 与主管线渲染同构——mg_html 写独立目录 index.html 交给 hyperframes。
        # 旧实现塞进 render_overlays 的 overlay 列表，_overlay_to_html 只读 text
        # 字段 → 恒产出空白视频却报成功。
        async def _run_preview(cmd):
            return await run_tracked_ff(
                cmd, capture_output=True, text=False, timeout=1800,
                env=HyperframesRenderer._render_env(),
            )

        out = await HyperframesRenderer.render_mg_html_to_mov(
            html, "renders", str(mov_path), fps=body.fps, run_cmd=_run_preview,
            # 预览是"快速看效果"场景：draft 档显著缩短出片时间（上游 Quality 分级），
            # 需要看最终画质时用正式渲染管线。
            quality="draft",
            # P1-5：把本次参数作为上游变量覆盖传入（HTML 声明里的 default 是兜底）
            variables={k: str(v) for k, v in (body.params or {}).items()},
        )
        if not out or not mov_path.exists():
            raise HTTPException(status_code=500, detail="Hyperframes 渲染失败")
        # MOV → MP4（H.264 重编码，浏览器可直接播放）
        r = await run_tracked_ff(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mov_path),
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             "-an", str(mp4_path)],
            capture_output=True, text=False, timeout=120,
        )
        if r.returncode != 0 or not mp4_path.exists():
            raise HTTPException(status_code=500, detail="预览转码失败")
        logger.info("MG 预览完成: %s (%.1fs)", name, dur)
        return {
            "url": f"/api/render/download/{name}.mp4",
            "path": str(mp4_path),
            "duration_sec": round(dur, 2),
            "width": body.width,
            "height": body.height,
            "fps": body.fps,
        }
    finally:
        try:
            mov_path.unlink(missing_ok=True)
        except Exception:
            pass
