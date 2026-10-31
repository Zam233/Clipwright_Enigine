"""Web 搜索服务 — 可插拔 provider（Bocha 主 / 百度备）+ 网页正文抓取。

提供统一的联网搜索与页面抓取基础设施，供各 Agent（requirements / structure /
animation 等）在需要事实/资料时调用（见 docs/agent-search-cancel.md）。

设计约束：
- 所有公开方法**绝不抛异常**：失败时记录日志并返回空结果，保证各接入点
  "未配置 / 失败 / 无结果" 时与现状逐字节一致。
- Provider 可插拔：`settings.web_search_provider` 选择主 provider，失败自动回退另一家。
- HTTP 客户端一律用 `httpx.AsyncClient`（每次请求新建），不引入新依赖。
"""

from __future__ import annotations

import html
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from clipwright.config import logger, settings

# Bocha 默认端点（可在 .env 通过 WEB_SEARCH_BASE_URL 覆盖）
_BOCHA_DEFAULT_ENDPOINT = "https://api.bochaai.com/v1/web-search"
# 百度千帆 v3 AI Search Web Search 默认端点（可在 .env 通过 WEB_SEARCH_BASE_URL 覆盖）
# 参考千帆 v3 AI Search Web Search API：POST /v2/ai_search/web_search，
# Authorization: Bearer bce-v3/ALTAK-{ak}，请求头 X-Request-Id（UUID 字符串）。
_BAIDU_DEFAULT_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"
# 百度鉴权前缀：key 可能已带 bce-v3/ALTAK- 前缀，缺失时自动补
_BAIDU_AUTH_PREFIX = "bce-v3/ALTAK-"

_DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; ClipWrightBot/1.0)"
_DEFAULT_HEADERS = {"User-Agent": _DEFAULT_USER_AGENT}

# 抓取时需整体剥离的块：<script>...</script> / <style>...</style>
_HTML_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
# 其余所有标签（含注释），替换为空格避免单词粘连
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class WebSearchService:
    """联网搜索服务：按 settings.web_search_provider 选主 provider，失败自动回退。

    - provider = "bocha"（默认）：Bocha Web Search API（Bearer 鉴权）
    - provider = "baidu"：百度千帆 v3 AI Search Web Search API（`Bearer bce-v3/ALTAK-...` 鉴权）
    - 主 provider 失败（非 2xx / 超时 / 解析失败）→ 回退另一 provider
    - 全部失败 → 返回 []（绝不抛异常）

    注意：不缓存 settings 值，所有配置在调用时实时读取（测试/热更新依赖此行为）。
    """

    def __init__(self) -> None:
        pass

    def is_configured(self) -> bool:
        """是否有任何可用 provider（付费 key **或** 允许的本地抓取）。绝不抛异常。

        2026-10-30 变更（PRD `web-search-fetch-prd-2026-10-30.md` M1）：
        旧语义要求"总开关 + API key"，于是没配 key 的机器上 animation/主 agent
        连"能不能联网"都无从判断，工具面也看不到 `web_search`。
        现在只要有一条可用链路（含无需密钥的本地抓取）就算已配置。
        """
        try:
            from clipwright.services import web_providers as wp

            return bool(wp.provider_chain())
        except Exception:
            try:
                enabled = getattr(settings, "enable_web_search", False)
                api_key = getattr(settings, "web_search_api_key", "") or ""
                return bool(enabled) and bool(api_key)
            except Exception:
                return False

    async def search(self, query: str, max_results: int | None = None,
                     *, client_factory: Any = None) -> list[dict[str, Any]]:
        """执行联网搜索，返回归一化结果列表 [{title, url, snippet, score}]。绝不抛异常。

        走 `services.web_providers` 的 provider 链（主 provider → 其余付费 → 本地抓取）；
        全部失败时登记 `web_search_unavailable` 降级，让主 agent 知道"这次没搜到"
        而不是以为"搜过了、就是没有"。
        """
        try:
            n = max_results or getattr(settings, "web_search_max_results", 5) or 5
            n = max(1, min(int(n), 50))  # 夹在 1..50，防异常配置值
        except (TypeError, ValueError):
            n = 5

        from clipwright.services import web_providers as wp

        if not wp.provider_chain():
            self._note_search_degradation(
                "no_provider",
                "没有任何可用的 web 搜索 provider（未配 API key 且本地抓取被关闭）",
            )
            return []

        outcome = await wp.search(query, n, client_factory=client_factory)
        if outcome.results:
            logger.info("web search 命中 provider=%s（attempts=%s）: query=%.80s",
                        outcome.provider, ",".join(outcome.attempts), query)
            return outcome.results

        self._note_search_degradation(
            "all_providers_failed",
            f"全部 provider 无结果（尝试：{','.join(outcome.attempts) or '无'}）；"
            "该段动画只能依靠现有素材与常识",
        )
        return []

    @staticmethod
    def _note_search_degradation(kind: str, detail: str) -> None:
        """搜索失败如实登记（主 agent 可见），不再是"静默返回空列表"。"""
        try:
            from clipwright.services.degradation import note_degradation

            note_degradation("web_search_unavailable", detail=f"{kind}: {detail}"[:300],
                             scope="web_search",
                             impact="事实/数据类动画失去外部依据，可能只能凭文案推断")
        except Exception:  # noqa: BLE001 — 登记失败不影响调用方
            pass

    async def _search_provider(self, provider: str, query: str, count: int) -> list[dict[str, Any]]:
        if provider == "baidu":
            return await self._search_baidu(query, count)
        return await self._search_bocha(query, count)

    # ── Bocha ────────────────────────────────────────────────

    async def _search_bocha(self, query: str, count: int) -> list[dict[str, Any]]:
        """调用 Bocha Web Search API，解析并归一化结果。失败时抛异常（由 search 兜底）。"""
        endpoint = getattr(settings, "web_search_base_url", None) or _BOCHA_DEFAULT_ENDPOINT
        api_key = getattr(settings, "web_search_api_key", "") or ""
        payload = {"query": query, "count": count, "freshness": "oneMonth"}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        logger.debug("Bocha web search: url=%s, count=%d, query=%.100s", endpoint, count, query)
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        items = self._extract_bocha_items(data)
        return [self._normalize(it) for it in items if self._is_result_item(it)]

    @staticmethod
    def _extract_bocha_items(data: Any) -> list[Any]:
        """Bocha 响应解析 — 防御多种嵌套形态。

        官方结构: data.data.webPages.value[]；兼容 data.webPages.value / webPages.value。
        """
        if not isinstance(data, dict):
            return []
        for path in (
            ("data", "data", "webPages", "value"),
            ("data", "webPages", "value"),
            ("webPages", "value"),
        ):
            node: Any = data
            ok = True
            for key in path:
                if not isinstance(node, dict):
                    ok = False
                    break
                node = node.get(key)
            if ok and isinstance(node, list):
                return node
        return []

    # ── Baidu（千帆 v3）──────────────────────────────────────

    async def _search_baidu(self, query: str, count: int) -> list[dict[str, Any]]:
        """调用百度千帆 v3 AI Search Web Search API，解析并归一化结果。
        失败时抛异常（由 search 兜底）。

        端点：POST https://qianfan.baidubce.com/v2/ai_search/web_search
        （可用 WEB_SEARCH_BASE_URL 覆盖）。
        鉴权：Authorization: Bearer bce-v3/ALTAK-{ak}（完整 key 含 bce-v3/ALTAK- 前缀，
        缺失时自动补前缀后再包 Bearer）。请求头 X-Request-Id: <uuid>。
        请求体 {messages, search_source, resource_type_filter}；结果数组在响应顶层 `references`，
        防御式解析兼容各版本字段名。
        """
        endpoint = getattr(settings, "web_search_base_url", None) or _BAIDU_DEFAULT_ENDPOINT
        api_key = getattr(settings, "web_search_api_key", "") or ""
        if api_key and not api_key.startswith(_BAIDU_AUTH_PREFIX):
            api_key = f"{_BAIDU_AUTH_PREFIX}{api_key}"
        payload = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": count}],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Request-Id": str(uuid.uuid4()),
        }
        logger.debug("Baidu web search: url=%s, count=%d, query=%.100s", endpoint, count, query)
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        items = self._extract_baidu_items(data)
        return [self._normalize(it) for it in items if self._is_result_item(it)]

    @staticmethod
    def _extract_baidu_items(data: Any) -> list[Any]:
        """百度千帆响应解析 — 防御多种结果字段名（references/result/results 等）。"""
        if not isinstance(data, dict):
            return []
        for key in ("references", "result", "results", "webSearchResult", "searchResult", "data"):
            found = WebSearchService._find_list(data.get(key))
            if found is not None:
                return found
        return []

    @staticmethod
    def _find_list(node: Any) -> list[Any] | None:
        """在任意嵌套结构中找到结果数组：list 本身 / dict 中常见 key / 兜底任意 list 值。"""
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            for key in ("items", "list", "results", "value", "webPages"):
                v = node.get(key)
                if isinstance(v, list):
                    return v
            for v in node.values():
                if isinstance(v, list):
                    return v
        return None

    # ── 通用 ─────────────────────────────────────────────────

    @staticmethod
    def _is_result_item(item: Any) -> bool:
        """只保留带 url/title/name/link 的结果条目（丢弃无效杂项）。"""
        if not isinstance(item, dict):
            return False
        return bool(item.get("url") or item.get("title") or item.get("name") or item.get("link"))

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        """归一化为 {title, url, snippet, score}（score 恒为 float）。"""
        title = WebSearchService._first(item, "title", "name") or ""
        url = WebSearchService._first(item, "url", "link", "href") or ""
        snippet = WebSearchService._first(
            item, "snippet", "summary", "abstract", "description", "content"
        ) or ""
        try:
            score = float(item["score"]) if item.get("score") is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        return {
            "title": str(title),
            "url": str(url),
            "snippet": str(snippet)[:2000],
            "score": score,
        }

    @staticmethod
    def _first(item: dict[str, Any], *keys: str) -> Any:
        """按顺序取第一个非空字段值。"""
        for key in keys:
            v = item.get(key)
            if v not in (None, ""):
                return v
        return None

    @staticmethod
    def _timeout() -> float:
        try:
            return max(float(getattr(settings, "web_search_timeout", 15) or 15), 1.0)
        except (TypeError, ValueError):
            return 15.0


class WebFetchService:
    """网页正文抓取服务：HTML → 纯文本（去 script/style/标签、解实体、压缩空白、截断）。

    永不抛异常：非 2xx / 超时 / 非 HTML 一律返回 ""。
    """

    async def fetch(self, url: str, max_chars: int = 4000) -> str:
        """抓取并提取 URL 正文文本；失败或非文本时返回空字符串。"""
        rich = await self.probe(url, max_chars=max_chars, want_body=True)
        return str(rich.get("content") or "")

    #: 内容类型 → 语义类别（"抓回来的到底是什么"）
    @staticmethod
    def classify(content_type: str, url: str = "") -> str:
        ct = (content_type or "").split(";")[0].strip().lower()
        if ct in ("text/html", "application/xhtml+xml"):
            return "html"
        if ct in ("text/plain", "text/markdown"):
            return "text"
        if ct in ("application/json", "application/ld+json") or ct.endswith("+json"):
            return "json"
        if ct.startswith("image/"):
            return "image"
        if ct.startswith("video/"):
            return "video"
        if ct.startswith("audio/"):
            return "audio"
        if ct in ("application/pdf", "application/octet-stream", "application/zip"):
            return "file"
        # content-type 缺失/含糊时按扩展名兜底
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in (".mp4", ".webm", ".mov", ".m4v"):
            return "video"
        if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
            return "image"
        if suffix in (".mp3", ".wav", ".m4a", ".aac", ".flac"):
            return "audio"
        if suffix in (".json", ".jsonl"):
            return "json"
        if suffix in (".md", ".txt", ".csv", ".srt", ".vtt"):
            return "text"
        return "file"

    async def probe(self, url: str, *, max_chars: int = 4000,
                    want_body: bool = True) -> dict[str, Any]:
        """探测一个 URL **到底是什么**，并按类别取回可用的东西（2026-10-31）。

        这是 `web_fetch` 的统一入口：网页要正文、图片/视频要落盘、JSON 要原文。
        两类请求分开做，避免"抓媒体时把几十 MB 读进内存再丢掉"：
        - 先 HEAD（拿不到就 Range GET 两字节）确定 content-type / 大小；
        - 只有文本类才真正 GET 正文。

        返回（永不抛异常）：
        `{ok, kind, content_type, bytes, content, final_url, error}`
        `kind` ∈ html/text/json/image/video/audio/file。
        """
        out: dict[str, Any] = {
            "ok": False, "kind": "", "content_type": "", "bytes": 0,
            "content": "", "final_url": url, "error": "",
        }
        ct = ""
        size = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout(), follow_redirects=True) as client:
                # 1) 先探类型（HEAD；不支持 HEAD 的站点退化为 2 字节 Range GET）
                try:
                    head = await client.head(url, headers=_DEFAULT_HEADERS)
                    if head.status_code < 400:
                        ct = (head.headers.get("content-type") or "").split(";")[0].strip().lower()
                        size = int(head.headers.get("content-length") or 0)
                        out["final_url"] = str(head.url)
                    else:
                        head = None
                except Exception:  # noqa: BLE001 — HEAD 不被支持很常见
                    head = None
                if not ct:
                    probe = await client.get(
                        url, headers={**_DEFAULT_HEADERS, "Range": "bytes=0-1"})
                    ct = (probe.headers.get("content-type") or "").split(";")[0].strip().lower()
                    size = int(probe.headers.get("content-length") or 0)
                    out["final_url"] = str(probe.url)
                    if probe.status_code >= 400:
                        out["error"] = f"HTTP {probe.status_code}"
                        return out
                kind = self.classify(ct, url)
                out["kind"] = kind
                out["content_type"] = ct
                out["bytes"] = size
                # 2) 只有文本类才取正文（媒体交给 web_assets.fetch_and_store 落盘）
                if want_body and kind in ("html", "text", "json"):
                    # 网页优先走 DeepSeek 官方 Fetch（返回 Markdown 正文，比本地正则剥标签干净）；
                    # 没配 key / 失败再回落本地 httpx + 正则。
                    if kind == "html":
                        rich = await self._deepseek_fetch(url, max_chars=max_chars)
                        if rich:
                            out.update(rich)
                            out["ok"] = True
                            return out
                    resp = await client.get(url, headers=_DEFAULT_HEADERS)
                    resp.raise_for_status()
                    raw = resp.text
                    out["ok"] = True
                    out["bytes"] = len(raw.encode("utf-8", errors="ignore")) or size
                    out["content"] = (self._html_to_text(raw, max_chars)
                                      if kind == "html" else raw[:max_chars])
                    out["via"] = "local"
                    return out
                out["ok"] = True
                return out
        except Exception as exc:  # noqa: BLE001 — 抓取失败如实回报
            out["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            return out

    async def _deepseek_fetch(self, url: str, *, max_chars: int = 4000) -> dict[str, Any] | None:
        """用 DeepSeek 官方 Fetch（`POST /v1/tools/fetch`）取网页 Markdown 正文。

        返回 `{kind, content, title, via}`；没 key / 任何失败返回 None，
        由调用方回落本地抽正文——**不把"没配 key"当抓取失败**。
        """
        try:
            from clipwright.services import web_providers as wp

            key = wp.provider_key(wp.PROVIDERS["deepseek"])
            if not key:
                return None
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.post(
                    wp.DEEPSEEK_FETCH_ENDPOINT,
                    json={"url": url},
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                )
                if resp.status_code >= 400:
                    logger.info("DeepSeek fetch 失败（回落本地抽取）%s: %s",
                                resp.status_code, wp._deepseek_error(resp))
                    return None
                data = resp.json()
            content = str((data or {}).get("content") or "")
            if not content.strip():
                return None
            return {
                "kind": "html",
                "content": content[:max_chars],
                "title": str((data or {}).get("title") or ""),
                "via": "deepseek_fetch",
                "bytes": len(content.encode("utf-8", errors="ignore")),
            }
        except Exception as exc:  # noqa: BLE001 — 官方 fetch 不可用就回落
            logger.info("DeepSeek fetch 异常（回落本地抽取）: %s: %s", type(exc).__name__, exc)
            return None

    @staticmethod
    def _html_to_text(raw: str, max_chars: int) -> str:
        """HTML → 纯文本：剥离 script/style、去标签、解实体、压缩空白并截断到 max_chars。"""
        if not raw:
            return ""
        text = _HTML_SCRIPT_STYLE_RE.sub(" ", raw)
        text = _HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars]
        return text

    @staticmethod
    def _timeout() -> float:
        try:
            return max(float(getattr(settings, "web_search_timeout", 15) or 15), 1.0)
        except (TypeError, ValueError):
            return 15.0


# ── 模块级单例（与 llm.py _client_cache 相同思路：进程内共享实例）──────────────────

_web_search_instance: WebSearchService | None = None
_web_fetch_instance: WebFetchService | None = None


def get_web_search_service() -> WebSearchService:
    """获取进程级 WebSearchService 单例。"""
    global _web_search_instance
    if _web_search_instance is None:
        _web_search_instance = WebSearchService()
    return _web_search_instance


def get_web_fetch_service() -> WebFetchService:
    """获取进程级 WebFetchService 单例。"""
    global _web_fetch_instance
    if _web_fetch_instance is None:
        _web_fetch_instance = WebFetchService()
    return _web_fetch_instance
