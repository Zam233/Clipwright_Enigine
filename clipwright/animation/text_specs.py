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

PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / "text"

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


TEXT_ANIM_SPECS: tuple[TextAnimSpec, ...] = (
    TextAnimSpec(
        id="emphasis_keyword",
        name="关键词强调",
        intent="把旁白里最关键的那一个词/短语砸在画面上，其余信息让位",
        motion="短促入场（0.25–0.4s back-out），一次轻微弹跳后停住；不做整句滚动",
        constraints=(
            "只允许 1 个主元素（可选 1 个装饰线/底色块）",
            "文本必须是旁白的**摘要**，≤10 字，禁止与字幕逐字相同",
            "默认锚点 center 或 upper，禁止落入底部字幕带",
        ),
        anti_patterns=(
            "把整句旁白原样搬上来（那是字幕的职责）",
            "同时出现 3 个以上强调词，观众不知道看哪个",
            "持续抖动/无限循环，抢走旁白注意力",
        ),
    ),
    TextAnimSpec(
        id="reveal_line",
        name="整句揭示",
        intent="用一次干净的揭示把结论句立起来（常用于章节开头）",
        motion="遮罩/位移揭示（0.4–0.6s ease-out），词组错峰 0.06s，末段静止",
        constraints=(
            "单行 ≤14 字；超过就摘要或拆两行",
            "文本来自旁白，允许删减不允许新增事实",
            "reveal 完成后至少静止 0.6s 供阅读",
        ),
        anti_patterns=("逐字打字机（信息密度太低）", "长句三行以上压住画面"),
    ),
    TextAnimSpec(
        id="quote_block",
        name="引语块",
        intent="呈现一句被引用的话（评论区/官方说明/当事人原话）",
        motion="卡片从下方 12–20px 浮入 + 引号先落定（0.3s），随后正文淡入",
        constraints=(
            "必须带引号符号或明确的『引自』标识",
            "卡片背景只允许半透明（alpha 0.25–0.55）",
            "≤2 行，单行 ≤18 字",
        ),
        anti_patterns=("伪造引语或改写原话", "不透明大色块遮住实拍"),
    ),
    TextAnimSpec(
        id="label_callout",
        name="标注条",
        intent="给画面上的某个对象贴一个短标签（时间、地点、数字来源）",
        motion="从锚点侧滑入 8–12px + 描边高亮扫过一次",
        constraints=(
            "≤8 字；必须指向画面中的具体对象或区域",
            "不得遮挡人脸与字幕",
            "标签与指向物之间允许一条 1px 指示线",
        ),
        anti_patterns=("悬空标签（观众不知道标的是什么）", "标签数量 >2"),
    ),
    TextAnimSpec(
        id="number_punch",
        name="数字冲屏",
        intent="让一个关键数字（75 分钟 / 4 小时 / 3 个版本）成为画面主角",
        motion="数字从 0 快速计数到目标值（0.6–0.9s），单位与说明随后淡入",
        constraints=(
            "数字必须在旁白里出现过，禁止推算/编造",
            "主数字字号 ≥ 画面高度的 18%",
            "数字单位必须紧邻出现",
        ),
        anti_patterns=("无单位裸数字", "同时冲屏 2 个数字"),
    ),
    TextAnimSpec(
        id="title_stack",
        name="标题堆叠",
        intent="两到三层信息同时立住：主标题 + 副标题 + 一条细线",
        motion="主标题先落（0.35s），副标题错峰 0.15s 后淡入，细线最后扫过",
        constraints=(
            "最多 3 层；主标题 ≤12 字，副标题 ≤18 字",
            "层间距 ≥ 12px，整体高度 ≤ 画面 45%",
            "副标题颜色饱和度低于主标题",
        ),
        anti_patterns=("三层同样字号（没有层级）", "标题堆满整屏"),
    ),
)

_BY_ID = {spec.id: spec for spec in TEXT_ANIM_SPECS}


def get_text_spec(type_id: str) -> TextAnimSpec | None:
    return _BY_ID.get(str(type_id or ""))


def known_text_ids() -> tuple[str, ...]:
    return tuple(_BY_ID)


def _read_prompt(type_id: str) -> str:
    path = PROMPT_DIR / f"{type_id}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_text_prompt(type_id: str) -> dict[str, str]:
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
