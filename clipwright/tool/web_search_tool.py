"""联网搜索 / 网页抓取 Agent 工具 — 提供事实与资料检索能力。

基于 clipwright.services.web_search 的 WebSearchService / WebFetchService：
- WebSearchTool: 关键词联网搜索，返回归一化结果列表（供 LLM 引用）
- WebFetchTool: 抓取网页正文纯文本，供搜索结果深读

设计约束：与服务层一致，**绝不抛异常**。未配置 / 无结果 / 失败时返回
ToolStatus.DEPENDENCY_MISSING 与空结果，供 Agent 判断当前环境不具备联网能力。
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
# 惰性引用（2026-10-30）：直接 `from ... import get_web_search_service` 会在 import 时绑定符号，
# 只能靠"patch 本模块符号"注入桩。这里同时保留**本模块的包装函数**（旧测试/部署可继续
# patch `web_search_tool.get_web_search_service`）与模块属性访问（可 patch 服务模块）。
from clipwright.services import web_search as _web_search_module
from clipwright.tool.base import BaseTool

WebSearchService = _web_search_module.WebSearchService


def get_web_search_service():
    """薄包装：本模块符号可被替换（测试/部署注入），内部读服务模块的当前实现。"""
    return _web_search_module.get_web_search_service()


def get_web_fetch_service():
    return get_web_fetch_service()


class WebSearchTool(BaseTool):
    """关键词联网搜索工具。"""

    name = "web_search"
    agent_callable = True
    description = (
        "联网搜索网络获取最新/事实信息，返回相关网页结果列表（标题、URL、摘要）。"
        "当需要查询实时新闻、百科事实、资料引用或任何超出模型知识范围的信息时调用。"
        "注意：未配置搜索服务时返回空结果列表。"
    )

    def is_available(self) -> bool:
        """工具本体始终可用；**配置缺失在 execute 里如实回报**。

        旧语义把它当"不可用"，后果是 Kernel 的工具面里根本没有 web_search，
        主 agent 永远不知道自己能联网——连"我想搜一下"都无从决策。
        `is_available()` 的语义应当是"依赖的二进制在不在"，不是"配置填没填"。
        阶段 Agent 的可见性由 `ToolRegistry.list_agent_callable()` 用 `is_configured()` 把关。
        """
        return True

    def is_configured(self) -> bool:
        """搜索服务是否已配置（阶段 Agent 用它决定是否暴露该工具）。"""
        return WebSearchService().is_configured()

    async def execute(
        self,
        query: str,
        max_results: int = 5,
        **kwargs: Any,
    ) -> ToolExecResult:
        """执行联网搜索并返回归一化结果列表。

        Args:
            query: 搜索关键词
            max_results: 期望返回的最大结果条数（默认 5）
        """
        try:
            service = get_web_search_service()
            if not service.is_configured():
                logger.debug("web_search: 搜索未配置，返回空结果")
                return ToolExecResult(
                    status=ToolStatus.DEPENDENCY_MISSING,
                    tool_name=self.name,
                    output={"results": []},
                )
            results = await service.search(query, max_results=max_results)
            normalized = [
                {
                    "title": str(r.get("title", "")),
                    "url": str(r.get("url", "")),
                    "snippet": str(r.get("snippet", "")),
                }
                for r in results
            ]
            if not normalized:
                return ToolExecResult(
                    status=ToolStatus.DEPENDENCY_MISSING,
                    tool_name=self.name,
                    output={"results": []},
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"results": normalized},
            )
        except Exception as e:
            logger.warning("web_search 执行异常: %s", e)
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                output={"results": []},
            )


class WebFetchTool(BaseTool):
    """网页正文抓取工具（search 结果的深读）。"""

    name = "web_fetch"
    agent_callable = True
    description = (
        "抓取指定网页的正文纯文本内容（去除导航/脚本/样式），用于对搜索结果进行深读。"
        "当 web_search 返回的结果需要阅读全文时调用；非 HTML 页面或抓取失败返回空内容。"
    )

    def is_available(self) -> bool:
        """同 web_search：本体可用，缺配置由 execute 如实报 DEPENDENCY_MISSING。"""
        return True

    def is_configured(self) -> bool:
        """抓取本身不需要搜索 API key（2026-10-30 修正）。

        旧实现复用了 `WebSearchService().is_configured()` —— 于是"搜索没配 key"
        连"抓一个指定 URL"都被判成不可用，`resolve("web_fetch")` 解析不到，
        所有 Agent 都拿不到这个能力。抓取是本地 HTTP + 正文抽取，不依赖第三方鉴权；
        真失败（网络/域名受限）由 `execute` 如实回报。
        """
        return True

    async def execute(
        self,
        url: str,
        max_chars: int = 4000,
        save_to_materials: bool = False,
        workspace_root: str = "",
        title: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        """抓取 URL 网页正文；可选**落盘为素材**（图片/视频/正文）。

        Args:
            url: 要抓取的网页地址
            max_chars: 返回正文的最大字符数（默认 4000）
            save_to_materials: True 时把内容按类型存进 `<workspace>/materials/web/`
                （图片/视频存二进制，其余存 markdown），并写 `.meta.json` 记录
                来源、许可、sha256、抓取时间（PRD web-search-fetch M2）
            workspace_root: 工作区根（save_to_materials 必填；由工具面自动注入）
            title: 素材标题（用于文件名与 meta）
        """
        try:
            content = await get_web_fetch_service().fetch(url, max_chars=max_chars)
            output: dict[str, Any] = {"content": content}
            status = ToolStatus.SUCCESS if content else ToolStatus.DEPENDENCY_MISSING
            if not content:
                output["content"] = ""
            if save_to_materials:
                if not workspace_root:
                    output["saved"] = {"ok": False, "error": "缺少 workspace_root，无法落盘"}
                else:
                    from clipwright.services import web_assets

                    saved = await web_assets.fetch_and_store(
                        url, root=workspace_root, title=title,
                    )
                    output["saved"] = saved.to_public()
                    if saved.ok:
                        status = ToolStatus.SUCCESS
            return ToolExecResult(
                status=status,
                tool_name=self.name,
                output=output,
            )
        except Exception as e:
            logger.warning("web_fetch 执行异常: %s", e)
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                output={"content": ""},
            )


