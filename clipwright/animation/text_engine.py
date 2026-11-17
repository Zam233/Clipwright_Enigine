"""文字动画 Engine 接口（engine 不实现任何 motion）。

`TextAnimationEngine` 只做：
- style 归一化/校验；
- 从 registry 解析 `TextMotionProvider`；
- 校验 provider 返回的 MGDef；
- 生成 HTML/预览计划；
- 所有文字 clip 的统一 `apply_style`。

fade/slide/scale/typewriter/... 的实现由 `text-motion-core` 插件提供。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from clipwright.animation.text_style import normalize_motion, normalize_style


class TextMotionError(RuntimeError):
    code = "TEXT_MOTION_ERROR"


class TextMotionUnavailable(TextMotionError):
    code = "TEXT_MOTION_UNAVAILABLE"


class TextMotionPlanInvalid(TextMotionError):
    code = "TEXT_MOTION_PLAN_INVALID"


@dataclass
class TextMotionRequest:
    text: str
    style: dict[str, Any]
    motion: dict[str, Any]
    width: int
    height: int
    duration_sec: float
    base_mg_def: dict[str, Any] = field(default_factory=dict)
    primitives: Any = None


@dataclass
class MotionPlan:
    mg_def: dict[str, Any]
    provider_id: str
    provider_version: str = ""
    fidelity: str = "exact"
    preview_ops: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TextPlan:
    provider_id: str
    mg_def: dict[str, Any]
    width: int
    height: int
    duration_sec: float
    fidelity: str
    preview_ops: list[dict[str, Any]] = field(default_factory=list)
    style: dict[str, Any] = field(default_factory=dict)
    motion: dict[str, Any] = field(default_factory=dict)


class TextMotionProvider(Protocol):
    id: str
    version: str

    def supports(self, *, text: str, style: dict, motion: dict) -> bool: ...
    def plan(self, request: TextMotionRequest) -> MotionPlan: ...


class TextMotionRegistry:
    _providers: dict[str, tuple[str, TextMotionProvider]] = {}

    @classmethod
    def register(cls, provider: TextMotionProvider, *, plugin_id: str = "") -> str | None:
        motion_id = str(getattr(provider, "id", "") or "")
        if not motion_id:
            return "text_motion provider 缺少 id"
        if motion_id in cls._providers:
            owner, _ = cls._providers[motion_id]
            if owner != plugin_id and owner != "__bundled__":
                return f"text_motion:{motion_id} 已被 {owner} 注册"
        cls._providers[motion_id] = (plugin_id, provider)
        return None

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> list[str]:
        removed = [mid for mid, (owner, _) in cls._providers.items() if owner == plugin_id]
        for mid in removed:
            cls._providers.pop(mid, None)
        return removed

    @classmethod
    def has(cls, motion_type: str) -> bool:
        return str(motion_type or "") in cls._providers

    @classmethod
    def resolve(cls, motion_type: str) -> TextMotionProvider:
        entry = cls._providers.get(str(motion_type or ""))
        if entry is None:
            raise TextMotionUnavailable(f"没有 text_motion provider: {motion_type!r}")
        return entry[1]

    @classmethod
    def list(cls) -> list[dict[str, str]]:
        return [
            {"id": mid, "plugin_id": owner,
             "version": str(getattr(provider, "version", "") or "")}
            for mid, (owner, provider) in sorted(cls._providers.items())
        ]


class TextAnimationEngine:
    @staticmethod
    def resolve_style_pack(pack_id: str) -> dict[str, Any]:
        from clipwright.animation.text_plugin_registry import TextStylePackRegistry
        pack = TextStylePackRegistry.get(pack_id)
        if not pack:
            raise TextMotionUnavailable(f"没有 text_style_pack: {pack_id!r}")
        return dict(pack.get("style") or {})

    @staticmethod
    def resolve_asset(pack_id: str, asset_id: str) -> dict[str, Any]:
        from clipwright.animation.text_plugin_registry import TextAssetPackRegistry
        pack = TextAssetPackRegistry.get(pack_id)
        if not pack:
            raise TextMotionUnavailable(f"没有 text_asset_pack: {pack_id!r}")
        for asset in pack.get("assets") or []:
            if str(asset.get("id") or "") == str(asset_id or ""):
                return dict(asset)
        raise TextMotionUnavailable(f"asset_pack {pack_id!r} 没有 asset: {asset_id!r}")

    def capabilities(self) -> dict[str, Any]:
        try:
            from clipwright.animation.text_plugin_registry import (
                TextAssetPackRegistry, TextPromptPackRegistry, TextStylePackRegistry,
            )
            packs = {
                "prompts": [x["id"] for x in TextPromptPackRegistry.list()],
                "styles": [x["id"] for x in TextStylePackRegistry.list()],
                "assets": [x["id"] for x in TextAssetPackRegistry.list()],
            }
        except Exception:
            packs = {"prompts": [], "styles": [], "assets": []}
        return {"motions": TextMotionRegistry.list(),
                "style_schema": 1,
                "renderer": "mg_hyperframes", "packs": packs}

    def plan(self, *, text: str, style: dict, motion: dict,
             width: int, height: int, display_sec: float,
             base_mg_def: dict | None = None,
             provider_id: str | None = None,
             style_pack_id: str | None = None) -> TextPlan:
        if style_pack_id:
            style = {**self.resolve_style_pack(style_pack_id), **dict(style or {})}
        normalized_style = normalize_style(style)
        motion_type = provider_id or str(motion.get("type") or "fade_in")
        provider = TextMotionRegistry.resolve(motion_type)
        normalized_motion = normalize_motion({**motion, "type": motion_type}, text=text)
        _base = dict(base_mg_def or {})
        _base.setdefault("animation_id", f"text_{motion_type}")
        _base.setdefault("duration_sec", float(display_sec))
        _base.setdefault("width", int(width))
        _base.setdefault("height", int(height))
        request = TextMotionRequest(
            text=str(text), style=normalized_style, motion=normalized_motion,
            width=int(width), height=int(height), duration_sec=float(display_sec),
            base_mg_def=_base, primitives=_primitives(),
        )
        plan = provider.plan(request)
        if isinstance(plan, MotionPlan) and isinstance(plan.mg_def, dict):
            # provider 可能带着 base mgdef 的旧 keyframe 时间；engine 统一夹到
            # 当前 display_sec，避免 scale/typewriter 的浮点尾差被判非法。
            _dur = max(0.1, float(display_sec))
            for _e in plan.mg_def.get("elements") or []:
                if not isinstance(_e, dict):
                    continue
                for _kf in _e.get("keyframes") or []:
                    if isinstance(_kf, dict):
                        try:
                            _kf["time"] = round(max(0.0, min(_dur, float(_kf.get("time") or 0))), 3)
                        except (TypeError, ValueError):
                            pass
        if not isinstance(plan, MotionPlan):
            raise TextMotionPlanInvalid(f"{motion_type} provider 返回非法 plan")
        from clipwright.animation.mg.validator import validate_mg_json
        ok, errors = validate_mg_json(plan.mg_def)
        if not ok:
            raise TextMotionPlanInvalid(
                f"{motion_type} 返回非法 MGDef: {'; '.join(errors[:5])}")
        return TextPlan(
            provider_id=plan.provider_id or motion_type,
            mg_def=plan.mg_def, width=int(width), height=int(height),
            duration_sec=float(display_sec), fidelity=plan.fidelity,
            preview_ops=list(plan.preview_ops or []),
            style=normalized_style, motion=normalized_motion,
        )

    def render_html(self, plan: TextPlan, *, fps: float = 30.0) -> str:
        from clipwright.animation.mg_renderer import MGRenderer
        return MGRenderer.render(plan.mg_def, params={}, width=plan.width,
                                 height=plan.height, fps=float(fps))

    def apply_style(self, clip: Any, *, style: dict, motion: dict,
                    width: int, height: int) -> Any:
        """所有文字 clip 的唯一写入口；无 provider 直接失败。"""
        meta = dict(getattr(clip, "metadata", None) or {})
        base = meta.get("mg_def") if isinstance(meta.get("mg_def"), dict) else {}
        plan = self.plan(
            text=str(clip.text or ""), style=style, motion=motion,
            width=int(width), height=int(height),
            display_sec=float(clip.duration_sec or 3), base_mg_def=base,
            style_pack_id=str(meta.get("style_pack_id") or "") or None,
        )
        html = self.render_html(plan)
        meta.update({
            "style": plan.style, "motion": plan.motion, "mg_def": plan.mg_def,
            "mg_html": html, "mg_params": {}, "renderer": "mg_hyperframes",
            "text_provider_id": plan.provider_id, "fidelity": plan.fidelity,
            "preview_ops": list(plan.preview_ops or []),
            "apply_text_style": True,
        })
        clip.metadata = meta
        return clip


_ENGINE = TextAnimationEngine()


def get_engine() -> TextAnimationEngine:
    return _ENGINE


def _primitives():
    from clipwright.animation import text_primitives
    return text_primitives
