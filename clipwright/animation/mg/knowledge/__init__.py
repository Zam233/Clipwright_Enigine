"""MG 动效配方库加载与选取（P2-1）。

`motion_recipes.yaml` 是本项目自行撰写的**结构提炼**（非上游原文），每条登记来源与许可。
本模块只负责：加载 → 按镜头类型/关键词选取 → 渲染成提示词片段。

设计约束：
- 加载失败/文件缺失一律**静默降级为空**（不得阻断 MG 生成）；
- 选取是纯启发式（零 LLM 调用），确定性：同样输入必然同样输出；
- 片段长度有上限，避免把上下文挤爆。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from clipwright.config import logger

_PACK_PATH = Path(__file__).resolve().parent / "motion_recipes.yaml"
_ALLOWED_LICENSES = {"Apache-2.0", "MIT"}
_MAX_SECTION_CHARS = 3200


@lru_cache(maxsize=1)
def load_pack() -> dict[str, Any]:
    """加载配方库（进程级缓存）。失败返回空结构。"""
    try:
        import yaml

        data = yaml.safe_load(_PACK_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("配方库根节点不是映射")
        recipes = [r for r in (data.get("recipes") or []) if isinstance(r, dict) and r.get("id")]
        data["recipes"] = recipes
        return data
    except Exception as exc:  # pragma: no cover - 资源缺失/损坏
        logger.warning("MG 配方库加载失败（跳过知识注入）: %s", exc)
        return {"version": 0, "recipes": [], "defaults": []}


def all_recipes() -> list[dict[str, Any]]:
    return list(load_pack().get("recipes") or [])


def get_recipe(recipe_id: str) -> dict[str, Any] | None:
    for r in all_recipes():
        if r.get("id") == recipe_id:
            return r
    return None


def select_recipes(
    shot_type: str = "",
    text: str = "",
    limit: int = 2,
) -> list[dict[str, Any]]:
    """按镜头类型 + 文本关键词选取配方（确定性）。

    打分：shot_type 命中 +3；每个关键词命中 +2；标题命中 +2。
    无任何命中时回退到 `defaults` 指定的配方，仍无则取库中前 N 条。
    """
    recipes = all_recipes()
    if not recipes or limit <= 0:
        return []
    haystack = f"{shot_type or ''} {text or ''}".lower()
    shot = (shot_type or "").strip().lower()

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, r in enumerate(recipes):
        score = 0
        st = [str(s).lower() for s in (r.get("shot_types") or [])]
        if shot and shot in st:
            score += 3
        for kw in r.get("keywords") or []:
            if str(kw).lower() and str(kw).lower() in haystack:
                score += 2
        title = str(r.get("title") or "").lower()
        if title and title in haystack:
            score += 2
        if score > 0:
            scored.append((score, -idx, r))

    if not scored:
        defaults = [get_recipe(str(d)) for d in (load_pack().get("defaults") or [])]
        picked = [r for r in defaults if r]
        return (picked or recipes)[:limit]

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [r for _, _, r in scored[:limit]]


def _render_one(recipe: dict[str, Any]) -> str:
    st = recipe.get("structure") or {}
    beats = st.get("beats") or []
    lines = [f"### 配方：{recipe.get('title', recipe.get('id'))}（{recipe.get('id')}）"]
    if recipe.get("intent"):
        lines.append(f"- 意图：{recipe['intent']}")
    dur = st.get("duration_sec")
    if isinstance(dur, (list, tuple)) and len(dur) == 2:
        lines.append(f"- 建议时长：{dur[0]}-{dur[1]} 秒")
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        at = beat.get("at", "")
        do = beat.get("do", "")
        if do:
            lines.append(f"- {at}：{do}" if at != "" else f"- {do}")
    if st.get("layout"):
        lines.append(f"- 构图：{st['layout']}")
    if st.get("easing"):
        lines.append(f"- 缓动：{'、'.join(str(e) for e in st['easing'])}")
    if st.get("avoid"):
        lines.append(f"- 禁忌：{'、'.join(str(a) for a in st['avoid'])}")
    return "\n".join(lines)


def render_section(recipes: list[dict[str, Any]]) -> str:
    """渲染为提示词片段（超长则截断并注明）。"""
    if not recipes:
        return ""
    body = "\n\n".join(_render_one(r) for r in recipes)
    header = (
        "## 动效配方参考（结构骨架，非强制）\n"
        "以下是本项目沉淀的动效配方（来源与方法论已在仓库 `animation/mg/knowledge/` 登记）。\n"
        "当镜头语义与配方匹配时，**优先沿用其 beat 结构与节奏**；不匹配则按设计原则自由发挥。\n"
        "配方只约束「何时发生什么」，配色/字体/文案仍以 Persona 与需求为准。\n"
    )
    section = header + "\n" + body
    if len(section) > _MAX_SECTION_CHARS:
        section = section[:_MAX_SECTION_CHARS] + "\n（配方片段已截断）"
    return section
