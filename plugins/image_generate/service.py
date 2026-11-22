"""image_generate 插件的生成服务：解析 provider → 校验能力 → 调用 → 落盘 → 记账。

职责边界：
- **本插件**：选源/选模型、能力协商、产物路径/命名、sha256 去重、幂等账本、
  工作区素材登记，以及 `image_generate` / `image_generate_batch` 工具；
- **Engine**：只提供 `ImageProvider` 协议、源配置/注册、工作区/素材/时间线等通用服务。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
from clipwright.providers.base import ImageGenerationRequest, ImageProviderCapabilities
from clipwright.services import provider_sources as ps

#: 幂等账本（工作区内，随项目迁移/备份）
LEDGER_NAME = "generated_images.json"
#: 账本最多保留条数（旧条目只丢引用，不删文件）
MAX_LEDGER_ENTRIES = 500
#: 通用尺寸校验下限/上限（provider 声明 sizes="any" 时）
_MIN_SIDE, _MAX_SIDE = 64, 4096

_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class ImageGenerationError(RuntimeError):
    """可读的生成失败（配置缺失/参数非法/厂商错误）。"""


@dataclass
class ImageGenerationOutcome:
    ok: bool
    rel_path: str = ""
    abs_path: str = ""
    sha256: str = ""
    mime: str = "image/png"
    width: int = 0
    height: int = 0
    provider: str = ""
    source_id: str = ""
    model: str = ""
    reused: bool = False
    cost_usd: float | None = None
    error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "rel_path": self.rel_path, "sha256": self.sha256,
            "mime": self.mime, "width": self.width, "height": self.height,
            "provider": self.provider, "source_id": self.source_id,
            "model": self.model, "reused": self.reused,
            "cost_usd": self.cost_usd, "error": self.error,
            **({"detail": self.detail} if self.detail else {}),
        }


def _ext_for(mime: str) -> str:
    return _MIME_EXT.get(str(mime or "").split(";")[0].strip().lower(), ".png")


def _slug(prompt: str, limit: int = 24) -> str:
    """把 prompt 收敛成可检索的文件名前缀（保留中英文/数字，空白转下划线）。"""
    import re

    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(prompt or "")).strip("_")
    return (text[:limit] or "image")


def _idem_key(*, source_id: str, provider: str, model: str, prompt: str,
              size: str, n: int, seed: int | None) -> str:
    raw = json.dumps(
        [source_id, provider, model, prompt, size, n, seed],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = str(size).lower().split("x", 1)
        return int(w), int(h)
    except Exception as exc:  # noqa: BLE001
        raise ImageGenerationError(
            f"尺寸格式非法: {size!r}（应为 WxH，如 1024x1024）") from exc


def validate_params(caps: ImageProviderCapabilities, *, prompt: str, size: str,
                    n: int) -> tuple[str, int]:
    """按 provider 声明做能力协商；返回 (规范化 size, n)。非法即报错，不静默改。"""
    prompt = str(prompt or "")
    limit = int(getattr(caps, "max_prompt_chars", 0) or 0)
    if limit and len(prompt) > limit:
        raise ImageGenerationError(f"prompt 过长（{len(prompt)} > {limit} 字符）")

    allowed = getattr(caps, "sizes", "any")
    resolved_size = str(size or "").strip().lower()
    if isinstance(allowed, (tuple, list)) and allowed:
        allowed_list = [str(s).lower() for s in allowed]
        if not resolved_size:
            resolved_size = allowed_list[0]
        if resolved_size not in allowed_list:
            raise ImageGenerationError(
                f"尺寸 {resolved_size!r} 不受支持；可用: {', '.join(allowed_list)}")
    else:
        if not resolved_size:
            resolved_size = "1024x1024"
        w, h = _parse_size(resolved_size)
        if not (_MIN_SIDE <= w <= _MAX_SIDE and _MIN_SIDE <= h <= _MAX_SIDE):
            raise ImageGenerationError(
                f"尺寸超出范围: {w}x{h}（每边需在 {_MIN_SIDE}..{_MAX_SIDE} 像素）")
        if w % 2 or h % 2:
            raise ImageGenerationError(f"尺寸需为偶数: {w}x{h}")

    max_n = max(1, int(getattr(caps, "max_n", 1) or 1))
    count = int(n or 1)
    if count < 1 or count > max_n:
        raise ImageGenerationError(f"n 超出范围: {count}（该 provider 支持 1..{max_n}）")
    return resolved_size, count


def _ledger_path(root: Path) -> Path:
    return root / "cache" / LEDGER_NAME


def _read_ledger(root: Path) -> list[dict[str, Any]]:
    path = _ledger_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data) if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — 坏账本按空（不阻断生成）
        logger.warning("图像账本解析失败，按空处理: %s", path)
        return []


def _write_ledger(root: Path, entries: list[dict[str, Any]]) -> None:
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries[-MAX_LEDGER_ENTRIES:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def resolve_image_source(source_id: str = "", model: str = "") -> tuple[dict[str, Any], str]:
    """解析出 (源, 模型)：显式 source_id > 角色 `image` 的绑定。"""
    sid = str(source_id or "").strip()
    chosen_model = str(model or "").strip()
    if not sid:
        binding = dict((ps.get_roles() or {}).get("image") or {})
        sid = str(binding.get("source_id") or "").strip()
        if not chosen_model:
            chosen_model = str(binding.get("model") or "").strip()
    if not sid:
        raise ImageGenerationError(
            "没有可用的图像源：请到「供应商 → AI 生图」创建源并绑定「图像模型」角色")
    src = ps.get_source(sid, public=False)
    if src is None:
        raise ImageGenerationError(f"图像源不存在: {sid}")
    if str(src.get("kind") or "") != "image":
        raise ImageGenerationError(
            f"源 {sid} 的类型是 {src.get('kind')!r}，不是 image（请在 AI 生图 tab 选择）")
    if not chosen_model:
        models = [m for m in (src.get("models") or []) if m.get("enabled", True)]
        chosen_model = str(models[0].get("id") if models else "")
    return src, chosen_model


class ImageGenerationService:
    """工作区作用域的图像生成服务。"""

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()

    def _resolve_provider(self, provider_id: str):
        item = ContributionRegistry.resolve(ContributionKind.IMAGE_PROVIDER, provider_id)
        if item is None:
            raise ImageGenerationError(
                f"未安装图像 provider: {provider_id}（请安装 image_generate 插件）")
        return item.object

    def _cached(self, key: str) -> dict[str, Any] | None:
        for entry in reversed(_read_ledger(self.root)):
            if entry.get("key") != key:
                continue
            rel = str(entry.get("rel_path") or "")
            if rel and (self.root / rel).is_file():
                return entry
        return None

    def _save(self, data: bytes, mime: str, prompt: str) -> tuple[str, str]:
        sha = hashlib.sha256(data).hexdigest()
        rel = f"materials/image/{_slug(prompt)}_{sha[:12]}{_ext_for(mime)}"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():  # 内容去重：同 sha 不重写
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(target)
        return rel, sha

    def plan(
        self,
        *,
        prompt: str,
        size: str = "",
        n: int = 1,
        model: str = "",
        source_id: str = "",
    ) -> dict[str, Any]:
        """dry-run：只解析源/校验能力，不调用 provider、不产生文件与费用。"""
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ImageGenerationError("prompt 不能为空")
        src, chosen_model = resolve_image_source(source_id, model)
        provider_id = str(src.get("provider") or "").strip()
        impl = self._resolve_provider(provider_id)
        caps = impl.capabilities(chosen_model)
        resolved_size, count = validate_params(caps, prompt=prompt, size=size, n=n)
        return {
            "source_id": str(src.get("id") or ""),
            "source_name": str(src.get("name") or ""),
            "provider": provider_id,
            "model": chosen_model,
            "size": resolved_size,
            "n": count,
            "cost_per_image_usd": getattr(caps, "cost_per_image_usd", None),
        }

    async def generate(
        self,
        *,
        prompt: str,
        size: str = "",
        n: int = 1,
        model: str = "",
        source_id: str = "",
        seed: int | None = None,
    ) -> ImageGenerationOutcome:
        prompt = str(prompt or "").strip()
        if not prompt:
            return ImageGenerationOutcome(ok=False, error="prompt 不能为空")

        try:
            src, chosen_model = resolve_image_source(source_id, model)
            provider_id = str(src.get("provider") or "").strip()
            impl = self._resolve_provider(provider_id)
            caps = impl.capabilities(chosen_model)
            resolved_size, count = validate_params(
                caps, prompt=prompt, size=size, n=n)
        except ImageGenerationError as exc:
            return ImageGenerationOutcome(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — provider 声明异常也按可读错误
            return ImageGenerationOutcome(
                ok=False, error=f"解析 provider 失败: {type(exc).__name__}: {exc}")

        key = _idem_key(source_id=str(src.get("id") or ""), provider=provider_id,
                        model=chosen_model, prompt=prompt, size=resolved_size,
                        n=count, seed=seed)
        cached = self._cached(key)
        if cached is not None:
            return ImageGenerationOutcome(
                ok=True, rel_path=str(cached["rel_path"]),
                abs_path=str(self.root / str(cached["rel_path"])),
                sha256=str(cached.get("sha256") or ""),
                mime=str(cached.get("mime") or "image/png"),
                width=int(cached.get("width") or 0), height=int(cached.get("height") or 0),
                provider=provider_id, source_id=str(src.get("id") or ""),
                model=chosen_model, reused=True,
                cost_usd=cached.get("cost_usd"),
            )

        resolved = ps.resolve_for_source(str(src.get("id") or "")) or {}
        request = ImageGenerationRequest(
            prompt=prompt, model=chosen_model, size=resolved_size, n=count,
            seed=seed, extra={"source_config": resolved},
        )
        try:
            images = await impl.generate(request)
        except Exception as exc:  # noqa: BLE001 — 厂商错误转可读结果，不抛出给 LLM
            return ImageGenerationOutcome(
                ok=False, provider=provider_id, source_id=str(src.get("id") or ""),
                model=chosen_model, error=str(exc) or type(exc).__name__)
        return self._finish(
            src=src, provider_id=provider_id, chosen_model=chosen_model,
            prompt=prompt, resolved_size=resolved_size, count=count,
            seed=seed, caps=caps, key=key, images=images,
        )

    async def generate_many(
        self, *, prompts: list[str], size: str = "", n: int = 1,
        model: str = "", source_id: str = "", seed: int | None = None,
        concurrency: int = 2,
    ) -> list[ImageGenerationOutcome]:
        """批量生成：provider 支持时一次原生 batch，否则逐项 generate 回退。"""
        clean = [str(p or "").strip() for p in (prompts or [])]
        if not clean:
            return []
        try:
            src, chosen_model = resolve_image_source(source_id, model)
            provider_id = str(src.get("provider") or "").strip()
            impl = self._resolve_provider(provider_id)
            caps = impl.capabilities(chosen_model)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) or type(exc).__name__
            return [ImageGenerationOutcome(ok=False, error=msg) for _ in clean]

        resolved = ps.resolve_for_source(str(src.get("id") or "")) or {}
        outcomes: list[ImageGenerationOutcome | None] = [None] * len(clean)
        pending: list[tuple[int, str, str, int, str]] = []
        for i, prompt in enumerate(clean):
            if not prompt:
                outcomes[i] = ImageGenerationOutcome(ok=False, error="prompt 不能为空")
                continue
            try:
                resolved_size, count = validate_params(caps, prompt=prompt, size=size, n=n)
            except ImageGenerationError as exc:
                outcomes[i] = ImageGenerationOutcome(ok=False, error=str(exc))
                continue
            key = _idem_key(source_id=str(src.get("id") or ""), provider=provider_id,
                            model=chosen_model, prompt=prompt, size=resolved_size,
                            n=count, seed=seed)
            cached = self._cached(key)
            if cached is not None:
                outcomes[i] = ImageGenerationOutcome(
                    ok=True, rel_path=str(cached["rel_path"]),
                    abs_path=str(self.root / str(cached["rel_path"])),
                    sha256=str(cached.get("sha256") or ""),
                    mime=str(cached.get("mime") or "image/png"),
                    width=int(cached.get("width") or 0),
                    height=int(cached.get("height") or 0),
                    provider=provider_id, source_id=str(src.get("id") or ""),
                    model=chosen_model, reused=True, cost_usd=cached.get("cost_usd"),
                )
            else:
                pending.append((i, prompt, resolved_size, count, key))

        if pending and callable(getattr(impl, "generate_batch", None)) and len(pending) > 1:
            requests = [ImageGenerationRequest(
                prompt=p, model=chosen_model, size=resolved_size, n=count,
                seed=seed, extra={"source_config": resolved},
            ) for _, p, resolved_size, count, _ in pending]
            try:
                batches = await impl.generate_batch(requests)
            except Exception:  # noqa: BLE001 — 原生 batch 失败，逐项回退
                batches = []
            if len(batches) == len(pending):
                for (i, prompt, resolved_size, count, key), images in zip(pending, batches):
                    try:
                        outcomes[i] = self._finish(
                            src=src, provider_id=provider_id, chosen_model=chosen_model,
                            prompt=prompt, resolved_size=resolved_size, count=count,
                            seed=seed, caps=caps, key=key, images=list(images or []),
                        )
                    except Exception as exc:  # noqa: BLE001
                        outcomes[i] = ImageGenerationOutcome(
                            ok=False, provider=provider_id,
                            source_id=str(src.get("id") or ""), model=chosen_model,
                            error=f"{type(exc).__name__}: {exc}")
                pending = []

        if pending:
            sem = asyncio.Semaphore(max(1, min(4, int(concurrency or 2))))

            async def _one(item):
                i, prompt, _size, _count, _key = item
                async with sem:
                    outcomes[i] = await self.generate(
                        prompt=prompt, size=size, n=n, model=model,
                        source_id=source_id, seed=seed,
                    )
            await asyncio.gather(*[_one(item) for item in pending])

        return [o or ImageGenerationOutcome(ok=False, error="未生成") for o in outcomes]

    def _finish(
        self, *, src: dict[str, Any], provider_id: str, chosen_model: str,
        prompt: str, resolved_size: str, count: int, seed: int | None,
        caps: Any, key: str, images: list[Any],
    ) -> ImageGenerationOutcome:
        if not images:
            return ImageGenerationOutcome(
                ok=False, provider=provider_id, source_id=str(src.get("id") or ""),
                model=chosen_model, error="provider 没有返回任何图像")

        first = images[0]
        data = first.data
        if data is None and first.url:
            return ImageGenerationOutcome(
                ok=False, provider=provider_id, source_id=str(src.get("id") or ""),
                model=chosen_model,
                error="provider 只返回了 URL 且未下载为字节；请让插件下载后返回 data")
        if not isinstance(data, (bytes, bytearray)) or not data:
            return ImageGenerationOutcome(
                ok=False, provider=provider_id, source_id=str(src.get("id") or ""),
                model=chosen_model, error="provider 返回的图像数据为空")

        mime = str(first.mime or "image/png")
        if not mime.startswith("image/") or "svg" in mime:
            return ImageGenerationOutcome(
                ok=False, error=f"拒绝非位图产物: {mime}")
        rel_path, sha = self._save(bytes(data), mime, prompt)
        w, h = (0, 0)
        if "x" in resolved_size:
            try:
                w, h = _parse_size(resolved_size)
            except ImageGenerationError:
                w, h = (0, 0)

        entry = {
            "key": key, "rel_path": rel_path, "sha256": sha, "mime": mime,
            "width": w, "height": h, "prompt": prompt, "provider": provider_id,
            "source_id": str(src.get("id") or ""), "model": chosen_model,
            "size": resolved_size, "n": count, "seed": seed,
            "cost_usd": getattr(caps, "cost_per_image_usd", None),
            "at": time.time(),
        }
        ledger = _read_ledger(self.root)
        ledger.append(entry)
        try:
            _write_ledger(self.root, ledger)
        except Exception as exc:  # noqa: BLE001 — 账本写失败不回滚产物，但明确告警
            logger.warning("图像账本写入失败: %s", exc)

        return ImageGenerationOutcome(
            ok=True, rel_path=rel_path, abs_path=str(self.root / rel_path),
            sha256=sha, mime=mime, width=w, height=h, provider=provider_id,
            source_id=str(src.get("id") or ""), model=chosen_model,
            reused=False, cost_usd=entry["cost_usd"],
        )
