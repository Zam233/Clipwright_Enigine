"""MotionSpec 精确帧：MGRenderer HTML → Hyperframes snapshot PNG。"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from clipwright.animation.mg_renderer import MGRenderer
from clipwright.animation.hyperframes_renderer import HyperframesRenderer
from clipwright.config import logger


class MgFrameRenderer:
    VERSION = "1"
    _cache: OrderedDict[str, bytes] = OrderedDict()
    _max = 64

    @classmethod
    def cache_key(cls, mg_def: dict[str, Any], params: dict[str, Any], frame: int, dpr: int) -> str:
        raw = json.dumps({"mg": mg_def, "p": params, "f": frame, "d": dpr},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    async def render(
        cls, *, mg_def: dict[str, Any], params: dict[str, Any],
        width: int, height: int, fps: float, frame: int, dpr: int = 1,
    ) -> tuple[bytes | None, bool]:
        key = cls.cache_key(mg_def, params, frame, dpr)
        if key in cls._cache:
            cls._cache.move_to_end(key)
            return cls._cache[key], True
        html = MGRenderer.render(mg_def, params=params, width=width, height=height, fps=fps)
        if not html:
            return None, False
        with tempfile.TemporaryDirectory(prefix="mg_frame_") as td:
            png = await HyperframesRenderer.render_mg_html_to_png(
                html, td, time_sec=max(0.0, frame / max(1.0, fps)), timeout=20.0,
            )
        if not png:
            return None, False
        cls._cache[key] = png
        cls._cache.move_to_end(key)
        while len(cls._cache) > cls._max:
            cls._cache.popitem(last=False)
        return png, False

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()
