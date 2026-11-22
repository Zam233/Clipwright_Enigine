"""`image_generate` / `image_generate_batch` —— 由 image_generate 插件提供的工具。

PRD（image-provider-plugin §8 第二期）：Engine 负责选源、能力校验、落盘、账本与
素材可见性；厂商协议在 `image_provider` 插件里。本工具只做参数校验与结果映射。
"""

from __future__ import annotations

from pathlib import Path

from clipwright.config import logger
from .service import (
    ImageGenerationError,
    ImageGenerationService,
)
from clipwright.tool.base import BaseTool, ToolExecResult, ToolStatus
from clipwright.services import tool_jobs


def _resolve_root(project_id: str = "", workspace_root: str = "") -> Path | None:
    """与 artifacts 工具同款：显式 workspace_root 优先，其次按 project_id 找。"""
    if workspace_root:
        p = Path(str(workspace_root)).expanduser()
        if p.is_dir():
            return p
    if project_id:
        try:
            from clipwright.services.workspace import Workspace

            ws = Workspace(str(project_id))
            if ws.exists:
                return ws.dirs().root
        except Exception as exc:  # noqa: BLE001
            logger.debug("工作区定位失败 %s: %s", project_id, exc)
    return None


class ImageGenerateTool(BaseTool):
    """文生图：产物落工作区 `materials/image/`，同参数重复调用不重复计费。"""

    name = "image_generate"
    description = (
        "根据文字提示生成图片，产物保存到当前项目工作区 materials/image/，"
        "返回相对路径（rel_path）供时间线插入或素材编入。"
        "需要先在「供应商 → AI 生图」配置图像源。"
    )
    capability = "image_generate"
    capability_priority = 10
    permission_tier = "costly"
    agent_callable = True
    dry_run_capable = True
    dependencies: list[str] = []
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "图片描述（主体+环境+风格+光线）"},
            "size": {
                "type": "string",
                "description": "尺寸，如 1024x1024；不填用源/provider 默认（gpt-image-1 用 auto）",
            },
            "n": {"type": "integer", "default": 1, "description": "生成张数（受 provider 上限）"},
            "model": {"type": "string", "description": "模型 id（不填用源绑定/默认模型）"},
            "source_id": {"type": "string", "description": "图像源 id（不填用 image 角色绑定）"},
            "seed": {"type": "integer", "description": "随机种子（可选，用于幂等复用）"},
            # 由工具面在入口自动注入（模型不需要填）——不声明的话 surface 的
            # workspace_root/project_id 注入不会生效，主 agent 调用必报"未找到工作区"。
            "workspace_root": {"type": "string", "description": "工作区根（宿主自动注入）"},
            "project_id": {"type": "string", "description": "项目 id（宿主自动注入）"},
            "task_id": {"type": "string", "description": "任务 id（宿主自动注入）"},
        },
        "required": ["prompt"],
    }

    def is_available(self) -> bool:
        """没有可用图像源时不出现在工具面（与旧生成工具「能力即已配置」一致）。"""
        try:
            from .service import resolve_image_source

            resolve_image_source()
            return True
        except Exception:  # noqa: BLE001 — 未配置/插件未装都按不可用
            return False

    async def execute(
        self,
        *,
        prompt: str = "",
        size: str = "",
        n: int = 1,
        model: str = "",
        source_id: str = "",
        seed: int | None = None,
        project_id: str = "",
        workspace_root: str = "",
        dry_run: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        root = _resolve_root(project_id, workspace_root)
        if root is None:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="未找到工作区（需要 project_id 或 workspace_root 之一，且目录存在）",
            )

        service = ImageGenerationService(root)
        if dry_run:
            try:
                plan = service.plan(prompt=prompt, size=size, n=n,
                                    model=model, source_id=source_id)
            except (ImageGenerationError, Exception) as exc:  # noqa: BLE001
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=str(exc), output={"dry_run": True},
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"dry_run": True, "planned": plan,
                        "workspace": str(root)},
            )

        try:
            outcome = await service.generate(
                prompt=prompt, size=size, n=n, model=model,
                source_id=source_id, seed=seed,
            )
        except Exception as exc:  # noqa: BLE001 — 工具边界不抛异常给 LLM
            logger.exception("image_generate 失败")
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not outcome.ok:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=outcome.error or "图像生成失败",
                output=outcome.as_dict(),
            )

        # 工作区素材源：生成后注册，素材阶段按场景关键词即可检索到
        try:
            from clipwright.services.workspace_materials import ensure_workspace_source

            ensure_workspace_source(root)
        except Exception as exc:  # noqa: BLE001 — 可见性失败不影响产物
            logger.warning("工作区素材源注册失败: %s", exc)

        note = "产物已在 materials/image/；插入时间线请用 timeline_insert_image"
        try:
            from clipwright.services.generated_assets import legacy_image_history_present

            if legacy_image_history_present():
                note += "（检测到旧版 AI 生成历史：只读保留、未迁移）"
        except Exception:  # noqa: BLE001 — 提示失败不影响结果
            pass
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={
                **outcome.as_dict(),
                "workspace": str(root),
                "note": note,
            },
        )


class ImageGenerateBatchTool(BaseTool):
    """批量文生图：逐项返回结果，单项失败不拖垮整批。"""

    name = "image_generate_batch"
    description = (
        "根据多个文字提示批量生成图片，产物保存到当前项目工作区 materials/image/。"
        "返回每个 prompt 的独立结果与失败项；需要先在「供应商 → AI 生图」配置图像源。"
    )
    capability = "image_generate_batch"
    capability_priority = 10
    permission_tier = "costly"
    agent_callable = True
    dry_run_capable = False
    dependencies: list[str] = []
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "图片描述列表（每项主体+环境+风格+光线），最多 20 项",
            },
            "size": {
                "type": "string",
                "description": "尺寸，如 1024x1024；不填用源/provider 默认",
            },
            "n": {"type": "integer", "default": 1, "description": "每个 prompt 生成张数"},
            "model": {"type": "string", "description": "模型 id（不填用源绑定/默认模型）"},
            "source_id": {"type": "string", "description": "图像源 id（不填用 image 角色绑定）"},
            "seed": {"type": "integer", "description": "随机种子（可选）"},
            "concurrency": {"type": "integer", "default": 2, "description": "并发数（1-4）"},
            "insert": {"type": "boolean", "default": False,
                       "description": "生成后是否批量插入当前任务时间线（需 task_id）"},
            "workspace_root": {"type": "string", "description": "工作区根（宿主自动注入）"},
            "project_id": {"type": "string", "description": "项目 id（宿主自动注入）"},
            "task_id": {"type": "string", "description": "任务 id（宿主自动注入）"},
        },
        "required": ["prompts"],
    }

    def is_available(self) -> bool:
        try:
            from .service import resolve_image_source

            resolve_image_source()
            return True
        except Exception:  # noqa: BLE001 — 未配置/插件未装都按不可用
            return False

    async def execute(
        self,
        *,
        prompts: list[str] | None = None,
        size: str = "",
        n: int = 1,
        model: str = "",
        source_id: str = "",
        seed: int | None = None,
        concurrency: int = 2,
        insert: bool = False,
        task_id: str = "",
        project_id: str = "",
        workspace_root: str = "",
        dry_run: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        if dry_run:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="image_generate_batch 不支持 dry_run",
            )
        root = _resolve_root(project_id, workspace_root)
        if root is None:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="未找到工作区（需要 project_id 或 workspace_root 之一，且目录存在）",
            )
        clean = [str(p).strip() for p in (prompts or []) if str(p).strip()]
        if not clean:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="prompts 不能为空",
            )
        if len(clean) > 20:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"prompts 最多 20 项（收到 {len(clean)}）",
            )

        service = ImageGenerationService(root)
        job = tool_jobs.create("image_generate_batch", len(clean))
        heartbeat = tool_jobs.start_heartbeat(task_id, job)
        try:
            outcomes = await service.generate_many(
                prompts=clean, size=size, n=n, model=model,
                source_id=source_id, seed=seed, concurrency=concurrency,
            )
        finally:
            tool_jobs.stop_heartbeat(heartbeat)
        items = [{"index": i, **o.as_dict()} for i, o in enumerate(outcomes)]
        progress = {"ok": 0, "failed": 0}
        for item in items:
            if item.get("ok"):
                progress["ok"] += 1
            else:
                progress["failed"] += 1
            tool_jobs.update(job.job_id, completed=progress["ok"],
                             failed=progress["failed"])
        tool_jobs.emit_progress(task_id, job)

        ok_count = sum(1 for x in items if x.get("ok"))
        failed = [x for x in items if not x.get("ok")]
        tool_jobs.finish(job.job_id, failed=len(failed),
                         error="" if ok_count else "所有 prompt 均生成失败")
        try:
            from clipwright.services.workspace_materials import ensure_workspace_source

            ensure_workspace_source(root)
        except Exception as exc:  # noqa: BLE001 — 可见性失败不影响产物
            from clipwright.config import logger

            logger.warning("工作区素材源注册失败: %s", exc)

        output = {
            "job_id": job.job_id,
            "items": items,
            "summary": {"total": len(items), "ok": ok_count, "failed": len(failed)},
            "workspace": str(root),
            "note": "产物已在 materials/image/；插入时间线请用 timeline_insert_images",
        }
        if ok_count == 0:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="所有 prompt 均生成失败", output=output,
            )
        if insert and task_id:
            try:
                from clipwright.tool.registry import ToolRegistry

                refs = [x.get("rel_path") for x in items
                        if x.get("ok") and x.get("rel_path")]
                inserted = await ToolRegistry.execute(
                    "timeline_insert_images", task_id=task_id,
                    items=[{"asset_ref": p} for p in refs],
                )
                output["insert"] = getattr(inserted, "output", None) or {}
            except Exception as exc:  # noqa: BLE001 — 插入失败不回滚图片
                output["insert_error"] = f"{type(exc).__name__}: {exc}"[:300]
        elif insert:
            output["insert_error"] = "insert=true 但缺少 task_id"
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output=output)
