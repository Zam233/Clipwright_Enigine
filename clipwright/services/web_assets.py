"""网页素材化：把 web 搜索结果/正文落进项目 `materials/`（PRD web-search-fetch M2）。

为什么需要它（2026-10-30 核查）：动画 Agent 的联网只把 3 条摘要塞进 prompt，
搜索结果里的图片/视频/正文**从不落盘**，于是"动画自己找素材"实际不成立——
素材仍然只能来自 `MaterialRegistry` 的图库源。

本模块做三件事：
1. **SSRF/协议治理**：只允许 http/https、拒绝 localhost/私网/链路本地地址；
2. **下载即落盘**：按内容类型分目录（`materials/web/images|videos|pages`），
   文件名用 sha256 前缀去重，写 `.meta.json` 旁车记录来源、许可、抓取时间、字节数；
3. **诚实标注许可**：只有明确已知的图库域名（pexels/wikimedia 等）才给许可名，
   其余一律 `unknown`——宁可让主 agent 看到"许可未知"，也不伪造授权。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import httpx

from clipwright.config import logger

#: 单文件上限（20MB）：超过视为非法/不适合当素材
MAX_BYTES = 20 * 1024 * 1024
#: 允许的媒体内容类型 → 子目录
MEDIA_DIRS = {
    "image/jpeg": "images",
    "image/png": "images",
    "image/webp": "images",
    "image/gif": "images",
    "video/mp4": "videos",
    "video/webm": "videos",
}
#: 已知图库域名 → 许可标注（其余 unknown，须人工确认）
KNOWN_LICENSES = {
    "pexels.com": "Pexels License",
    "images.pexels.com": "Pexels License",
    "videos.pexels.com": "Pexels License",
    "upload.wikimedia.org": "见 Wikimedia 文件页（多为 CC BY-SA）",
    "commons.wikimedia.org": "见 Wikimedia 文件页（多为 CC BY-SA）",
}
_DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ClipWrightBot/1.0)"}
_SLUG_RE = re.compile(r"[^a-zA-Z0-9\u4e00-\u9fff]+")

#: robots.txt 文本缓存：host → (文本, 到期 monotonic)。
#: 注意缓存的是**文本**不是判定结果——同站不同路径的结论可能相反。
_ROBOTS_CACHE: dict[str, tuple[str, float]] = {}
#: robots 缓存秒数（同一次任务里不会反复问同一个域名）
ROBOTS_TTL_SEC = 1800.0
#: 站内 URL 命中数上限（防止 ingest 一堆同站页面把磁盘写满）
MAX_PER_HOST = 4


def _setting(name: str, default: Any) -> Any:
    """读配置（配置层没有该字段时用默认值，测试可直接 monkeypatch 本函数）。"""
    try:
        from clipwright.config import settings

        value = getattr(settings, name, None)
        return default if value is None else value
    except Exception:  # noqa: BLE001 — 配置不可用时按默认治理策略执行
        return default


def _split_domains(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple, set)):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"[,;\s]+", str(raw or ""))
    return [i.strip().lower().lstrip(".") for i in items if i.strip()]


def host_allowed(url: str) -> tuple[bool, str]:
    """域名治理：白名单（非空时只放行其中域名）+ 黑名单（永远拒）。

    白名单为空 = 只要公网就放行（默认），与 SSRF 检查叠加而不是替代它。
    """
    host = (urlparse(str(url or "")).hostname or "").lower()
    if not host:
        return False, "URL 没有主机名"
    denied = _split_domains(_setting("web_assets_denied_domains", ""))
    for d in denied:
        if host == d or host.endswith("." + d):
            return False, f"域名 {host} 在拒绝清单里"
    allow = _split_domains(_setting("web_assets_allowed_domains", ""))
    if allow and not any(host == d or host.endswith("." + d) for d in allow):
        return False, f"域名 {host} 不在允许清单里"
    return True, ""


def _robots_decide(path: str, text: str) -> tuple[bool, str]:
    """按 robots.txt 文本判定某路径（不支持通配，够用且不假装聪明）。

    只处理 `User-agent: *`（或 ClipWrightBot）段里的 `Disallow`/`Allow`；
    **最长匹配优先**，同长时 Allow 胜出（与主流实现一致）。
    """
    best_len, best_allow = -1, True
    applies = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            applies = value.lower() in ("*", "clipwrightbot")
            continue
        if not applies or key not in ("allow", "disallow") or not value:
            continue
        if path.startswith(value) and len(value) > best_len:
            best_len, best_allow = len(value), key == "allow"
    if best_len >= 0 and not best_allow:
        return False, f"robots.txt 禁止抓取 {path}"
    return True, ""


def _fetch_robots_text(target: str) -> str:
    """真正去拿 robots.txt（测试里可整体替换，避免联网）。"""
    resp = httpx.get(target, headers=_DEFAULT_HEADERS, timeout=8.0, follow_redirects=True)
    if resp.status_code >= 400:
        return ""
    return resp.text


def robots_allows(url: str, body_getter: Callable[[str], str] | None = None) -> tuple[bool, str]:
    """同步版 robots 判定（带 TTL 缓存）。`respect_robots=false` 时直接放行。"""
    if not bool(_setting("web_assets_respect_robots", True)):
        return True, "未启用 robots 检查"
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    now = time.monotonic()
    cached = _ROBOTS_CACHE.get(host)
    if cached and cached[1] > now:
        return _robots_decide(path, cached[0])
    try:
        text = (body_getter or _fetch_robots_text)(
            f"{parsed.scheme}://{parsed.netloc}/robots.txt") or ""
    except Exception:  # noqa: BLE001 — robots 拿不到就按允许处理（不阻断正常抓取）
        return True, "robots 不可读，按允许处理"
    _ROBOTS_CACHE[host] = (text, now + ROBOTS_TTL_SEC)
    return _robots_decide(path, text)


def cached_copy(root: str | Path, url: str, ttl_hours: float | None = None) -> Path | None:
    """缓存治理：同 URL 的旁车里记录过、且未过期，就直接复用不再下载。"""
    if ttl_hours is None:
        ttl_hours = float(_setting("web_assets_cache_ttl_hours", 168) or 0)
    if ttl_hours <= 0:
        return None
    base = _materials_root(root)
    if not base.is_dir():
        return None
    deadline = time.time() - ttl_hours * 3600
    for sidecar in base.glob("*/*.meta.json"):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏旁车跳过
            continue
        if str(meta.get("url") or "") != str(url):
            continue
        media = Path(str(sidecar)[: -len(".meta.json")])
        if media.is_file() and media.stat().st_mtime >= deadline:
            return media
    return None


def clear_robots_cache() -> None:
    """测试用。"""
    _ROBOTS_CACHE.clear()



@dataclass
class IngestResult:
    ok: bool
    kind: str = ""            # images / videos / pages
    local_path: str = ""
    url: str = ""
    title: str = ""
    license: str = "unknown"
    sha256: str = ""
    bytes: int = 0
    content_type: str = ""
    error: str = ""
    meta_path: str = ""
    cached: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "kind": self.kind, "local_path": self.local_path,
            "url": self.url, "title": self.title, "license": self.license,
            "sha256": self.sha256, "bytes": self.bytes,
            "content_type": self.content_type, "error": self.error,
            "meta_path": self.meta_path, "cached": self.cached,
        }


def is_public_url(url: str) -> bool:
    """SSRF 防护：只允许 http/https + 公网主机（IP 直连必须不是私网/环回/链路本地）。"""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # 域名：交给 DNS 与网络策略，不做 DNS 解析（避免 TOCTOU）
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def license_of(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower()
    for domain, label in KNOWN_LICENSES.items():
        if host == domain or host.endswith("." + domain):
            return label
    return "unknown（需人工确认授权）"


def _slug(text: str, fallback: str = "asset") -> str:
    cleaned = _SLUG_RE.sub("-", str(text or "")).strip("-")
    return (cleaned or fallback)[:48]


def _materials_root(root: str | Path) -> Path:
    return Path(root) / "materials" / "web"


def _guess_kind(content_type: str, url: str) -> tuple[str, str]:
    """内容类型 → (子目录, 扩展名)；未知类型返回 ("", "")。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in MEDIA_DIRS:
        ext = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm",
        }[ct]
        return MEDIA_DIRS[ct], ext
    # 内容类型缺失时按扩展名兜底
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return "images", (".jpg" if suffix == ".jpeg" else suffix)
    if suffix in (".mp4", ".webm"):
        return "videos", suffix
    return "", ""


def _write_sidecar(path: Path, meta: dict[str, Any]) -> str:
    sidecar = path.with_suffix(path.suffix + ".meta.json")
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(sidecar)


async def fetch_and_store(
    url: str,
    *,
    root: str | Path,
    title: str = "",
    client_factory: Callable[..., Any] | None = None,
    max_bytes: int = MAX_BYTES,
    skip_robots: bool = False,
) -> IngestResult:
    """抓取一个 URL 并按类型落盘（图片/视频二进制，其余当正文存 markdown）。

    `skip_robots=True` 只用于**素材源 API 直接返回的媒体文件 URL**（那是接口给的
    下载地址，不是爬站页面）：省掉 robots.txt 的网络往返，路径治理/SSRF 检查照旧。
    """
    if not is_public_url(url):
        return IngestResult(ok=False, url=url, error="URL 不被允许（协议/私网/环回）")
    ok_host, host_why = host_allowed(url)
    if not ok_host:
        return IngestResult(ok=False, url=url, error=host_why)
    if not skip_robots:
        ok_robots, robots_why = robots_allows(url)
        if not ok_robots:
            return IngestResult(ok=False, url=url, error=robots_why)
    reused = cached_copy(root, url)
    if reused is not None:
        # 缓存命中：不重复下载，但仍返回来源与许可（旁车里的原始记录）
        meta = {}
        try:
            side = Path(str(reused) + ".meta.json")
            meta = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {}
        except Exception:  # noqa: BLE001
            meta = {}
        logger.info("web 素材走缓存: %s（%s）", reused, meta.get("license", "unknown"))
        return IngestResult(
            ok=True, kind=str(meta.get("kind") or ""), local_path=str(reused), url=url,
            title=title or str(meta.get("title") or ""),
            license=str(meta.get("license") or license_of(url)),
            sha256=str(meta.get("sha256") or ""), bytes=int(meta.get("bytes") or 0),
            content_type=str(meta.get("content_type") or ""),
            meta_path=str(Path(str(reused) + ".meta.json")), cached=True,
        )
    factory = client_factory or httpx.AsyncClient
    try:
        async with factory(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=_DEFAULT_HEADERS)
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            body: bytes
            if hasattr(resp, "content") and isinstance(resp.content, (bytes, bytearray)):
                body = bytes(resp.content)
            else:
                body = str(getattr(resp, "text", "")).encode("utf-8")
    except Exception as exc:  # noqa: BLE001 — 抓取失败如实回报
        return IngestResult(ok=False, url=url, error=f"{type(exc).__name__}: {exc}"[:200])

    if not body:
        return IngestResult(ok=False, url=url, error="空响应体")
    if len(body) > max_bytes:
        return IngestResult(ok=False, url=url,
                            error=f"文件过大（{len(body)} > {max_bytes}）")

    digest = hashlib.sha256(body).hexdigest()
    kind_dir, ext = _guess_kind(content_type, url)
    base_dir = _materials_root(root)

    if kind_dir:
        target_dir = base_dir / kind_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        name = f"{_slug(title or Path(urlparse(url).path).stem)}_{digest[:10]}{ext}"
        path = target_dir / name
        if not path.exists():  # 同 sha256 已存在 → 去重，不重复写
            path.write_bytes(body)
        result_kind = kind_dir
    else:
        # 非媒体 → 当正文保存（便于引用/复核，也能被 read_file 读到）
        from clipwright.services.web_search import WebFetchService

        text = await WebFetchService().fetch(url, max_chars=20000)
        if not text.strip():
            return IngestResult(ok=False, url=url, error="正文为空（可能非 HTML）")
        target_dir = base_dir / "pages"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{_slug(title or Path(urlparse(url).path).stem)}_{digest[:10]}.md"
        if not path.exists():
            path.write_text(text, encoding="utf-8")
        result_kind = "pages"

    meta = {
        "url": url,
        "title": title,
        "license": license_of(url),
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sha256": digest,
        "bytes": len(body),
        "content_type": content_type or "text/html",
        "kind": result_kind,
    }
    meta_path = _write_sidecar(path, meta)
    logger.info("web 素材落盘: %s（%s, %d bytes, license=%s）",
                path, result_kind, len(body), meta["license"])
    return IngestResult(
        ok=True, kind=result_kind, local_path=str(path), url=url, title=title,
        license=meta["license"], sha256=digest, bytes=len(body),
        content_type=meta["content_type"], meta_path=meta_path,
    )


async def ingest_results(
    results: Iterable[dict[str, Any]],
    *,
    root: str | Path,
    limit: int = 3,
    media_only: bool = False,
    client_factory: Callable[..., Any] | None = None,
) -> list[IngestResult]:
    """把一批搜索结果落成本地素材（默认最多 3 个，失败不影响其余）。"""
    out: list[IngestResult] = []
    for item in list(results)[: max(0, int(limit))]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        res = await fetch_and_store(
            url, root=root, title=str(item.get("title") or ""),
            client_factory=client_factory,
        )
        if media_only and res.ok and res.kind == "pages":
            continue
        out.append(res)
    return out


__all__ = [
    "IngestResult", "KNOWN_LICENSES", "MAX_BYTES", "MAX_PER_HOST", "cached_copy",
    "clear_robots_cache", "fetch_and_store", "host_allowed", "ingest_results",
    "is_public_url", "license_of", "robots_allows",
]
