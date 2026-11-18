"""文字动画类型注册表（PRD `docs/text-animation-llm-prd-2026-10-30.md` M1）。

与 38 个逻辑动画类型的关系：**文字动画不再查 `AnimationCatalog` 的关键帧表**，
而是像 mg 一样「prompt 定义类型语义 + LLM 生成结构」。这里只声明类型的**语义契约**
（意图/动效语言/硬约束/反例），正文提示词在 `animation/prompts/text/<id>.md`。

`AnimationCatalog` 仍保留为 fallback（LLM 不可用/生成失败时用它，并登记降级）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts" / "engine" / "templates" / "text"

#: 提示词必须包含的段落（与 38 个 MG 类型同构 + 风格参考）
PROMPT_SECTIONS = (
    "Intent", "Semantic constraints", "Composition", "Motion",
    "Anti-patterns", "Example", "Style reference",
)


@dataclass(frozen=True)
class TextAnimSpec:
    id: str
    name: str
    intent: str
    motion: str
    constraints: tuple[str, ...] = ()
    anti_patterns: tuple[str, ...] = ()
    #: 该类型的默认锚点（字幕在 bottom，文字动画默认避开）
    default_anchor: str = "center"


#: 内置类型的数据源在插件目录（物理迁出 engine）。
_BUILTIN_PACK = Path(__file__).resolve().parents[2] / "plugins" / "text_packs" / "builtin_types.json"


def _load_builtin_specs() -> tuple[TextAnimSpec, ...]:
    try:
        import json
        data = json.loads(_BUILTIN_PACK.read_text(encoding="utf-8"))
        return tuple(
            TextAnimSpec(
                id=str(item["id"]), name=str(item.get("name") or item["id"]),
                intent=str(item.get("intent") or ""), motion=str(item.get("motion") or ""),
                constraints=tuple(item.get("constraints") or ()),
                anti_patterns=tuple(item.get("anti_patterns") or ()),
                default_anchor=str(item.get("default_anchor") or "center"),
            )
            for item in data.get("types") or []
        )
    except Exception:
        return ()


TEXT_ANIM_SPECS: tuple[TextAnimSpec, ...] = _load_builtin_specs()

_BY_ID = {spec.id: spec for spec in TEXT_ANIM_SPECS}


def get_text_spec(type_id: str) -> TextAnimSpec | None:
    type_id = str(type_id or "")
    try:
        from clipwright.animation.text_plugin_registry import TextPromptPackRegistry
        packed = TextPromptPackRegistry.get(type_id)
        if packed:
            return TextAnimSpec(
                id=type_id,
                name=str(packed.get("name") or type_id),
                intent=str(packed.get("intent") or ""),
                motion=str(packed.get("motion") or ""),
                constraints=tuple(packed.get("constraints") or ()),
                anti_patterns=tuple(packed.get("anti_patterns") or ()),
                default_anchor=str(packed.get("default_anchor") or "center"),
            )
    except Exception:
        pass
    return _BY_ID.get(type_id)


def known_text_ids() -> tuple[str, ...]:
    return tuple(_BY_ID)


def _read_prompt(type_id: str) -> str:
    path = PROMPT_DIR / f"{type_id}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_text_prompt(type_id: str) -> dict[str, str]:
    try:
        from clipwright.animation.text_plugin_registry import TextPromptPackRegistry
        packed = TextPromptPackRegistry.get(type_id)
        if packed and str(packed.get("prompt") or "").strip():
            text = str(packed["prompt"])
            sections: dict[str, list[str]] = {}
            current = ""
            for raw in text.splitlines():
                line = raw.rstrip()
                if line.startswith("## "):
                    current = line[3:].strip()
                    sections.setdefault(current, [])
                elif current:
                    sections[current].append(line)
            return {name: "\n".join(lines).strip() for name, lines in sections.items()}
    except Exception:
        pass
    """按 `## 段名` 切分提示词正文（与 `type_specs.load_type_prompt` 同构）。"""
    body = _read_prompt(type_id)
    if not body.strip():
        return {}
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def text_prompt_version(type_id: str) -> str:
    body = _read_prompt(type_id)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12] if body else ""


def missing_text_prompts() -> list[str]:
    return [spec.id for spec in TEXT_ANIM_SPECS if not _read_prompt(spec.id).strip()]


def validate_text_registry() -> list[str]:
    """自检：提示词存在、六段齐全、必填语义字段非空。"""
    problems: list[str] = []
    for spec in TEXT_ANIM_SPECS:
        body = _read_prompt(spec.id)
        if not body.strip():
            problems.append(f"{spec.id}: 缺提示词文件")
            continue
        sections = load_text_prompt(spec.id)
        missing = [name for name in PROMPT_SECTIONS if not sections.get(name)]
        if missing:
            problems.append(f"{spec.id}: 缺段落 {missing}")
        if not spec.intent.strip():
            problems.append(f"{spec.id}: intent 为空")
    return problems


__all__ = [
    "PROMPT_DIR", "PROMPT_SECTIONS", "TEXT_ANIM_SPECS", "TextAnimSpec",
    "get_text_spec", "known_text_ids", "load_text_prompt", "missing_text_prompts",
    "text_prompt_version", "validate_text_registry",
]
