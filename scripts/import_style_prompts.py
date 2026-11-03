# -*- coding: utf-8 -*-
"""把 code-animation-prompts 的风格要点补进 38 个动画类型的提示词。

来源：https://github.com/XiaoyeCodes/code-animation-prompts（341 种代码动画风格）
做法：按语义为每个类型映射 1–3 个风格编号，抽取该风格的
「长什么样 / 适合 / 关键词」与提示词首句（≤200 字，去掉代码块），
写成 `## Style reference` 段追加到对应类型 md（幂等：已存在则替换该段）。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

LIB = Path(r"J:\Clipwright\_cache\cap\skills\code-animation-prompts\assets\library\风格提示词")
TYPES = Path(r"J:\Clipwright\clipwright\animation\prompts\types")

#: 类型 → 风格编号（按语义映射；同一风格可复用于多个类型）
MAP: dict[str, list[str]] = {
    "bar_chart": ["F1"],
    "line_chart": ["F1"],
    "heatmap": ["F1"],
    "datatable": ["F1"],
    "mg_data_bars": ["F1"],
    "mg_data_counter": ["F1"],
    "pie_chart": ["F1"],
    "radar": ["F1"],
    "sankey": ["F2"],
    "flow_chart": ["F2"],
    "diagram": ["F2"],
    "hierarchy": ["F2"],
    "orgchart": ["F2"],
    "tree": ["F2"],
    "venn": ["F2"],
    "venn3": ["F2"],
    "mindmap": ["F2"],
    "mg_mindmap": ["F2"],
    "mg_flow_arrows": ["F2"],
    "comparison": ["F3"],
    "mg_comparison_split": ["F3"],
    "causation": ["F2"],
    "sequence": ["F2"],
    "sequence_diagram": ["F2"],
    "timeline": ["M20"],
    "gantt": ["M20"],
    "mg_timeline_progress": ["M20"],
    "quote": ["M3"],
    "mg_quote_card": ["M3"],
    "mg_title_reveal": ["M6"],
    "mg_logo_intro": ["M37"],
    "mg_countdown": ["M7"],
    "mg_counter_up": ["M7"],
    "mg_deck_deal_flyin": ["E1"],
    "mg_row_embed": ["E1"],
    "mg_spotlight_hero_card": ["E1"],
    "compcard": ["H1"],
    "concept": ["M10"],
    "codeblock": ["A2"],
}


def find_style(code: str) -> Path | None:
    hits = list(LIB.rglob(f"{code}_*.md"))
    return hits[0] if hits else None


def parse_style(path: Path) -> dict[str, str]:
    body = io.open(path, encoding="utf-8").read()
    out = {"name": path.stem.split("_", 1)[-1]}
    for line in body.splitlines():
        m = re.match(r"^- (长什么样|适合|关键词)：(.+)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    # 提示词首句：跳过代码块与标题
    in_code = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line or line.startswith(("#", "-", "|")):
            continue
        if line.startswith("用代码") or "风格提示词" in line:
            sentence = re.split(r"[。\n]", line)[0]
            if len(sentence) > 6:
                out["prompt"] = sentence[:200]
                break
    return out


def render_block(codes: list[str]) -> str:
    lines = ["## Style reference", ""]
    lines.append(
        "以下视觉语言来自公开的代码动画风格库（341 种，按语义匹配本类型），"
        "只借用**构图/配色/节奏/入场方式**，内容与事实必须仍来自本片旁白："
    )
    for code in codes:
        p = find_style(code)
        if p is None:
            continue
        s = parse_style(p)
        bits = [f"**{code} {s['name']}**"]
        if s.get("长什么样"):
            bits.append(f"画面：{s['长什么样']}")
        if s.get("关键词"):
            bits.append(f"关键词：{s['关键词']}")
        if s.get("prompt"):
            bits.append(f"参考做法：{s['prompt']}")
        lines.append("- " + "；".join(bits))
    lines.append("")
    lines.append("落笔要求：借用其动效语言，但元素内容必须能在本段旁白里找到依据；"
                 "不引入参考图里的示例数据与文案。")
    return "\n".join(lines)


def main() -> int:
    changed = 0
    for tid, codes in MAP.items():
        path = TYPES / f"{tid}.md"
        if not path.exists():
            print("skip (missing md):", tid)
            continue
        body = io.open(path, encoding="utf-8").read()
        block = render_block(codes)
        if "## Style reference" in body:
            body = re.sub(r"## Style reference.*\Z", block + "\n", body, flags=re.S)
        else:
            body = body.rstrip() + "\n\n" + block + "\n"
        io.open(path, "w", encoding="utf-8").write(body)
        changed += 1
    print("updated:", changed, "类型提示词")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
