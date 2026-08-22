"""远程渲染服务 — 将 Timeline JSON 提交到远程 Worker 渲染为 MP4。

与 :class:`clipwright.services.render.RenderService` 的 ``render`` **完全同签名**
（drop-in 替代；todo 6 集成按 ``remote_render_url`` 是否配置选择本地/远程服务，
此处签名与进度回调约定必须与 RenderService 保持一致，SSE 转发无需改动）。

工作流:
  1. 素材收集 + 去重上传 — 收集本地源文件（video/image clip 的 asset_id +
     audio_file_path / bgm_file_path + 系统字体），逐文件计算 sha1；
     HEAD 探测远程已存在（按 sha1[:16] 前缀）则跳过，否则 multipart 上传
     （携带 ``hash`` 表单字段，服务端校验；``stored:false`` 视为已存在）。
  2. Timeline 重写 — clip.asset_id / audio_file_path / bgm_file_path 重映射为
     ``asset://<sha1>``，并构造 ``asset_refs``（原始 id → asset:// uri）。
  3. 提交 job — POST ``/api/worker/jobs``，返回 job_id（202）。
  4. 轮询 — 按 ``remote_render_poll_interval`` 轮询状态，把 phase/progress/detail
     转发给 progress_callback（约定同 RenderService：pct 为 0-100）。
  5. 下载 — 完成后流式下载到 ``<output>.part-<uuid>``，ffprobe 取时长后原子
     ``os.replace`` 到最终路径（绝不遗留 .part-* 临时文件）。
  6. 失败兜底 — 网络错误 / 非 2xx / job failed / 超时：按 ``remote_render_fallback``
     决定回退本地 ``RenderService().render``（使用**原始** timeline / 参数）或
     返回失败结果。

事件循环约束：全程使用 httpx.AsyncClient（异步 I/O，不阻塞事件循环）；
sha1 计算与 ffprobe 等 CPU / 阻塞操作经 ``asyncio.to_thread`` offload。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.services.render import (
    RenderResult,
    RenderService,
    _get_actual_duration,
    _resolve_system_font,
)

# 上传/下载等大体积传输的读写超时（秒）；连接超时单独 15s
_REMOTE_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=15.0)

# 进度回调类型：RenderService 用 ``await`` 调用回调，因此回调必须是协程函数，
# 签名 (phase: str, pct: float, detail: str)，pct 为 0-100。
ProgressCallback = Callable[[str, float, str], Any]


class RemoteRenderError(RuntimeError):
    """远程渲染可控失败（网络错误 / 非 2xx / job failed / 超时 / 下载失败）。"""


class RemoteRenderService:
    """远程渲染服务 — 与 RenderService.render 签名一致的 drop-in 替代。"""

    # ── sha1 计算（CPU 密集，供 to_thread 调用）──────────────────

    @staticmethod
    def _sha1_file(path: str | Path) -> str:
        """分块计算文件 sha1（流式，内存 O(chunk)）。"""
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ── 素材收集 ─────────────────────────────────────────────────

    def _collect_local_assets(
        self, timeline: Timeline, audio_file_path: str, bgm_file_path: str
    ) -> dict[str, str]:
        """收集需要上传的本地文件 → {绝对路径: ""}（sha1 稍后填充）。

        收集范围（与 todo 契约一致）：video/image clip 的 asset_id、
        audio_file_path、bgm_file_path，以及系统字体文件（_resolve_system_font）。
        """
        files: dict[str, str] = {}
        for track in timeline.tracks:
            for clip in track.clips or []:
                kind = str(clip.kind) if clip.kind else str(track.kind)
                if kind in ("video", "image") and clip.asset_id:
                    p = Path(clip.asset_id)
                    if p.exists():
                        files[str(p.resolve())] = ""
        for ap in (audio_file_path, bgm_file_path):
            if ap and Path(ap).exists():
                files[str(Path(ap).resolve())] = ""
        font = _resolve_system_font()
        if font and Path(font).exists():
            files[str(Path(font).resolve())] = ""
        return files

    # ── 远程 HTTP 调用 ───────────────────────────────────────────

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        """构造请求头；令牌非空时以 Bearer 形式透传。"""
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    async def _asset_exists(
        self, client: httpx.AsyncClient, base_url: str, headers: dict[str, str], sha1: str
    ) -> bool:
        """HEAD 探测素材是否已在远程（去重前探）；200 存在 / 404 不存在。"""
        prefix = sha1[:16]
        try:
            r = await client.head(f"{base_url}/api/worker/assets/{prefix}", headers=headers)
        except httpx.HTTPError as e:
            raise RemoteRenderError(f"素材存在性探测失败 (HEAD {prefix}): {e}") from e
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            return False
        raise RemoteRenderError(f"素材探测异常 (HEAD {prefix}): HTTP {r.status_code}")

    async def _upload_asset(
        self, client: httpx.AsyncClient, base_url: str, headers: dict[str, str],
        path: str, sha1: str,
    ) -> None:
        """multipart 上传素材（携带 hash 表单字段校验）；stored:false 视为已存在。"""
        try:
            with open(path, "rb") as f:
                files = {"file": (Path(path).name, f, "application/octet-stream")}
                data = {"hash": sha1}
                r = await client.post(
                    f"{base_url}/api/worker/assets", headers=headers, files=files, data=data
                )
        except OSError as e:
            raise RemoteRenderError(f"读取本地素材失败 {path}: {e}") from e
        except httpx.HTTPError as e:
            raise RemoteRenderError(f"素材上传失败 {path}: {e}") from e
        if r.status_code == 409:
            raise RemoteRenderError(
                f"素材哈希不匹配 (409) {path}: 服务端计算 sha1 与客户端不一致"
            )
        if r.status_code != 200:
            raise RemoteRenderError(f"素材上传失败 (HTTP {r.status_code}) {path}: {r.text[:200]}")
        body = r.json()
        logger.debug(
            "素材上传完成: %s hash=%s stored=%s",
            Path(path).name, str(body.get("hash", sha1))[:16], body.get("stored"),
        )

    async def _submit_job(
        self, client: httpx.AsyncClient, base_url: str, headers: dict[str, str],
        timeline_dict: dict[str, Any], params: dict[str, Any], asset_refs: dict[str, str],
    ) -> str:
        """提交远程渲染任务，返回 job_id（202）。"""
        body = {"timeline": timeline_dict, "params": params, "asset_refs": asset_refs}
        try:
            r = await client.post(f"{base_url}/api/worker/jobs", headers=headers, json=body)
        except httpx.HTTPError as e:
            raise RemoteRenderError(f"提交渲染任务失败: {e}") from e
        if r.status_code not in (200, 202):
            raise RemoteRenderError(f"提交渲染任务失败 (HTTP {r.status_code}): {r.text[:200]}")
        job_id = r.json().get("job_id")
        if not job_id:
            raise RemoteRenderError("提交渲染任务失败: 响应缺少 job_id")
        return job_id

    async def _poll_job(
        self, client: httpx.AsyncClient, base_url: str, headers: dict[str, str],
        job_id: str, progress_callback: Optional[ProgressCallback],
        cancel_id: str | None = None,
    ) -> dict[str, Any]:
        """轮询 job 直到 completed / failed / 超时；进度转发给 progress_callback。

        进度约定与 RenderService 一致：pct 为 0-100（worker 的 progress 即 0-100）。
        """
        from clipwright.config import settings

        interval = max(0.1, float(getattr(settings, "remote_render_poll_interval", 1.5)))
        timeout = int(getattr(settings, "remote_render_timeout", 1800))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        status_url = f"{base_url}/api/worker/jobs/{job_id}"

        while True:
            # Phase 3.4: 轮询期间感知用户取消（取消不触发本地兜底）
            from clipwright.services.render import is_render_cancelled
            if is_render_cancelled(cancel_id):
                raise RemoteRenderError(f"渲染已取消: {job_id}")
            try:
                r = await client.get(status_url, headers=headers)
            except httpx.HTTPError as e:
                raise RemoteRenderError(f"轮询任务状态失败: {e}") from e
            if r.status_code == 404:
                raise RemoteRenderError(f"任务不存在 (404): {job_id}")
            if r.status_code != 200:
                raise RemoteRenderError(f"轮询任务状态异常 (HTTP {r.status_code}): {r.text[:200]}")
            job = r.json()
            status = job.get("status", "")
            phase = job.get("phase", "") or ""
            progress = float(job.get("progress", 0) or 0)
            detail = job.get("detail", "") or ""

            if progress_callback:
                await progress_callback(phase, progress, detail)

            if status == "completed":
                return job
            if status == "failed":
                raise RemoteRenderError(f"远程渲染任务失败: {job.get('error') or 'unknown error'}")

            if loop.time() + interval >= deadline:
                raise RemoteRenderError(f"远程渲染超时（{timeout}s 内未完成）")
            await asyncio.sleep(interval)

    async def _download_output(
        self, client: httpx.AsyncClient, base_url: str, headers: dict[str, str],
        job_id: str, output_path: str | Path,
    ) -> Path:
        """流式下载产物到 <output>.part-<uuid>，再原子替换为最终文件。

        finally 中必定删除 .part-* 临时文件，绝不遗留部分下载。
        """
        final = Path(output_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(f"{final.name}.part-{uuid.uuid4().hex[:8]}")
        url = f"{base_url}/api/worker/jobs/{job_id}/download"
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 409:
                    raise RemoteRenderError(f"任务尚未完成，无法下载 (409): {job_id}")
                if resp.status_code == 404:
                    raise RemoteRenderError(f"任务或产物不存在 (404): {job_id}")
                if resp.status_code != 200:
                    raise RemoteRenderError(f"下载产物失败 (HTTP {resp.status_code}): {job_id}")
                with open(part, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
            if part.stat().st_size == 0:
                raise RemoteRenderError(f"下载产物为空文件: {part}")
            os.replace(part, final)
            return final.resolve()
        except httpx.HTTPError as e:
            raise RemoteRenderError(f"下载产物失败: {e}") from e
        finally:
            part.unlink(missing_ok=True)  # 绝不遗留 .part-* 临时文件

    # ── 本地兜底（复用 RenderService）────────────────────────────

    async def _render_local(
        self, timeline: Timeline, output_path: str | Path, width: int, height: int,
        fps: float, bitrate: str, audio_bitrate: str, audio_file_path: str,
        bgm_file_path: str, progress_callback: Optional[ProgressCallback],
        enable_progress: bool, cancel_id: str | None = None,
        encoder_override: str = "", pix_fmt_override: str = "",
    ) -> RenderResult:
        """本地渲染兜底：完整透传原始参数，交给 RenderService 处理。"""
        return await RenderService().render(
            timeline, output_path, width=width, height=height, fps=fps,
            bitrate=bitrate, audio_bitrate=audio_bitrate,
            audio_file_path=audio_file_path, bgm_file_path=bgm_file_path,
            progress_callback=progress_callback, enable_progress=enable_progress,
            cancel_id=cancel_id, encoder_override=encoder_override,
            pix_fmt_override=pix_fmt_override,
        )

    # ── 远程主流程 ───────────────────────────────────────────────

    async def _render_remote(
        self, base_url: str, timeline: Timeline, output_path: str | Path,
        width: int, height: int, fps: float, bitrate: str, audio_bitrate: str,
        audio_file_path: str, bgm_file_path: str,
        progress_callback: Optional[ProgressCallback], enable_progress: bool,
        cancel_id: str | None = None, encoder_override: str = "",
        pix_fmt_override: str = "",
    ) -> RenderResult:
        from clipwright.config import settings

        token = settings.remote_render_token or ""
        headers = self._headers(token)

        # ① 素材收集 + 去重上传
        local_files = self._collect_local_assets(timeline, audio_file_path, bgm_file_path)
        path_to_sha1: dict[str, str] = {}
        sha1_seen: set[str] = set()
        if local_files:
            logger.info("远程渲染: 收集 %d 个本地素材文件", len(local_files))

        # Phase 3.4: 连接池限制（防并发任务耗尽 worker 连接）
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        async with httpx.AsyncClient(timeout=_REMOTE_HTTP_TIMEOUT, limits=limits) as client:
            for path in local_files:
                sha1 = await asyncio.to_thread(self._sha1_file, path)
                path_to_sha1[path] = sha1
                if sha1 in sha1_seen:
                    continue  # 同内容素材只上传一次
                sha1_seen.add(sha1)
                if await self._asset_exists(client, base_url, headers, sha1):
                    logger.debug("素材已存在（HEAD 命中，跳过上传）: %s", Path(path).name)
                    continue
                await self._upload_asset(client, base_url, headers, path, sha1)

            # ② Timeline 重写：asset_id / audio/bgm 路径 → asset://<sha1>
            new_tl = timeline.model_copy(deep=True)
            asset_refs: dict[str, str] = {}
            for track in new_tl.tracks:
                for clip in track.clips or []:
                    aid = clip.asset_id
                    if not aid:
                        continue
                    resolved = str(Path(aid).resolve())
                    if resolved in path_to_sha1:
                        uri = f"asset://{path_to_sha1[resolved]}"
                        clip.asset_id = uri
                        asset_refs[aid] = uri

            def _remap_audio(p: str) -> str:
                """audio/bgm 本地文件 → asset://<sha1>；空串保持空串。"""
                if not p:
                    return ""
                resolved = str(Path(p).resolve())
                if resolved in path_to_sha1:
                    return f"asset://{path_to_sha1[resolved]}"
                return p

            params: dict[str, Any] = {
                "width": width,
                "height": height,
                "fps": fps,
                "bitrate": bitrate,
                "audio_bitrate": audio_bitrate,
                "audio_file_path": _remap_audio(audio_file_path),
                "bgm_file_path": _remap_audio(bgm_file_path),
                "enable_progress": enable_progress,
                # Phase 3.3/3.4: 交付级编码器/像素格式透传（worker 侧 RenderService 消费）
                "encoder": encoder_override,
                "pix_fmt": pix_fmt_override,
            }

            timeline_dict = new_tl.model_dump()
            if progress_callback:
                await progress_callback("submit", 0, f"提交远程渲染任务（{len(asset_refs)} 个素材）")

            # ③ 提交 job
            job_id = await self._submit_job(client, base_url, headers, timeline_dict, params, asset_refs)
            logger.info("远程渲染任务已提交: %s", job_id)

            # ④ 轮询
            job = await self._poll_job(client, base_url, headers, job_id, progress_callback,
                                       cancel_id=cancel_id)
            logger.info("远程渲染任务完成: %s", job_id)

            # ⑤ 下载 + 原子写 + ffprobe
            final_path = await self._download_output(client, base_url, headers, job_id, output_path)
            dur = await asyncio.to_thread(_get_actual_duration, str(final_path))
            logger.info("远程渲染产物就绪: %s (%.1fs)", final_path, dur)
            return RenderResult(True, output_path=str(final_path), duration_sec=dur)

    # ── 对外主入口（签名与 RenderService.render 完全一致）────────

    async def render(self, timeline: Timeline, output_path: str | Path = "out.mp4",
                     *, width=1920, height=1080, fps=30.0, bitrate="5M",
                     audio_bitrate="192k", audio_file_path="", bgm_file_path="",
                     progress_callback=None, enable_progress=True,
                     cancel_id: str | None = None,
                     encoder_override: str = "", pix_fmt_override: str = "",
                     force_render: bool = False) -> RenderResult:
        """将 Timeline 渲染为 MP4 —— 与 RenderService.render 签名完全一致（drop-in 替代）。

        - **No-remote 快速路径**：``remote_render_url`` 为空时防御性直接走本地渲染。
          todo 6 集成仅在该配置非空时才实例化本服务，这里兜底保证行为一致。
        - **失败兜底**：远程失败 / 超时按 ``remote_render_fallback`` 决定回退本地
          渲染（原始 timeline/参数）或返回失败结果；**用户取消不触发兜底**。
        """
        from clipwright.config import settings

        url = (settings.remote_render_url or "").strip()
        if not url:
            logger.debug("remote_render_url 为空，走本地渲染 (RemoteRenderService 兜底)")
            return await self._render_local(
                timeline, output_path, width, height, fps, bitrate,
                audio_bitrate, audio_file_path, bgm_file_path,
                progress_callback, enable_progress,
                cancel_id=cancel_id, encoder_override=encoder_override,
                pix_fmt_override=pix_fmt_override,
            )

        try:
            return await self._render_remote(
                url, timeline, output_path, width, height, fps, bitrate,
                audio_bitrate, audio_file_path, bgm_file_path,
                progress_callback, enable_progress,
                cancel_id=cancel_id, encoder_override=encoder_override,
                pix_fmt_override=pix_fmt_override,
            )
        except Exception as e:
            logger.warning("远程渲染失败: %s（fallback=%s）", e, settings.remote_render_fallback)
            from clipwright.services.render import is_render_cancelled
            if is_render_cancelled(cancel_id):
                return RenderResult(False, error="渲染已取消")
            if settings.remote_render_fallback:
                try:
                    return await self._render_local(
                        timeline, output_path, width, height, fps, bitrate,
                        audio_bitrate, audio_file_path, bgm_file_path,
                        progress_callback, enable_progress,
                        cancel_id=cancel_id, encoder_override=encoder_override,
                        pix_fmt_override=pix_fmt_override,
                    )
                except Exception as e2:
                    logger.error("本地渲染兜底失败: %s", e2)
                    return RenderResult(False, error=f"远程渲染失败且本地兜底失败: {e}")
            return RenderResult(False, error=f"远程渲染失败: {e}")
