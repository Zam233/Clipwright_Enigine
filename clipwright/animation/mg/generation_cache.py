# -*- coding: utf-8 -*-
"""M3: MG 生成结果缓存——同输入跳过 LLM 调用。

自愈循环 / run_from_agent / retry 重跑 AnimationAgent 时，同 marker 的
MG clip 会全量重付 LLM 成本。此缓存以 sha256(description+text+style+params)
为键缓存 mg_def+html，命中时零 LLM 调用。文件持久化到 _cache/mg_gen/。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "_cache" / "mg_gen"
_CACHE_MAX = 200


def _cache_key(description: str, text_content: str, extra: str = "") -> str:
    raw = f"{description}|{text_content}|{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def cache_get(key: str) -> dict[str, Any] | None:
    """读取缓存的生成结果；未命中或文件损坏返回 None。"""
    p = _CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "mg_def" in data and "html" in data:
            return data
    except Exception:
        pass
    return None


def cache_put(key: str, mg_def: dict[str, Any], html: str) -> None:
    """写入缓存（LRU 淘汰最旧）。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / f"{key}.json"
        p.write_text(json.dumps({"mg_def": mg_def, "html": html}, ensure_ascii=False),
                     encoding="utf-8")
        # LRU: 超限时淘汰最旧
        files = sorted(_CACHE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
        while len(files) > _CACHE_MAX:
            files.pop(0).unlink(missing_ok=True)
    except Exception:
        pass


def cache_clear() -> None:
    """清空全部缓存文件（测试用）。"""
    import shutil
    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR, ignore_errors=True)
