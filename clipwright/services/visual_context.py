"""视觉/Persona 上下文统一装配（PRD §16 R1）。

背景（2026-10-29 审计出的 P0 断链）：`persona_prompt` / `persona_config` /
`visual_config` / `vision_prompt` 四个字段在各 Agent 的 Input 里都有，但真实
pipeline 里恒为空——因为 `stage_merge.MERGE_KEYS` 不写这些键、`request.extra`
也不透传、`stage_inputs.assemble` 又只读黑板。

本模块把「四个字段从哪来」收口成一处，优先级固定：

    board（黑板，显式阶段产物）
      → extra（request.extra_params，前端/API 透传）
        → persona_store（按 persona_id 读 persona.yaml + prompt.md）
          → 空（并标注 source=fallback）

同时承担三件事：
1. 提示词截断统一（`settings.persona_prompt_max_chars`，默认 2000）——
   旧实现是 400/600/1500/2000 四处不同截断，同一个人格在不同路径拿到不同长度；
2. 生成缓存指纹（只有与"动画/视觉"相关的字段参与，避免无关 persona 字段冲掉缓存）；
3. 把 persona 的 rhythm（cut_profile / cut_density_tier）暴露出来，
   供"读了 extra_params.cut_profile 但没人写"的旧读取点使用。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipwright.config import logger

#: persona 提示词默认上限（字符）；settings 可覆盖
DEFAULT_PERSONA_PROMPT_MAX = 2000

#: 参与视觉指纹的 visual 子键（新增色板字段时同步）
_FINGERPRINT_VISUAL_KEYS = (
    "palette", "primary_color", "secondary_color", "accent_color",
    "text_color", "font", "font_size", "animation_style", "animation_styles",
)


def _max_chars() -> int:
    try:
        from clipwright.config import settings

        return max(200, int(getattr(settings, "persona_prompt_max_chars", DEFAULT_PERSONA_PROMPT_MAX)))
    except Exception:  # noqa: BLE001
        return DEFAULT_PERSONA_PROMPT_MAX


def _persona_dir() -> Path | None:
    try:
        from clipwright.config import settings

        return Path(settings.persona_dir)
    except Exception:  # noqa: BLE001
        return None


def _read_prompt_file(persona_id: str) -> str:
    base = _persona_dir()
    if base is None or not persona_id:
        return ""
    for name in ("prompt.md", "prompt.txt", "PROMPT.md"):
        p = base / persona_id / name
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                logger.warning("VisualContext: 读取 %s 失败", p, exc_info=True)
    return ""


def load_persona_store(persona_id: str) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """读 persona 仓库：返回 ``(prompt, parameter, visual, rhythm)``。

    任何失败都返回空（调用方会标注 source=fallback），绝不因为人格缺失阻断生成。
    """
    if not persona_id:
        return "", {}, {}, {}
    try:
        from clipwright.persona.loader import load_persona_by_id, resolve_inheritance

        manifest = resolve_inheritance(load_persona_by_id(persona_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("VisualContext: persona %s 加载失败: %s", persona_id, e)
        return "", {}, {}, {}

    parameter: dict[str, Any] = {}
    if getattr(manifest, "parameter", None) is not None:
        try:
            parameter = manifest.parameter.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            parameter = {}

    # prompt 优先读 prompt.md（全文），再退回 manifest.prompt
    prompt = _read_prompt_file(persona_id) or str(getattr(manifest, "prompt", "") or "")
    visual = parameter.get("visual") if isinstance(parameter.get("visual"), dict) else {}
    rhythm = parameter.get("rhythm") if isinstance(parameter.get("rhythm"), dict) else {}
    return prompt, parameter, dict(visual or {}), dict(rhythm or {})


def _first_nonempty(*values: Any) -> tuple[Any, int]:
    """返回 `(值, 命中索引)`；-1 表示全部为空（调用方可据此标注 source）。"""
    for i, v in enumerate(values):
        if v not in (None, "", {}, []):
            return v, i
    return (values[0] if values else None), -1


@dataclass(frozen=True)
class VisualContext:
    """一次装配结果（structure / animation 共用）。"""

    persona_id: str = ""
    persona_prompt: str = ""
    persona_config: dict[str, Any] = field(default_factory=dict)
    visual_config: dict[str, Any] = field(default_factory=dict)
    vision_prompt: str = ""
    rhythm: dict[str, Any] = field(default_factory=dict)
    #: 每个字段的来源：board / extra / persona_store / fallback
    source: str = "fallback"
    #: 提示词原始长度（截断前），用于 trace 与"是否被截"判定
    prompt_chars_raw: int = 0
    fingerprint: str = ""

    def to_trace(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "source": self.source,
            "prompt_chars": len(self.persona_prompt),
            "prompt_chars_raw": self.prompt_chars_raw,
            "truncated": self.prompt_chars_raw > len(self.persona_prompt),
            "palette": self.visual_config.get("palette"),
            "has_colors": any(
                self.visual_config.get(k) for k in ("primary_color", "secondary_color", "accent_color")
            ),
            "fingerprint": self.fingerprint,
        }

    def animation_style_hint(self, max_chars: int = 1200) -> str:
        """给插件/模板路径的人格式风格摘要（R6：``mg_params["style_hint"]``）。"""
        parts: list[str] = []
        v = self.visual_config
        if v.get("palette"):
            parts.append(f"配色：{v['palette']}")
        colors = [f"{k}={v[k]}" for k in ("primary_color", "secondary_color", "accent_color")
                  if v.get(k)]
        if colors:
            parts.append("色板：" + "、".join(colors))
        if v.get("font"):
            parts.append(f"字体：{v['font']}")
        styles = v.get("animation_styles")
        if isinstance(styles, dict) and styles:
            parts.append("动画风格：" + "；".join(
                str(x) for x in styles.values() if isinstance(x, str) and x.strip()
            ))
        rhythm = self.rhythm
        if rhythm.get("cut_profile"):
            parts.append(f"剪辑节奏：{rhythm['cut_profile']}")
        if rhythm.get("cut_density_tier"):
            parts.append(f"切点密度：{rhythm['cut_density_tier']}")
        if self.persona_prompt:
            parts.append("创作者风格原文：" + self.persona_prompt[:600])
        return "；".join(parts)[:max_chars]


def _fingerprint(persona_id: str, visual: dict[str, Any], prompt: str) -> str:
    payload = {
        "persona_id": persona_id,
        "visual": {k: visual.get(k) for k in _FINGERPRINT_VISUAL_KEYS if visual.get(k) is not None},
        "prompt": prompt[:800],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def load(
    persona_id: str,
    *,
    board: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> VisualContext:
    """装配视觉上下文（四个字段各自独立取值，逐级兜底）。"""
    board = dict(board or {})
    extra = dict(extra or {})
    pid = str(persona_id or extra.get("persona_id") or board.get("persona_id") or "")

    store_prompt, store_param, store_visual, store_rhythm = load_persona_store(pid)

    prompt, hit_p = _first_nonempty(
        board.get("persona_prompt"), extra.get("persona_prompt"), store_prompt,
    )
    visual, hit_v = _first_nonempty(
        board.get("visual_config"), extra.get("visual_config"), store_visual,
    )
    pconf, hit_c = _first_nonempty(
        board.get("persona_config"), extra.get("persona_config"), store_param,
    )
    vision, _hit_vision = _first_nonempty(
        board.get("vision_prompt"), extra.get("vision_prompt"),
        # vision_prompt 不从 persona 仓库编造：人格里没有这个字段就保持为空
    )

    # 命中索引 → 来源名（0=board, 1=extra, 2=persona_store）
    _SRC = {0: "board", 1: "extra", 2: "persona_store"}
    sources = [_SRC[i] for i in (hit_p, hit_v, hit_c) if i in _SRC]
    source = "+".join(dict.fromkeys(sources)) or "fallback"

    prompt_text = str(prompt or "")
    raw_len = len(prompt_text)
    limit = _max_chars()
    if raw_len > limit:
        logger.info("VisualContext: persona prompt %d 字 → 截断到 %d（settings.persona_prompt_max_chars）",
                    raw_len, limit)
        prompt_text = prompt_text[:limit]

    visual_dict = dict(visual or {})
    return VisualContext(
        persona_id=pid,
        persona_prompt=prompt_text,
        persona_config=dict(pconf or {}),
        visual_config=visual_dict,
        vision_prompt=str(vision or ""),
        rhythm=dict(store_rhythm or {}),
        source=source,
        prompt_chars_raw=raw_len,
        fingerprint=_fingerprint(pid, visual_dict, prompt_text),
    )


def apply_rhythm_defaults(extra_params: dict[str, Any], vc: VisualContext) -> dict[str, Any]:
    """把 persona rhythm 补进 ``extra_params`` 的空缺键（cut_profile 等读取点才有值）。"""
    out = dict(extra_params or {})
    if not out.get("cut_profile") and vc.rhythm.get("cut_profile"):
        out["cut_profile"] = vc.rhythm["cut_profile"]
    if not out.get("cut_density_tier") and vc.rhythm.get("cut_density_tier"):
        out["cut_density_tier"] = vc.rhythm["cut_density_tier"]
    if not out.get("base_shot_duration_ms") and vc.rhythm.get("base_shot_duration_ms"):
        out["base_shot_duration_ms"] = vc.rhythm["base_shot_duration_ms"]
    # P3-15: persona 的 visual.transition_weights 此前只有写没有读（M3 删除
    # pipeline_v2 后接线丢失，值只落在 yaml 里）。显式 extra 优先，不覆盖调用方。
    if not out.get("transition_weights"):
        _tw = (vc.persona_config.get("visual") or {}).get("transition_weights")
        if isinstance(_tw, dict) and _tw:
            out["transition_weights"] = dict(_tw)
    return out


__all__ = [
    "DEFAULT_PERSONA_PROMPT_MAX",
    "VisualContext",
    "apply_rhythm_defaults",
    "load",
    "load_persona_store",
]
