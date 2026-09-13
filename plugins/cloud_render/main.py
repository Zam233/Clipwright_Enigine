"""云端渲染插件 — 将 FFmpeg 渲染卸载到远程服务器。

通过 PRE_RENDER hook 拦截渲染请求，转发到配置的远程渲染服务。
远程服务需实现 HTTP API：POST /render（接收 timeline JSON + 参数），
GET /render/{id}/status（轮询进度），GET /render/{id}/download（下载结果）。

配置（config.yaml）：
  server_url: "https://render-server:8080"
  api_token: "your_token"
"""
from __future__ import annotations
from typing import Any
from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger

class CloudRenderPlugin(CapabilityPlugin):
    manifest = PluginManifest(id="cloud_render", name="Cloud Render", version="1.0.0",
        kind=PluginKind.CAPABILITY, description="Offload FFmpeg rendering to remote server", author="Clipwright Team")

    def contributions(self) -> list[Any]:
        """新契约（P1 迁移）：配好 server_url 才注入云渲染 PRE_RENDER 钩子。"""
        cfg = self.config or {}
        server_url = cfg.get("server_url", "")
        if not server_url:
            return []
        return [Contribution(
            ContributionKind.HOOK, "cloud_render_pre_render",
            self._make_hook(server_url, cfg.get("api_token", "")),
            meta={"point": "pre_render"},
        )]

    @staticmethod
    def _make_hook(server_url: str, token: str):
        def hook(context: dict[str, Any]) -> dict[str, Any]:
            context["cloud_render"] = {"server_url": server_url, "api_token": token, "enabled": True}
            logger.info("[CloudRender] 渲染任务将转发到 %s", server_url)
            return context
        return hook

    def shutdown(self) -> None: pass

__all__ = ["CloudRenderPlugin"]
