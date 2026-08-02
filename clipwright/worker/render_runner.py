"""远程渲染任务执行器 — 将 Timeline dict 渲染为本地 MP4。

本模块是 Worker 渲染管线的心脏：todo 3（jobs 端点）收到任务后调用
:func:`run_job`，后者把 timeline 里的素材引用重映射为本地文件路径，再
**直接复用** ``clipwright.services.render.RenderService`` 完成真正的 ffmpeg
渲染（SAR/setsar、fontfile 相对路径、损坏源 ffprobe 预检、输出有效性校验等
全部固定逻辑都在那一边，这里零复制）。

约束 / 约定:
- 素材哈希 → 本地文件: ``<work_dir>/assets/<sha1[:16]><ext>``（上传时扩展名
  已消毒，故用前缀匹配；work_dir 解析见 :func:`resolve_work_dir`）。
- 事件循环: ``RenderService.render`` 本身是 ``async`` 且内部已把阻塞的
  ffmpeg 调用 offload 到线程池（``_ff`` / ``_ff_concat``），因此直接 await
  不会阻塞事件循环，无需再用 ``asyncio.to_thread`` 包一层。
- 异常语义: 任何失败（含资产缺失 / timeline 畸形 / RenderResult.success=False）
  都会先 ``update_job(job_id, status="failed", error=...)`` 再 re-raise，
  由调用方决定如何处理。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from clipwright.schema.timeline import Timeline
from clipwright.services.render import RenderResult, RenderService
from clipwright.worker.store import JobStore

# ── work_dir 解析 ──────────────────────────────────
# 优先级：CLIPWRIGHT_WORKER_WORK_DIR > <仓库根>\_cache\worker。
# 注意：与并行 worker 添加的 asset 上传端点保持一致 —— 若其在 api.py 中
# 新增了同名常量，请直接复用；当前 api.py 尚未暴露，故在此定义并交由
# todo 3 集成检查确认。
_DEFAULT_WORK_DIR_REL = Path("_cache") / "worker"


def resolve_work_dir() -> Path:
    """解析 worker 工作目录，创建 jobs/ 与 assets/ 子目录。"""
    env = os.environ.get("CLIPWRIGHT_WORKER_WORK_DIR")
    if env:
        root = Path(env)
    else:
        # render_runner.py 位于 <root>/clipwright/worker/ → parents[2] 为仓库根
        root = Path(__file__).resolve().parents[2] / _DEFAULT_WORK_DIR_REL
    root = root.resolve()
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    return root


class AssetNotFoundError(FileNotFoundError):
    """timeline 引用了 assets/ 下不存在的素材文件。"""


# RenderService.render 支持的参数（render.py render() 签名）：
# width/height/fps/bitrate/audio_bitrate/audio_file_path/bgm_file_path/
# enable_progress。progress_callback 由本模块单独注入。
_RENDER_PARAMS: tuple[str, ...] = (
    "width",
    "height",
    "fps",
    "bitrate",
    "audio_bitrate",
    "audio_file_path",
    "bgm_file_path",
    "enable_progress",
)


def _filter_render_params(params: dict[str, Any], tl: dict[str, Any]) -> dict[str, Any]:
    """只透传 RenderService.render 支持的 kwargs；width/height/fps 缺省回退到 timeline。"""
    kwargs = {k: v for k, v in (params or {}).items() if k in _RENDER_PARAMS}
    for key in ("width", "height", "fps"):
        if key not in kwargs and tl.get(key) not in (None, "", 0):
            kwargs[key] = tl[key]
    return kwargs


def _resolve_asset_uri(uri: str, assets_dir: Path) -> str:
    """把 ``asset://<sha1>`` 解析为 assets/ 下的本地文件路径。

    扩展名在上传时被消毒，故按 ``<sha1[:16]>`` 前缀匹配；找不到即抛
    :class:`AssetNotFoundError`（此时调用方负责把 job 标记为 failed）。
    """
    sha1 = uri[len("asset://"):].strip()
    if not sha1:
        raise AssetNotFoundError(f"asset not found: {uri}")
    prefix = sha1[:16]
    matches = sorted(p for p in assets_dir.glob(f"{prefix}*") if p.is_file())
    if not matches:
        raise AssetNotFoundError(f"asset not found: {sha1}")
    return str(matches[0])


def _resolve_asset_refs(obj: Any, assets_dir: Path) -> Any:
    """递归扫描 timeline，把每个 ``asset://...`` 字符串替换为本地文件路径。"""
    if isinstance(obj, dict):
        return {k: _resolve_asset_refs(v, assets_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_asset_refs(v, assets_dir) for v in obj]
    if isinstance(obj, str) and obj.startswith("asset://"):
        return _resolve_asset_uri(obj, assets_dir)
    return obj


def _remap_original_asset_ids(tracks: list[dict[str, Any]], asset_refs: dict[str, str]) -> None:
    """① 把 clip.asset_id 从「原始素材 id」重映射为 ``asset://<sha1>``。

    已在 ``asset://<sha1>`` 形式（或不在 asset_refs 内）的保持原样。
    就地修改传入 dict，返回无值。
    """
    for track in tracks or []:
        for clip in track.get("clips", []) or []:
            aid = clip.get("asset_id")
            if aid and aid in asset_refs:
                clip["asset_id"] = asset_refs[aid]


def _make_progress(job_id: str, store: JobStore) -> Callable[..., Any]:
    """构造 RenderService.render 的 progress_callback（phase, pct 0-100, detail）。

    RenderService 会 ``await`` 该回调，故必须返回 async 函数；写入 store 的
    progress 封顶 99，最终 100 由 run_job 完成时写入。
    """

    async def cb(phase: str, pct: float | int, detail: str) -> None:
        store.update_job(job_id, phase=phase, progress=min(float(pct), 99.0), detail=detail)

    return cb


async def run_job(
    job_id: str,
    timeline_dict: dict[str, Any],
    params: dict[str, Any],
    asset_refs: dict[str, str],
    store: JobStore,
) -> RenderResult:
    """执行单个远程渲染任务。

    :param job_id: 任务 ID（同时用作输出文件名 ``<work_dir>/jobs/<job_id>.mp4``）
    :param timeline_dict: 原始 timeline dict（raw JSON，来自 jobs 端点）
    :param params: 渲染参数（width/height/fps/bitrate/audio_bitrate/...）
    :param asset_refs: 原始素材 id → ``asset://<sha1>`` 映射
    :param store: JobStore 实例（todo 1），用于写 status/progress/error
    :returns: 成功时返回 RenderResult；任何失败先写 failed 再 re-raise
    """
    work_dir = resolve_work_dir()
    assets_dir = work_dir / "assets"
    jobs_dir = work_dir / "jobs"

    store.update_job(job_id, status="rendering", phase="prepare", progress=0, detail="开始渲染")

    try:
        if not isinstance(timeline_dict, dict) or "tracks" not in timeline_dict:
            raise ValueError("malformed timeline: missing 'tracks'")

        # ① 原始 id → asset://<sha1>
        _remap_original_asset_ids(timeline_dict.get("tracks", []), asset_refs or {})
        # ② asset://<sha1> → 本地文件路径（缺失即抛 AssetNotFoundError）
        tl = _resolve_asset_refs(timeline_dict, assets_dir)

        render_kwargs = _filter_render_params(params, tl)
        render_kwargs["progress_callback"] = _make_progress(job_id, store)

        output_path = jobs_dir / f"{job_id}.mp4"
        service = RenderService()
        # RenderService.render 本身为 async 且内部将阻塞 ffmpeg 调用丢进线程池，
        # 直接 await 不阻塞事件循环（包 to_thread 反而只会产生未 await 的协程）。
        result = await service.render(
            Timeline(**tl), output_path=str(output_path), **render_kwargs
        )
        if not result.success:
            raise RuntimeError(result.error or "render failed")

        store.update_job(
            job_id,
            status="completed",
            progress=100,
            phase="done",
            detail=f"渲染完成: {result.duration_sec:.2f}s",
            output_path=str(output_path),
        )
        return result
    except Exception as e:
        store.update_job(job_id, status="failed", error=str(e))
        raise
