"""Engine prompt-skill registry; type packs are plugin contributions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptSkill:
    id: str
    when: tuple[str, ...]
    body: str
    kind: str = "builtin"  # builtin|plugin
    priority: int = 0


SKILLS: dict[str, PromptSkill] = {}
_PLUGIN_SKILLS: dict[str, PromptSkill] = {}


def register_prompt_skill(skill: PromptSkill) -> None:
    """Plugin-facing registration; builtins remain protected by id."""
    if skill.id in SKILLS and SKILLS[skill.id].kind == "builtin":
        raise ValueError(f"prompt skill {skill.id} 已存在（内置不可覆盖）")
    SKILLS[skill.id] = skill


BUILTIN_SKILLS: dict[str, PromptSkill] = {
    "core-layout": PromptSkill(
        "core-layout", ("*",),
        "坐标：text/shape/image 左上角，line 中心，bg 忽略；bbox=[x,y,x+w,y+h]；elements 顺序=层级。",
    ),
    "text-box": PromptSkill(
        "text-box", ("text", "title", "quote"),
        "text：不写 width 单行；写 width 才换行；text_align 仅在 width 下生效；text.height 可选；文字无 border 默认。",
    ),
    "text-arrow": PromptSkill(
        "text-arrow", ("flow", "process"),
        "流程箭头优先文本 →；用 line 必须另加箭头头；n 个节点需要 n-1 个箭头；不得用无头 line。",
    ),
    "multi-line": PromptSkill(
        "multi-line", ("quote", "title", "text"),
        "多行：每行独立 text，同 x 不同 y；y[i+1]=y[i]+font_size*line_height+24；禁止共用 y/bbox。",
    ),
    "card-labels": PromptSkill(
        "card-labels", ("comparison", "flow", "data"),
        "卡片标签：框不宽于卡片；同宽必须 text_align=center；否则 x=card.x+(card.width-text.width)/2；禁止贴左。",
    ),
    "duplicate-text": PromptSkill(
        "duplicate-text", ("*",),
        "同一画面同一句/关键词只能出现一个 text；重复必须删或拆成不重叠两行。",
    ),
    "image": PromptSkill(
        "image", ("image", "asset"),
        "image：src+x/y/w/h；object-fit=contain；src 仅 data:image 或媒体白名单；不写 alt/object_fit。",
    ),
    "unsupported": PromptSkill(
        "unsupported", ("*",),
        "不支持 z_index/object_fit/alt/gradient_angle/stroke_dasharray；替代：顺序/contain/background 渐变/实线。",
    ),
}
SKILLS.update(BUILTIN_SKILLS)


def select_skills(*, category: str = "", shot_type: str = "", needs: set[str] | None = None) -> list[PromptSkill]:
    tags = {"*", category, shot_type, *(needs or set())}
    out = [s for s in SKILLS.values() if any(t in tags for t in s.when)]
    return sorted(out, key=lambda s: (-s.priority, s.id))


def compose_skills(*, category: str = "", shot_type: str = "", needs: set[str] | None = None) -> str:
    return "\n".join(f"## {s.id}\n{s.body}" for s in select_skills(category=category, shot_type=shot_type, needs=needs))
