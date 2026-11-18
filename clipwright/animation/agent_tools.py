"""Engine-owned animation Tool registry for the agentic loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class AnimationToolSpec:
    name: str
    description: str
    permission: str = "read"  # read|write|costly
    handler: Callable[..., Awaitable[Any]] | None = field(default=None, compare=False)
    kind: str = "builtin"


_TOOLS: dict[str, AnimationToolSpec] = {}


def register_animation_tool(spec: AnimationToolSpec) -> None:
    if spec.name in _TOOLS and _TOOLS[spec.name].kind == "builtin":
        raise ValueError(f"animation tool {spec.name} 已存在（内置不可覆盖）")
    _TOOLS[spec.name] = spec


def bind_animation_tool(name: str, handler: Callable[..., Awaitable[Any]]) -> None:
    spec = _TOOLS.get(name)
    if spec is None:
        raise KeyError(name)
    _TOOLS[name] = AnimationToolSpec(spec.name, spec.description, spec.permission, handler, spec.kind)


def get_animation_tool(name: str) -> AnimationToolSpec | None:
    return _TOOLS.get(name)


def list_animation_tools() -> list[AnimationToolSpec]:
    return sorted(_TOOLS.values(), key=lambda s: s.name)


async def call_animation_tool(name: str, **kwargs: Any) -> Any:
    spec = get_animation_tool(name)
    if spec is None:
        raise KeyError(f"unknown animation tool: {name}")
    if spec.handler is None:
        raise RuntimeError(f"animation tool {name} 没有实现")
    result = spec.handler(**kwargs)
    if hasattr(result, "__await__"):
        return await result
    return result


BUILTIN_TOOLS = (
    AnimationToolSpec("build_animation_brief", "生成/更新 AnimationBrief", "read"),
    AnimationToolSpec("research_sources", "查资料/知识库/联网来源", "costly"),
    AnimationToolSpec("build_shot_plan", "生成镜头意图/承接/seams", "read"),
    AnimationToolSpec("build_draft_spec", "生成元素角色/层级/相对布局草稿", "read"),
    AnimationToolSpec("generate_mg", "按草稿生成 mg_def", "costly"),
    AnimationToolSpec("validate_layout", "只校验 bbox/层级/对齐，不修改", "read"),
    AnimationToolSpec("repair_mg", "按具体 issues 让 LLM 修复", "costly"),
    AnimationToolSpec("qa_mg", "text/phone/motion/determinism QA", "read"),
    AnimationToolSpec("render_preview", "渲染单镜预览", "costly"),
    AnimationToolSpec("inspect_reference", "参考片 contact sheet/剪点", "costly"),
    AnimationToolSpec("ask_user", "需要用户确认时提问", "read"),
)
for _spec in BUILTIN_TOOLS:
    register_animation_tool(_spec)
