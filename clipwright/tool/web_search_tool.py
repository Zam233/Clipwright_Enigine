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
    """薄包装：本模块符号可被替换（测试/部署注入），内部读服务模块的当前实现。

    **这里是 self-recursion 的老坑（2026-10-31 实测）**：旧实现写成
    `return get_web_fetch_service()` —— 函数自己调自己，每次 `web_fetch` 都以
    `RecursionError: maximum recursion depth exceeded` 收场；而 `execute` 把任何异常
    都翻译成 `DEPENDENCY_MISSING + 空 content`，于是主 agent 那边看到的是
    「web_fetch 直接失败」，整整一条联网深读能力等于不存在。
    """
    return _web_search_module.get_web_fetch_service()


class WebSearchTool(BaseTool):
    """关键词联网搜索工具。"""

    name = "web_search"

    permission_tier = "costly"
    agent_callable = True
    #: 语义能力名 + 权限档位**自声明**（外网读取：costly；引擎 TIER_MAP 不再需要登记）
    capability = "web_search"
    permission_tier = "costly"
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
            # 归一化但**不丢字段**：DeepSeek 的 rerank_score/summary/content/authority/date
            # 对 agent 判断"这条靠不靠谱、要不要读正文"很关键（旧实现只留 title/url/snippet，
            # 于是 `score` 恒为 None、`content` 全被丢掉）。
            normalized = []
            for r in results:
                item = {
                    "title": str(r.get("title", "")),
                    "url": str(r.get("url", "")),
                    "snippet": str(r.get("snippet", "")),
                }
                for key in ("score", "summary", "content", "authority", "date", "site_name"):
                    if r.get(key) not in (None, ""):
                        item[key] = r[key]
                normalized.append(item)
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
    #: 语义能力名 + 权限档位**自声明**（外网读取：costly；引擎 TIER_MAP 不再需要登记）
    capability = "web_fetch"
    permission_tier = "costly"
    description = (
        "抓取任意 URL：网页 → 正文纯文本（去导航/脚本/样式）；JSON → 原文 + 解析结果；"
        "图片/视频/音频 → 自动落进工作区 materials/web/ 并返回 local_path、字节数、许可。"
        "给各 agent 共用：需要素材（image_clip / video_trim 的输入）就传 workspace_root 拿 local_path，"
        "需要事实信息就读 content；抓不到时 output 里有 kind/content_type/error，不吞原因。"
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
        save_to_materials: bool | None = None,
        workspace_root: str = "",
        title: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        """抓一个 URL —— **网页出正文、图片/视频/音频落盘、JSON 出原文**（统一入口）。

        用户 2026-10-31：`web_fetch` 必须"能抓视频、也能抓网页/信息/图片给各个 agent 用"。
        先探测内容类型（HEAD / Range 两字节），再按类别分流：

        | kind | 处理 | 返回 |
        |---|---|---|
        | `html` | DeepSeek 官方 Fetch（Markdown）优先，失败回落本地抽正文 | `content` |
        | `text` / `json` | 原文 | `content`（json 另给解析结果 `json`） |
        | `image` / `video` / `audio` | **默认落盘**到 `<workspace>/materials/web/` | `local_path` + `bytes` + `license` + `sha256` |
        | `file`（pdf/zip…） | 落原文件 | `local_path` + `content_type` |

        `save_to_materials`：
        - `None`（默认）= 媒体照常落盘、网页正文不落盘（"读一眼"不该在磁盘留垃圾）；
        - `True` = 连网页正文也存成 `materials/web/pages/*.md`（做资料归档时用）；
        - `False` = 什么都不落盘（只读）。
        """
        try:
            service = get_web_fetch_service()
            probe = await service.probe(url, max_chars=max_chars, want_body=True)
            kind = str(probe.get("kind") or "")
            output: dict[str, Any] = {
                "url": url,
                "final_url": probe.get("final_url") or url,
                "kind": kind or "unknown",
                "content_type": probe.get("content_type") or "",
                "via": probe.get("via") or "",
                "fetched": bool(probe.get("ok")),
                "content": probe.get("content") or "",
                "bytes": int(probe.get("bytes") or 0),
            }
            if probe.get("title"):
                output["title"] = probe.get("title")
            if probe.get("error"):
                output["error"] = probe.get("error")

            # JSON：顺手给结构化结果，agent 不用自己再 json.loads
            if kind == "json" and output["content"]:
                try:
                    import json as _json

                    output["json"] = _json.loads(output["content"])
                except Exception:  # noqa: BLE001 — 不是合法 JSON 就只给原文
                    pass

            is_media = kind in ("image", "video", "audio", "file")
            want_saved = (save_to_materials is not False) and (
                is_media or save_to_materials is True
            )
            if probe.get("ok") and want_saved:
                if not workspace_root:
                    output["saved"] = {
                        "ok": False,
                        "error": "缺少 workspace_root：媒体只能落盘才能被 edit/image_clip 使用",
                    }
                    if is_media:
                        output["hint"] = "带 workspace_root 再调一次即可落盘并返回 local_path"
                else:
                    from clipwright.services import web_assets

                    saved = await web_assets.fetch_and_store(
                        url, root=workspace_root, title=title, skip_robots=True,
                    )
                    output["saved"] = saved.to_public()
                    if saved.ok:
                        # 顶层直接给 local_path：调用方（material/edit）不用再钻 saved 里找。
                        # 注意 `kind` 保持语义（image/video/html），落盘子目录放 `saved_kind`
                        # ——返回 "images"/"videos" 这种目录名会让 agent 判断错素材类型。
                        output["local_path"] = saved.local_path
                        output["saved_kind"] = saved.kind
                        output["license"] = saved.license
                        output["sha256"] = saved.sha256
                        output["bytes"] = saved.bytes or output["bytes"]

            if not probe.get("ok"):
                return ToolExecResult(
                    status=ToolStatus.DEPENDENCY_MISSING,
                    tool_name=self.name,
                    output=output,
                )
            if not output["content"] and is_media and not output.get("local_path"):
                output["hint"] = ("这是媒体但没落盘：带 workspace_root 重调，"
                                  "拿到 local_path 后 material/edit 才能用")
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output=output,
            )
        except Exception as e:
            # 异常**必须带原因**（旧实现只留一句空 output，外界只能看到"web_fetch 直接失败"；
            # 实际根因是这条链路自己递归爆栈，删掉日志等于把线索一起删掉）。
            logger.warning("web_fetch 执行异常: %s: %s", type(e).__name__, e)
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                output={"content": "", "fetched": False,
                        "error": f"{type(e).__name__}: {str(e)[:200]}"},
            )


