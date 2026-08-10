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
from clipwright.services.web_search import (
    WebSearchService,
    get_web_fetch_service,
    get_web_search_service,
)
from clipwright.tool.base import BaseTool


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
        """仅当搜索服务已配置时可用（依赖配置而非外部命令）。"""
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
        """与搜索同一配置开关：联网能力整体可用时才暴露抓取。"""
        return WebSearchService().is_configured()

    async def execute(
        self,
        url: str,
        max_chars: int = 4000,
        **kwargs: Any,
    ) -> ToolExecResult:
        """抓取 URL 网页正文并提取纯文本。

        Args:
            url: 要抓取的网页地址
            max_chars: 返回正文的最大字符数（默认 4000）
        """
        try:
            content = await get_web_fetch_service().fetch(url, max_chars=max_chars)
            if not content:
                return ToolExecResult(
                    status=ToolStatus.DEPENDENCY_MISSING,
                    tool_name=self.name,
                    output={"content": ""},
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"content": content},
            )
        except Exception as e:
            logger.warning("web_fetch 执行异常: %s", e)
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                output={"content": ""},
            )
