"""`web_images` 能力（插件提供）：从一个网页里拿**真实图片**并落进工作区素材库。

为什么是插件而不是内置工具（用户 2026-11-02 裁定）：抓网页、解析 HTML、抽 `<img>`、
落盘素材属于**可替换的能力**——不同团队会有自己的实现（用 Playwright 渲染、
用图床 API、用内部代理…）。写在 engine 里等于把实现钉死，插件想接管只能改源码。
现在只要给工具声明 `capability = "web_images"` 并注册进来，`capabilities.resolve()`
就会在**调用时刻**解析到这个实现，装卸立即生效。
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool

from .html_images import page_image_urls

#: 小于这个字节数的"图片"通常是 logo/图标/1x1 占位，进动画只会糊成一块
MIN_IMAGE_BYTES = 8000
DEFAULT_MAX_IMAGES = 4


def _workspace_root(workspace_root: str, project_id: str) -> str:
    root = str(workspace_root or "").strip()
    if root:
        return root
    pid = str(project_id or "").strip()
    if not pid:
        return ""
    try:
        from clipwright.services.workspace import Workspace

        ws = Workspace(pid)
        if ws.exists:
            return str(ws.dirs().root)
    except Exception as exc:  # noqa: BLE001 — 定位失败就返回空
        logger.debug("web_images: 工作区定位失败 %s: %s", pid, exc)
    return ""


class WebImagesTool(BaseTool):
    """从网页抽图片并落盘到 `<workspace>/materials/web/`。"""

    name = "web_images"
    #: 语义能力名：agent 用 `call_tool("web_images")` 解析，不写死工具名
    capability = "web_images"
    capability_priority = 50
    permission_tier = "costly"
    dry_run_capable = True
    agent_callable = True
    dependencies: list[str] = []
    description = (
        "从一个网页里抓取真实图片并落到工作区素材库。参数：`url`（页面地址）；"
        "可选 `max_images`（默认 4）、`download`（默认 true，false 只返回候选地址）、"
        "`min_bytes`（默认 8000，太小的图标会被跳过）、`title`（素材标题前缀）。"
        "返回 `{page_url, found[], saved[{path,url,bytes,license}], skipped[]}`。"
    )

    def is_available(self) -> bool:
        """httpx 已随项目安装；缺它时才判不可用（插件不该因为环境探测恒 False 消失）。"""
        try:
            import httpx  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    async def execute(
        self,
        url: str = "",
        max_images: int = DEFAULT_MAX_IMAGES,
        download: bool = True,
        min_bytes: int = MIN_IMAGE_BYTES,
        title: str = "",
        workspace_root: str = "",
        project_id: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        target = str(url or "").strip()
        if not target.startswith(("http://", "https://")):
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error="需要 http(s) 页面地址")
        limit = max(1, min(int(max_images or DEFAULT_MAX_IMAGES), 12))

        found = await page_image_urls(target, limit=limit)
        if not found:
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"page_url": target, "found": [], "saved": [], "skipped": [],
                        "note": "页面里没有可下载的图片（Markdown 与原始 HTML 都查过）"},
            )
        if not download:
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"page_url": target, "found": found, "saved": [], "skipped": []},
            )

        root = _workspace_root(workspace_root, project_id)
        if not root:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="download=true 需要 workspace_root（或 project_id）以确定落盘位置",
            )
        from clipwright.services.web_assets import fetch_and_store

        # 注意：`fetch_and_store(root=...)` 自己会拼 `materials/web/<kind>/`
        # （见 `web_assets._materials_root()`）。所以这里必须传**工作区根**；
        # 传 `<root>/materials/web` 会落成 `materials/web/materials/web/images/…`（实测踩过）。
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        used_bytes = 0
        for img_url in found:
            if len(saved) >= limit:
                break
            try:
                res = await fetch_and_store(img_url, root=root,
                                            title=f"{title or '联网图片'}".strip())
            except Exception as exc:  # noqa: BLE001 — 单张失败不影响其它
                skipped.append({"url": img_url, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            if not getattr(res, "ok", False):
                skipped.append({"url": img_url,
                                "reason": str(getattr(res, "error", "") or "抓取失败")})
                continue
            # `IngestResult.kind` 是目录名（images / videos / pages），不是单数 image
            kind = str(getattr(res, "kind", "") or "")
            size = int(getattr(res, "bytes", 0) or 0)
            if kind not in ("images", "image"):
                skipped.append({"url": img_url, "reason": f"不是图片（{kind or 'unknown'}）"})
                continue
            if size < int(min_bytes or 0):
                skipped.append({"url": img_url,
                                "reason": f"过小（{size}B < {min_bytes}B，多为图标）"})
                continue
            saved.append({
                "path": str(getattr(res, "local_path", "") or ""),
                "url": img_url,
                "bytes": size,
                "kind": kind,
                "license": str(getattr(res, "license", "") or ""),
            })
            used_bytes += size
        logger.info("web_images: %s → 候选 %d / 落盘 %d / 跳过 %d（共 %d 字节）",
                    target, len(found), len(saved), len(skipped), used_bytes)
        return ToolExecResult(
            status=ToolStatus.SUCCESS, tool_name=self.name,
            output={"page_url": target, "found": found, "saved": saved,
                    "skipped": skipped, "bytes": used_bytes},
        )


__all__ = ["DEFAULT_MAX_IMAGES", "MIN_IMAGE_BYTES", "WebImagesTool"]
