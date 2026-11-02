"""网页媒体抽取（**插件内部实现**，纯函数）。

2026-11-02 用户裁定：`web_media` / `web_images` / `fact_need` 都属于**插件**，
不该沉在 `clipwright/services` 里。所以这份实现随插件走：

```
plugins/web_media/
├── plugin.yaml
├── html_images.py    ← 本文件（抓原 HTML + 抽图地址）
├── web_images.py     ← WebImagesTool（能力 web_images）
├── fact_need.py      ← FactNeedTool（能力 fact_need）
└── main.py           ← 插件类，声明上面两个 tool 贡献
```

本文件只做"取页面 + 抽地址 + 合法性校验"，**不落盘、不下载**：
落盘复用平台的 `clipwright.services.web_assets.fetch_and_store`（robots/SSRF/去重/大小
限制都在那边，属于平台能力，插件不该自己再写一遍）。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from clipwright.config import logger

#: 原始 HTML 抓取上限（1.5MB 足够覆盖首屏 `<img>` 与 og:image）
DEFAULT_MAX_BYTES = 1_500_000
DEFAULT_TIMEOUT = 20.0
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
#: 先剥掉 `<script>` / `<style>` 块：里面的字符串不是图（JS 拼的地址也常带 .png）
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL,
)

#: 图片地址形态（Markdown / 普通 img / 懒加载 / og:image）
_IMAGE_PATTERNS: tuple[str, ...] = (
    r"!\[[^\]]*\]\((https?://[^)\s]+)\)",                        # Markdown 远程图
    r"!\[[^\]]*\]\((/[^)\s]+)\)",                                # Markdown 站内相对图
    r'<img[^>]+(?:data-src|data-original)=["\']([^"\']+)["\']',  # 懒加载
    r'<img[^>]+src=["\']([^"\']+)["\']',                         # 普通 img
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
)


def extract_image_urls(text: str, *, base: str = "", limit: int = 8) -> list[str]:
    """从正文（Markdown / 少量 HTML）里抽出图片地址。

    实际站点常见坑（实测 zol/百度云/smzdm 六页抽不出图）：
    - 相对路径（`/upload/x.jpg`）→ 用页面 URL（`base`）拼绝对地址；
    - 懒加载（`data-src` / `data-original`）→ `src` 里是占位图；
    - `data:` 内联 / `javascript:` → 不是可下载地址，跳过；
    - `<script>` / `<style>` 里的字符串不是图，先剥掉再抽。
    """
    found: list[str] = []
    body = _SCRIPT_STYLE_RE.sub(" ", text or "")
    for pattern in _IMAGE_PATTERNS:
        for m in re.finditer(pattern, body, re.IGNORECASE):
            raw = (m.group(1) or "").strip()
            if not raw or raw.startswith(("data:", "javascript:", "#")):
                continue
            url = urljoin(base or "", raw) if base else raw
            if not url.startswith(("http://", "https://")):
                continue
            if not url.lower().split("?")[0].endswith(_IMAGE_EXTS):
                continue
            if url not in found:
                found.append(url)
                if len(found) >= max(1, int(limit or 8)):
                    return found
    return found


async def fetch_html(
    url: str, *, max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """抓原始 HTML（浏览器 UA、跟随跳转、有大小上限）。失败返回空串。

    为什么不能只靠 `web_fetch`：DeepSeek Fetch 返回的是**已清洗的 Markdown**，
    实测 zol / 百度云 / 什么值得买 清洗后几乎不留图；图片要么在 `og:image`、
    要么在懒加载的 `data-src` 里，只有原 HTML 才看得到。
    """
    if not str(url or "").startswith(("http://", "https://")):
        return ""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml",
            })
            if resp.status_code >= 400:
                return ""
            return resp.text[:max_bytes]
    except Exception as exc:  # noqa: BLE001 — 抽不到图就当没拿到
        logger.info("web_media: 原始 HTML 抓取失败 %s: %s", url, exc)
        return ""


async def page_image_urls(
    url: str, *, limit: int = 4, extracted_markdown: str = "",
) -> list[str]:
    """一个页面的候选图片地址：先看已抓到的正文（Markdown），再退回原 HTML。"""
    text = str(extracted_markdown or "")
    urls = extract_image_urls(text, base=url, limit=limit) if text else []
    if urls:
        return urls[:limit]
    html = await fetch_html(url)
    if not html:
        return []
    return extract_image_urls(html, base=url, limit=limit)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT",
    "extract_image_urls",
    "fetch_html",
    "page_image_urls",
]
