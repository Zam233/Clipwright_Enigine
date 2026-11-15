"""`image_generate` 工具 —— 主 Agent 的受控文生图入口（第二期）。

PRD（image-provider-plugin §8 第二期）：Engine 负责选源、能力校验、落盘、账本与
素材可见性；厂商协议在 `image_provider` 插件里。本工具只做参数校验与结果映射。
"""

from __future__ import annotations

from pathlib import Path

from clipwright.config import logger
from clipwright.services.image_generation import (
    ImageGenerationError,
    ImageGenerationService,
)
from clipwright.tool.base import BaseTool, ToolExecResult, ToolStatus


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
        },
        "required": ["prompt"],
    }

    def is_available(self) -> bool:
        """没有可用图像源时不出现在工具面（与旧生成工具「能力即已配置」一致）。"""
        try:
            from clipwright.services.image_generation import resolve_image_source

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
