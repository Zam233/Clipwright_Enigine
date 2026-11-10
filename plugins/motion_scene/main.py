"""代码动效视频插件（M7 骨架）。

职责边界：
- 本插件拥有：视频类型提示词、工程格式（motion/ 命名空间）、场景源码、
  渲染时回调实现、运行时选择；
- engine 只提供：workspace / artifact / job / clip_kind / render RPC 与合成。

渲染路径（MVP）：
  engine 渲染到 motion_scene_clip → 调用 motion_render(request) →
  通过 job broker 跑 ffmpeg 生成透明视频 → 登记 artifact → 返回 artifact_id。
真实生产可把这里的 ffmpeg 换成 Remotion/Node，只需改本文件与 runtime 登记。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from clipwright.category.base import BaseCategoryPlugin
from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.plugins.runtime.bridge import call_host

PLUGIN_ID = "motion_scene"
KIND_ID = "motion_scene_clip"
RENDERER_TOOL = "motion_render"

PROMPTS = {
    "genre": "代码动效视频（工程文件驱动）：画面由可编辑的 React/TSX 场景与时间轴工程生成，"
             "追求确定性、可复现、可局部修改。",
    "structure": "按钩子 → 论点 → 证据/图示 → 实例 → 收尾组织；每镜只承担一个论证任务。",
    "animation": "镜头内单主角动效；入场错峰 0.2–0.5s；关键信息落定后 hold ≥1s；"
                 "优先图表/时间线/逻辑图，避免无信息量的快切与花哨转场。",
    "quality": "数字必须有出处；连续两镜的动效类型/主色/版式至少一项不同；"
               "末帧 hold 不得省略；失败或未验证的结果不得报告为成功。",
}

DECLARES = {"search_hint": "代码动效 工程文件 React 场景"}


class MotionScenePlugin(BasePlugin):
    """入口类必须排在其他 BasePlugin 子类之前：worker 取模块里第一个子类实例化。"""

    def contributions(self):
        return [
            Contribution(ContributionKind.CATEGORY, "motion_scene", MotionCategory(),
                         meta={
                             "display_name": "代码动效视频",
                             "prompts": PROMPTS,
                             "declares": DECLARES,
                         }),
            Contribution(ContributionKind.CLIP_KIND, KIND_ID, None, meta={
                "display_name": "代码场景",
                "track_label": "代码动效",
                "render_contract": "media_overlay.v1",
                "preview_contract": "artifact.v1",
                "renderer_tool": RENDERER_TOOL,
                "capabilities": ["render", "preview"],
            }),
            Contribution(ContributionKind.TOOL, "motion_project_init",
                         ProjectInitTool(), meta={"name": "motion_project_init"}),
            Contribution(ContributionKind.TOOL, "motion_scene_write",
                         SceneWriteTool(), meta={"name": "motion_scene_write"}),
            Contribution(ContributionKind.TOOL, "motion_scene_read",
                         SceneReadTool(), meta={"name": "motion_scene_read"}),
            Contribution(ContributionKind.TOOL, "motion_preview",
                         MotionPreviewTool(), meta={"name": "motion_preview"}),
            Contribution(ContributionKind.TOOL, "motion_scene_validate",
                         MotionValidateTool(), meta={"name": "motion_scene_validate"}),
            Contribution(ContributionKind.TOOL, RENDERER_TOOL,
                         MotionRenderTool(), meta={"name": RENDERER_TOOL}),
        ]


class MotionCategory(BaseCategoryPlugin):
    plugin_id = "motion_scene"
    display_name = "代码动效视频"
    description = "Agent 读写工程文件并用 React/TSX 场景生成动效视频"

    def translate_persona(self, params):
        # Persona → 本类型的剂量参数；MVP 直接透传可识别字段
        data = params.model_dump() if hasattr(params, "model_dump") else dict(params or {})
        return {"cut_density": data.get("cut_density", "medium")}

    def get_shot_params(self, translated):
        return {
            "min_shot_ms": 1200,
            "max_shot_ms": 15000,
            "base_shot_ms": 4000,
            "source": "motion_scene",
            "translated": translated,
        }

    def get_prompt_bundle(self):
        from clipwright.schema.category_spec import PromptBundle, PromptSlot

        return PromptBundle(slots={
            key: PromptSlot(text=text, priority=80) for key, text in PROMPTS.items()
        })

    def get_declares(self):
        from clipwright.schema.category_spec import TypeDeclares

        return TypeDeclares(**DECLARES)


class _ToolBase:
    name = ""
    description = ""

    async def _write(self, path: str, data: str) -> dict:
        return await call_host("workspace.write", {"path": path, "data": data})


class ProjectInitTool(_ToolBase):
    name = "motion_project_init"
    description = "初始化动效工程（motion/project.json + 目录结构）"

    async def execute(self, topic: str = "", width: int = 1920, height: int = 1080,
                      fps: float = 30.0, **kwargs):
        import json

        project = {
            "schema_version": 1,
            "topic": topic,
            "width": int(width),
            "height": int(height),
            "fps": float(fps),
            "scenes": [],
            "runtime": {"renderer": "ffmpeg-alpha", "version": "0.1"},
        }
        await call_host("workspace.mkdir", {"path": "motion/scenes"})
        entry = await self._write(
            "motion/project.json", json.dumps(project, ensure_ascii=False, indent=2),
        )
        return {"success": True, "output": {"project": "motion/project.json",
                                            "revision": entry["revision"]}}


class SceneWriteTool(_ToolBase):
    name = "motion_scene_write"
    description = "写一个场景源码（motion/scenes/scene-XXX.tsx）"

    async def execute(self, scene_id: str = "001", source: str = "",
                      expected_revision: str | None = None, **kwargs):
        if not source.strip():
            return {"success": False, "error": "source 不能为空"}
        path = f"motion/scenes/scene-{scene_id}.tsx"
        try:
            entry = await call_host("workspace.write", {
                "path": path, "data": source, "expected_revision": expected_revision,
            })
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "error": getattr(exc, "code", "") + ": " + str(exc)}
        return {"success": True, "output": {"path": path,
                                            "revision": entry["revision"]}}


class SceneReadTool(_ToolBase):
    name = "motion_scene_read"
    description = "读取场景源码或工程文件"

    async def execute(self, path: str = "motion/project.json", **kwargs):
        try:
            data = await call_host("workspace.read", {"path": path})
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "error": getattr(exc, "code", "") + ": " + str(exc)}
        return {"success": True, "output": {"path": data["path"],
                                            "sha256": data["sha256"],
                                            "data": data["data"]}}


class _RuntimeJobMixin:
    """通过 engine 的通用 job broker 执行插件自有的 Remotion 运行时。"""

    async def _run_runtime(
        self,
        mode: str,
        scene: str,
        out: str,
        *,
        frame: int = 0,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        duration: float = 2.0,
    ) -> dict:
        runtime_dir = Path(__file__).resolve().parent / "runtime"
        script = runtime_dir / "render.mjs"
        if not script.is_file():
            return {"ok": False, "error": f"运行时缺失: {script}"}
        argv = [
            str(script), "--mode", mode, "--scene", scene, "--out", out,
            "--width", str(width), "--height", str(height),
            "--fps", str(fps), "--duration", str(duration),
        ]
        if mode == "still":
            argv += ["--frame", str(frame)]
        parent = str(Path(out).parent).replace("\\", "/")
        await call_host("workspace.mkdir", {"path": parent})
        try:
            job = await call_host("job.submit", {
                "runtime_id": "node",
                "argv": argv,
                "cwd": ".",
                "outputs": [out],
                "timeout_ms": 600000,
            })
        except Exception as exc:  # noqa: BLE001
            return {"ok": False,
                    "error": getattr(exc, "code", "") + ": " + str(exc)}
        job_id = job["job_id"]
        status = job
        for _ in range(1200):
            status = await call_host("job.status", {"job_id": job_id})
            if status["status"] in {"succeeded", "failed", "cancelled",
                                    "timed_out", "interrupted"}:
                break
            await asyncio.sleep(0.5)
        if status["status"] != "succeeded":
            return {"ok": False, "job_id": job_id, "status": status}
        return {"ok": True, "job_id": job_id, "status": status}


class MotionRenderTool(_ToolBase, _RuntimeJobMixin):
    """渲染时回调：engine 渲染到本 kind 时调用，返回 media_overlay 结果。"""

    name = RENDERER_TOOL
    description = "用 Remotion 渲染动效场景为透明视频（engine 渲染回调专用）"

    async def execute(self, request: dict | None = None, **kwargs):
        req = dict(request or {})
        meta = dict(req.get("metadata") or {})
        scene = str(meta.get("scene_path") or "motion/scenes/scene-001.tsx")
        width = int(req.get("width") or 1920)
        height = int(req.get("height") or 1080)
        fps = float(req.get("fps") or 30)
        duration = float(req.get("duration_sec") or 2.0)
        out = "renders/scene.mov"
        result = await self._run_runtime(
            "video", scene, out,
            width=width, height=height, fps=fps, duration=duration,
        )
        if not result.get("ok"):
            return {"success": False, "error": f"渲染失败: {result}"}
        try:
            art = await call_host("artifact.register", {
                "path": out, "role": "render", "job_id": result.get("job_id", ""),
            })
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "error": getattr(exc, "code", "") + ": " + str(exc)}
        return {"success": True, "output": {
            "artifact_id": art["artifact_id"],
            "mime": "video/quicktime",
            "width": width, "height": height, "fps": fps,
            "duration": duration, "alpha": True,
        }}


class MotionPreviewTool(_ToolBase, _RuntimeJobMixin):
    name = "motion_preview"
    description = "渲染场景的一帧静帧（PNG），用于定调帧/预览"

    async def execute(self, scene: str = "motion/scenes/scene-001.tsx",
                      frame: int = 0, width: int = 1920, height: int = 1080,
                      fps: float = 30.0, duration: float = 2.0, **kwargs):
        out = "renders/preview.png"
        result = await self._run_runtime(
            "still", scene, out, frame=int(frame),
            width=int(width), height=int(height), fps=float(fps),
            duration=float(duration),
        )
        if not result.get("ok"):
            return {"success": False, "error": f"预览失败: {result}"}
        try:
            art = await call_host("artifact.register", {
                "path": out, "role": "preview", "job_id": result.get("job_id", ""),
            })
        except Exception as exc:  # noqa: BLE001
            return {"success": False,
                    "error": getattr(exc, "code", "") + ": " + str(exc)}
        return {"success": True, "output": {
            "artifact_id": art["artifact_id"],
            "mime": "image/png", "path": out,
        }}


class MotionValidateTool(_ToolBase, _RuntimeJobMixin):
    name = "motion_scene_validate"
    description = "用运行时编译/渲染 0 帧，验证场景可执行（不登记产物）"

    async def execute(self, scene: str = "motion/scenes/scene-001.tsx",
                      width: int = 320, height: int = 240,
                      fps: float = 30.0, duration: float = 1.0, **kwargs):
        result = await self._run_runtime(
            "still", scene, "renders/validate.png", frame=0,
            width=int(width), height=int(height), fps=float(fps),
            duration=float(duration),
        )
        return {"success": bool(result.get("ok")),
                "output": {"ok": bool(result.get("ok")),
                           "status": result.get("status")}}
